"""Backtest CLI.

Usage (on the instance, venv active):
    python -m src.backtest.run EUR_USD H1 --strategy dummy

Every run is logged to a `backtest_runs` table in the same SQLite DB —
config hash + metrics JSON — so results are reproducible and auditable.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from src.backtest.costs import CostModel
from src.backtest.engine import run_backtest
from src.backtest.metrics import compute_metrics, format_report
from src.data.store import conn, load_df

STRATEGIES = {
    "dummy": "src.strategy.dummy",
}

RUNS_SCHEMA = """
CREATE TABLE IF NOT EXISTS backtest_runs (
    run_ts      TEXT NOT NULL,
    instrument  TEXT NOT NULL,
    granularity TEXT NOT NULL,
    strategy    TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    metrics     TEXT NOT NULL
);
"""


def load_strategy(name: str, params: dict):
    import importlib

    if name not in STRATEGIES:
        raise SystemExit(
            f"unknown strategy {name!r}; available: {sorted(STRATEGIES)}"
        )
    module = importlib.import_module(STRATEGIES[name])
    return module.make(**params)


def log_run(result, metrics: dict) -> None:
    with conn() as c:
        c.executescript(RUNS_SCHEMA)
        c.execute(
            "INSERT INTO backtest_runs VALUES (?,?,?,?,?,?)",
            (
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                result.instrument,
                result.granularity,
                result.strategy_name,
                result.config_hash,
                json.dumps(metrics),
            ),
        )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("instrument")
    p.add_argument("granularity")
    p.add_argument("--strategy", default="dummy")
    p.add_argument(
        "--params",
        default="{}",
        help='strategy params as JSON, e.g. \'{"fast": 10, "slow": 40}\'',
    )
    args = p.parse_args()

    df = load_df(args.instrument, args.granularity)
    if df.empty:
        raise SystemExit(
            f"no candles for {args.instrument} {args.granularity}; "
            "run src.data.fetch first"
        )
    print(
        f"loaded {len(df)} candles  {df.index[0]} → {df.index[-1]}"
    )

    strategy = load_strategy(args.strategy, json.loads(args.params))
    costs = CostModel.for_instrument(args.instrument)
    print(
        f"cost model: spread {costs.spread_pips} pips + "
        f"slippage {costs.slippage_pips} pips/side "
        f"(round trip ≈ {costs.round_trip_pips} pips)"
    )

    result = run_backtest(
        df, strategy, costs, args.instrument, args.granularity
    )
    metrics = compute_metrics(result)
    print(format_report(result, metrics))
    log_run(result, metrics)
    print(f"run logged (cfg={result.config_hash})")


if __name__ == "__main__":
    main()
