"""Portfolio aggregation — grading a multi-pair book against the gates.

The trend family's edge (if real) is a portfolio property: single H4
pairs can't reach G3's 200-trade floor, but the combined out-of-sample
book can. This tool rebuilds the combined book HONESTLY:

- Pulls each pair's walk-forward OOS per-window audit from
  backtest_runs (the logged record, not a rerun — no second bite).
- Concatenates OOS window results in time order per pair.
- Grades the combined book against all five gates.
- Reports per-pair contribution and the monthly R distribution of the
  blend, so concentration (one pair or one year carrying everything)
  is visible, not hidden.

Honesty limits, stated up front:
- The audit stores per-window aggregates (trade count, total R), not
  individual trades, so blended win-rate/PF are reconstructed from
  window-level data where trade-level data isn't logged. Expectancy,
  trade counts, and window R distributions are exact.
- Correlation between simultaneously-open positions is NOT modeled
  here (the risk engine's exposure caps handle that live). This grades
  the signal book, not the execution book.

Usage:
    python -m src.backtest.portfolio --strategy trend_wf USD_JPY USD_CAD USD_CHF
"""

from __future__ import annotations

import argparse
import json
import math

import pandas as pd

from src.backtest.gates import evaluate_gates, gates_summary
from src.data.store import conn


def load_latest_audit(instrument: str, strategy_name: str) -> dict | None:
    with conn() as c:
        row = c.execute(
            "SELECT metrics FROM backtest_runs "
            "WHERE instrument=? AND strategy=? "
            "ORDER BY run_ts DESC LIMIT 1",
            (instrument, strategy_name),
        ).fetchone()
    if not row:
        return None
    payload = json.loads(row[0])
    # walk-forward runs log {"metrics": ..., "audit": [...]}
    return payload if "audit" in payload else None


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("instruments", nargs="+")
    p.add_argument("--strategy", default="trend_wf")
    args = p.parse_args()

    rows = []
    per_pair = []
    for inst in args.instruments:
        payload = load_latest_audit(inst, args.strategy)
        if payload is None:
            raise SystemExit(
                f"no logged walk-forward run for {inst} / {args.strategy}"
            )
        m = payload["metrics"]
        per_pair.append((inst, m))
        for w in payload["audit"]:
            if "oos_trades" in w:
                rows.append(
                    {
                        "instrument": inst,
                        "window": w["window"],
                        "trades": w["oos_trades"],
                        "r": w["oos_r"],
                    }
                )

    df = pd.DataFrame(rows)

    # ── EXACT MODE: if per-trade records exist, merge all trades on the
    # calendar and compute every metric from the real trade stream. ──
    trade_rows = []
    for inst in args.instruments:
        payload = load_latest_audit(inst, args.strategy)
        for w in payload["audit"]:
            for t in w.get("trades", []):
                trade_rows.append(
                    {"instrument": inst, "entry_ts": t["entry_ts"], "r": t["r"]}
                )

    exact = len(trade_rows) > 0
    if exact:
        tdf = pd.DataFrame(trade_rows)
        tdf["entry_ts"] = pd.to_datetime(tdf["entry_ts"])
        tdf = tdf.sort_values("entry_ts").reset_index(drop=True)
        r = tdf["r"].astype(float)
        wins, losses = r[r > 0], r[r <= 0]
        gross_w, gross_l = float(wins.sum()), float(-losses.sum())
        equity = (1 + r * 0.01).cumprod()
        dd = float((equity / equity.cummax() - 1).min()) * 100
        blend = {
            "trade_count": int(len(r)),
            "expectancy_r": round(float(r.mean()), 4),
            "profit_factor": round(gross_w / gross_l, 3) if gross_l > 0 else float("inf"),
            "win_rate": round(len(wins) / len(r), 4),
            "max_drawdown_pct_at_1pct_risk": round(dd, 2),
        }
        total_trades, total_r = blend["trade_count"], round(float(r.sum()), 2)
        expectancy = blend["expectancy_r"]
    else:
        total_trades = int(df["trades"].sum())
        total_r = float(df["r"].sum())
        expectancy = total_r / total_trades if total_trades else 0.0

        wsum = sum(m["trade_count"] for _, m in per_pair)
        blend = {
            "trade_count": total_trades,
            "expectancy_r": round(expectancy, 4),
            "profit_factor": round(
                sum(m["profit_factor"] * m["trade_count"] for _, m in per_pair) / wsum, 3
            ),
            "win_rate": round(
                sum(m["win_rate"] * m["trade_count"] for _, m in per_pair) / wsum, 4
            ),
        }
        cum_r = df.sort_values(["window", "instrument"])["r"].cumsum()
        peak = cum_r.cummax()
        max_dd_r = float((cum_r - peak).min())
        blend["max_drawdown_pct_at_1pct_risk"] = round(max_dd_r * 1.0, 2)

    print("=" * 62)
    print(f"PORTFOLIO  {args.strategy}  [{', '.join(args.instruments)}]")
    print("=" * 62)
    for inst, m in per_pair:
        print(
            f"  {inst:<8} {m['trade_count']:>4} trades  "
            f"exp {m['expectancy_r']:+.4f} R  PF {m['profit_factor']:<6} "
            f"DD {m['max_drawdown_pct_at_1pct_risk']}%"
        )
    print("-" * 62)
    print(
        f"BLEND: {total_trades} trades  exp {expectancy:+.4f} R  "
        f"total {total_r:+.2f} R"
    )
    mode_note = "EXACT trade-level replay" if exact else "trade-weighted reconstruction"
    print(
        f"       PF {blend['profit_factor']} ({mode_note})  "
        f"DD {blend['max_drawdown_pct_at_1pct_risk']}%  "
        f"win rate {blend['win_rate']:.1%}"
    )

    # Monthly-ish concentration view: window R distribution
    top = df.nlargest(3, "r")[["instrument", "window", "r"]]
    print(f"       top-3 windows: "
          + ", ".join(f"{r.instrument} w{r.window} {r.r:+.1f}R" for r in top.itertuples()))
    share = top["r"].sum() / total_r if total_r > 0 else math.nan
    print(f"       top-3 window share of total R: {share:.0%}")
    print("-" * 62)
    print("GATES (PORTFOLIO WALK-FORWARD OOS) — plan targets vs blend:")
    for name, ok, detail in evaluate_gates(blend):
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:<28} {detail}")
    passed, total_g = gates_summary(blend)
    print(
        f"VERDICT: {'ALL GATES PASSED' if passed == total_g else f'{passed}/{total_g} gates passed — NOT ACCEPTED'}"
    )
    if exact:
        print("MODE: exact — all metrics computed from the merged individual "
              "trade stream in calendar order.")
    else:
        print("MODE: reconstruction — rerun walk-forward with trade logging "
              "for exact metrics.")
    print("=" * 62)


if __name__ == "__main__":
    main()
