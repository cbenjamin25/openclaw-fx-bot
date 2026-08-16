"""Walk-forward harness — the legitimate way to tune parameters.

The problem it solves: tuning parameters against the full history and
reporting the tuned result is curve fitting — the strategy "learns" the
answers to the test it's graded on. Walk-forward breaks the loop:

    [ train 12mo ][ test 3mo ]
            [ train 12mo ][ test 3mo ]
                    [ train 12mo ][ test 3mo ]   ... rolling forward

For each window: grid-search parameters on the TRAIN slice only, pick
the best by expectancy (with a minimum-trades floor), then run that
frozen choice on the unseen TEST slice. Only the concatenated TEST
trades count. The strategy never gets graded on data it tuned on.

What honest output looks like:
- If train-picked params keep working on test → real, stable edge.
- If every window picks different params and test results are junk →
  the "edge" was noise wearing parameters. Also a valid answer, and a
  cheap one.

Runtime note: this brute-forces GRID × WINDOWS backtests in pure
Python. Expect 15–45 minutes on a t3.small for the default grid. It
prints progress; silence is work.
"""

from __future__ import annotations

import argparse
import itertools
import json
import time
from datetime import datetime, timezone

import pandas as pd

from src.backtest.costs import CostModel
from src.backtest.engine import BacktestResult, run_backtest
from src.backtest.metrics import compute_metrics, format_report
from src.data.store import conn, load_df

# ── Parameter grids ──────────────────────────────────────────────
# Values are hypotheses, not conclusions. Widen boldly; the harness
# is the instrument that keeps bold honest.

GRID_FULL = {
    "rsi_band": [(30.0, 70.0), (25.0, 75.0), (20.0, 80.0)],
    "confirm_bars": [1, 2, 3],
    "target_r": [1.5, 2.0],
    "atr_stop_mult": [1.0, 1.5],
}
GRID_QUICK = {
    "rsi_band": [(30.0, 70.0), (25.0, 75.0)],
    "confirm_bars": [1, 2],
    "target_r": [1.5, 2.0],
    "atr_stop_mult": [1.5],
}

H1_BARS_PER_MONTH = 520  # ~24h * 5d * 4.33wk

MIN_TRAIN_TRADES = 30  # a combo must trade this much on train to qualify


def make_strategy(params: dict):
    from src.strategy.mean_reversion import MeanReversion

    lo, hi = params["rsi_band"]
    return MeanReversion(
        rsi_low=lo,
        rsi_high=hi,
        confirm_bars=params["confirm_bars"],
        target_r=params["target_r"],
        atr_stop_mult=params["atr_stop_mult"],
    )


def grid_combos(grid: dict) -> list[dict]:
    keys = list(grid)
    return [dict(zip(keys, vals)) for vals in itertools.product(*grid.values())]


def score(metrics: dict) -> tuple:
    """Ranking key for train results: expectancy first, PF tiebreak."""
    return (metrics.get("expectancy_r", -9), metrics.get("profit_factor", 0))


def walk_forward(
    df: pd.DataFrame,
    instrument: str,
    granularity: str,
    grid: dict,
    train_months: int = 12,
    test_months: int = 3,
) -> tuple[BacktestResult, list[dict]]:
    costs = CostModel.for_instrument(instrument)
    train_bars = train_months * H1_BARS_PER_MONTH
    test_bars = test_months * H1_BARS_PER_MONTH
    combos = grid_combos(grid)

    oos = BacktestResult(
        instrument=instrument,
        granularity=granularity,
        strategy_name="mr_consecutive_wf",
        config_hash="",
    )
    audit: list[dict] = []
    start = 0
    window_n = 0
    total_windows = max(0, (len(df) - train_bars) // test_bars)
    t0 = time.time()

    while start + train_bars + test_bars <= len(df):
        window_n += 1
        train = df.iloc[start : start + train_bars]
        # Test slice gets a train-sized warmup prefix for indicator
        # context, but ONLY trades whose entry falls inside the true
        # test range are kept.
        test_with_warmup = df.iloc[start : start + train_bars + test_bars]
        test_start_ts = df.index[start + train_bars]

        # ── grid search on TRAIN only ──
        best_params, best_metrics = None, None
        for params in combos:
            res = run_backtest(
                train, make_strategy(params), costs, instrument, granularity
            )
            m = compute_metrics(res)
            if m.get("trade_count", 0) < MIN_TRAIN_TRADES:
                continue
            if best_metrics is None or score(m) > score(best_metrics):
                best_params, best_metrics = params, m

        if best_params is None:
            audit.append(
                {"window": window_n, "note": "no combo met min trades; window skipped"}
            )
            start += test_bars
            continue

        # ── frozen params on unseen TEST ──
        res_test = run_backtest(
            test_with_warmup,
            make_strategy(best_params),
            costs,
            instrument,
            granularity,
        )
        kept = [t for t in res_test.trades if t.entry_ts >= test_start_ts]
        oos.trades.extend(kept)

        audit.append(
            {
                "window": window_n,
                "train_expectancy": best_metrics["expectancy_r"],
                "params": {**best_params, "rsi_band": list(best_params["rsi_band"])},
                "oos_trades": len(kept),
                "oos_r": round(sum(t.r_multiple or 0 for t in kept), 2),
            }
        )
        elapsed = time.time() - t0
        print(
            f"window {window_n}/{total_windows}: "
            f"picked {best_params} (train exp {best_metrics['expectancy_r']:+.3f}) "
            f"→ OOS {len(kept)} trades, {audit[-1]['oos_r']:+.2f} R "
            f"[{elapsed/60:.1f} min]"
        )
        start += test_bars

    from src.backtest.engine import config_hash

    oos.config_hash = config_hash(
        {
            "mode": "walk_forward",
            "instrument": instrument,
            "granularity": granularity,
            "grid": {k: [list(v) if isinstance(v, tuple) else v for v in vs] for k, vs in grid.items()},
            "train_months": train_months,
            "test_months": test_months,
            "bars": len(df),
        }
    )
    return oos, audit


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("instrument")
    p.add_argument("granularity")
    p.add_argument("--grid", choices=["quick", "full"], default="quick")
    p.add_argument("--train-months", type=int, default=12)
    p.add_argument("--test-months", type=int, default=3)
    args = p.parse_args()

    df = load_df(args.instrument, args.granularity)
    if df.empty:
        raise SystemExit("no candles; run src.data.fetch first")
    grid = GRID_QUICK if args.grid == "quick" else GRID_FULL
    n_combos = len(grid_combos(grid))
    print(
        f"walk-forward: {args.instrument} {args.granularity}, "
        f"{len(df)} bars, grid={args.grid} ({n_combos} combos), "
        f"train {args.train_months}mo / test {args.test_months}mo"
    )
    print("this is a long run — progress prints per window; silence is work\n")

    oos, audit = walk_forward(
        df, args.instrument, args.granularity, grid,
        args.train_months, args.test_months,
    )
    metrics = compute_metrics(oos)
    print()
    print(format_report(oos, metrics, mode="WALK-FORWARD OUT-OF-SAMPLE"))

    with conn() as c:
        c.execute(
            "INSERT INTO backtest_runs VALUES (?,?,?,?,?,?)",
            (
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                oos.instrument,
                oos.granularity,
                oos.strategy_name,
                oos.config_hash,
                json.dumps({"metrics": metrics, "audit": audit}),
            ),
        )
    print(f"run + per-window audit logged (cfg={oos.config_hash})")


if __name__ == "__main__":
    main()
