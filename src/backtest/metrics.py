"""Metrics: turn a trade list into the numbers that matter.

Reported in R multiples (risk units). The %-equity view assumes a
nominal 1% risk per trade purely for drawdown intuition — live sizing
policy is the risk engine's job, not the backtester's.
"""

from __future__ import annotations

import math

import pandas as pd

from src.backtest.engine import BacktestResult

RISK_PER_TRADE = 0.01  # nominal, for the % equity view only


def compute_metrics(result: BacktestResult) -> dict:
    df = result.trades_df
    if df.empty:
        return {"trade_count": 0, "note": "no trades generated"}

    r = df["r_multiple"].astype(float)
    wins = r[r > 0]
    losses = r[r <= 0]

    expectancy = r.mean()
    win_rate = len(wins) / len(r)
    gross_win = wins.sum()
    gross_loss = -losses.sum()
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else math.inf

    # Equity curve at 1% risk per trade, compounded.
    equity = (1 + r * RISK_PER_TRADE).cumprod()
    peak = equity.cummax()
    drawdown = (equity / peak - 1).min()

    # Per-trade Sharpe (no annualization games on irregular trade spacing).
    sharpe_per_trade = r.mean() / r.std() if r.std() > 0 else math.inf

    monthly = (
        df.assign(
            month=pd.to_datetime(df["exit_ts"])
            .dt.tz_localize(None)
            .dt.to_period("M")
        )
        .groupby("month")["r_multiple"]
        .sum()
    )

    return {
        "trade_count": int(len(r)),
        "expectancy_r": round(float(expectancy), 4),
        "win_rate": round(float(win_rate), 4),
        "profit_factor": round(float(profit_factor), 3),
        "gross_win_r": round(float(gross_win), 2),
        "gross_loss_r": round(float(gross_loss), 2),
        "max_drawdown_pct_at_1pct_risk": round(float(drawdown) * 100, 2),
        "sharpe_per_trade": round(float(sharpe_per_trade), 3),
        "total_r": round(float(r.sum()), 2),
        "avg_win_r": round(float(wins.mean()), 3) if len(wins) else 0.0,
        "avg_loss_r": round(float(losses.mean()), 3) if len(losses) else 0.0,
        "exit_reasons": df["exit_reason"].value_counts().to_dict(),
        "best_month_r": round(float(monthly.max()), 2),
        "worst_month_r": round(float(monthly.min()), 2),
        "months_traded": int(len(monthly)),
    }


def format_report(result: BacktestResult, metrics: dict, mode: str = "IN-SAMPLE") -> str:
    lines = [
        "=" * 62,
        f"BACKTEST  {result.instrument} {result.granularity}  "
        f"strategy={result.strategy_name}  cfg={result.config_hash}",
        "=" * 62,
    ]
    if metrics.get("trade_count", 0) == 0:
        lines.append("No trades generated.")
        return "\n".join(lines)

    verdict = (
        "POSITIVE expectancy after costs"
        if metrics["expectancy_r"] > 0
        else "NEGATIVE expectancy after costs"
    )
    lines += [
        f"Trades: {metrics['trade_count']}   "
        f"Win rate: {metrics['win_rate']:.1%}   "
        f"Profit factor: {metrics['profit_factor']}",
        f"Expectancy: {metrics['expectancy_r']:+.4f} R/trade   "
        f"Total: {metrics['total_r']:+.2f} R   → {verdict}",
        f"Avg win: {metrics['avg_win_r']:+.3f} R   "
        f"Avg loss: {metrics['avg_loss_r']:+.3f} R",
        f"Max drawdown (at 1% risk/trade): "
        f"{metrics['max_drawdown_pct_at_1pct_risk']}%",
        f"Sharpe (per-trade): {metrics['sharpe_per_trade']}",
        f"Months: {metrics['months_traded']}   "
        f"best {metrics['best_month_r']:+.2f} R / "
        f"worst {metrics['worst_month_r']:+.2f} R",
        f"Exits: {metrics['exit_reasons']}",
        "-" * 62,
        f"GATES ({mode}) — plan targets vs this run:",
    ]
    from src.backtest.gates import evaluate_gates, gates_summary

    for name, ok, detail in evaluate_gates(metrics):
        mark = "PASS" if ok else "FAIL"
        lines.append(f"  [{mark}] {name:<28} {detail}")
    passed, total = gates_summary(metrics)
    overall = "ALL GATES PASSED" if passed == total else f"{passed}/{total} gates passed — NOT ACCEPTED"
    lines += [
        f"VERDICT: {overall}",
    ]
    if mode == "IN-SAMPLE":
        lines.append(
            "NOTE: in-sample run. Gates formally require WALK-FORWARD "
            "out-of-sample results; passing here is necessary, never sufficient."
        )
    lines.append("=" * 62)
    return "\n".join(lines)
