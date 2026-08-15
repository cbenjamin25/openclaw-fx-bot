"""Acceptance gates — the plan's targets, as code.

Every backtest report prints these next to the actuals so a result is
never read without its yardstick. Gates come from the build plan:

    G1  Expectancy after costs      > 0 R/trade      (the disqualifier)
    G2  Profit factor               >= 1.30
    G3  Trade count                 >= 200           (statistical floor)
    G4  Max drawdown @1% risk       > -15%
    G5  Beats dummy baseline        expectancy > dummy's on same data

IMPORTANT SCOPE NOTE: these gates formally apply to WALK-FORWARD,
OUT-OF-SAMPLE results. A full-history in-sample run passing them is
necessary but never sufficient — the report labels which mode it is.
"""

from __future__ import annotations

# The dummy control's full-history in-sample expectancy on EUR_USD H1
# (cfg=85d78b3c8700, 2026-08-14). Refresh if the control is rerun on
# materially different data.
DUMMY_BASELINE_EXPECTANCY_R = -0.0899

GATES = [
    ("G1 expectancy_r > 0", lambda m: m.get("expectancy_r", -9) > 0,
     "expectancy_r", "> 0"),
    ("G2 profit_factor >= 1.30", lambda m: m.get("profit_factor", 0) >= 1.30,
     "profit_factor", ">= 1.30"),
    ("G3 trade_count >= 200", lambda m: m.get("trade_count", 0) >= 200,
     "trade_count", ">= 200"),
    ("G4 max_dd > -15%", lambda m: m.get("max_drawdown_pct_at_1pct_risk", -99) > -15.0,
     "max_drawdown_pct_at_1pct_risk", "> -15.0"),
    ("G5 beats dummy baseline",
     lambda m: m.get("expectancy_r", -9) > DUMMY_BASELINE_EXPECTANCY_R,
     "expectancy_r", f"> {DUMMY_BASELINE_EXPECTANCY_R} (dummy)"),
]


def evaluate_gates(metrics: dict) -> list[tuple[str, bool, str]]:
    """Return [(gate_name, passed, 'actual vs target')] for the report."""
    rows = []
    for name, fn, key, target in GATES:
        actual = metrics.get(key, "n/a")
        rows.append((name, bool(fn(metrics)), f"actual {actual}  target {target}"))
    return rows


def gates_summary(metrics: dict) -> tuple[int, int]:
    rows = evaluate_gates(metrics)
    return sum(1 for _, ok, _ in rows if ok), len(rows)
