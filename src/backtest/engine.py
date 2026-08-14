"""Backtest engine — bar-by-bar event loop.

Design rules (each one closes a classic backtest lie):

1. NEXT-OPEN EXECUTION. A signal computed on bar N fills at bar N+1's
   open. No trading on the close you could only know after it closed.
2. COSTS ON EVERY FILL. Entries and exits pay half-spread + slippage in
   the adverse direction via CostModel. Stops fill WORSE, targets are
   only credited if price traded through them.
3. WORST-CASE INTRA-BAR RESOLUTION. If a bar's range touches both the
   stop and the target, we assume the STOP hit first. (Without tick
   data, any other assumption is optimistic fiction.)
4. ONE POSITION AT A TIME, fixed 1R risk per trade. Results are
   reported in R multiples — sizing policy stays out of strategy
   evaluation.
5. NO REPAINTING. Strategies receive only completed candles up to the
   decision bar.

Everything is deliberately simple and auditable. Vectorized cleverness
can come later if speed ever matters; correctness never gets traded
for speed in this codebase.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from src.backtest.costs import CostModel
from src.strategy.base import LONG, Signal, Strategy


@dataclass
class Trade:
    entry_ts: pd.Timestamp
    exit_ts: Optional[pd.Timestamp]
    direction: int
    entry_price: float          # actual fill (cost-adjusted)
    stop_price: float
    target_price: float
    r_distance: float           # 1R in price units
    exit_price: Optional[float] = None
    exit_reason: str = ""       # "stop" | "target" | "eod"
    r_multiple: Optional[float] = None


@dataclass
class BacktestResult:
    instrument: str
    granularity: str
    strategy_name: str
    config_hash: str
    trades: list[Trade] = field(default_factory=list)

    @property
    def trades_df(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "entry_ts": t.entry_ts,
                    "exit_ts": t.exit_ts,
                    "direction": t.direction,
                    "r_multiple": t.r_multiple,
                    "exit_reason": t.exit_reason,
                }
                for t in self.trades
            ]
        )


def config_hash(payload: dict) -> str:
    """Stable hash of a run's full configuration — reproducibility tag."""
    blob = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:12]


def run_backtest(
    df: pd.DataFrame,
    strategy: Strategy,
    costs: CostModel,
    instrument: str,
    granularity: str,
    min_history: int = 60,
) -> BacktestResult:
    """Run the event loop over a candle DataFrame (index: ts, cols OHLCV)."""
    cfg = {
        "instrument": instrument,
        "granularity": granularity,
        "strategy": strategy.name,
        "strategy_params": vars(strategy),
        "spread_pips": costs.spread_pips,
        "slippage_pips": costs.slippage_pips,
        "bars": len(df),
        "first": df.index[0],
        "last": df.index[-1],
    }
    result = BacktestResult(
        instrument=instrument,
        granularity=granularity,
        strategy_name=strategy.name,
        config_hash=config_hash(cfg),
    )

    open_trade: Optional[Trade] = None
    pending: Optional[Signal] = None

    for i in range(min_history, len(df)):
        bar = df.iloc[i]
        ts = df.index[i]

        # ── 1. Manage the open position against this bar's range ──
        if open_trade is not None:
            hit_stop = (
                bar["low"] <= open_trade.stop_price
                if open_trade.direction == LONG
                else bar["high"] >= open_trade.stop_price
            )
            hit_target = (
                bar["high"] >= open_trade.target_price
                if open_trade.direction == LONG
                else bar["low"] <= open_trade.target_price
            )
            # Worst-case rule: stop wins ties.
            if hit_stop:
                raw = open_trade.stop_price
                fill = (
                    costs.close_long_fill(raw)
                    if open_trade.direction == LONG
                    else costs.close_short_fill(raw)
                )
                _close(open_trade, ts, fill, "stop")
                result.trades.append(open_trade)
                open_trade = None
            elif hit_target:
                raw = open_trade.target_price
                fill = (
                    costs.close_long_fill(raw)
                    if open_trade.direction == LONG
                    else costs.close_short_fill(raw)
                )
                _close(open_trade, ts, fill, "target")
                result.trades.append(open_trade)
                open_trade = None

        # ── 2. Execute a signal pended from the PREVIOUS bar at this open ──
        if pending is not None and open_trade is None:
            sig = pending
            mid_open = bar["open"]
            if sig.direction == LONG:
                entry = costs.buy_fill(mid_open)
                stop = entry - sig.stop_distance
                target = entry + sig.stop_distance * sig.target_r
            else:
                entry = costs.sell_fill(mid_open)
                stop = entry + sig.stop_distance
                target = entry - sig.stop_distance * sig.target_r
            open_trade = Trade(
                entry_ts=ts,
                exit_ts=None,
                direction=sig.direction,
                entry_price=entry,
                stop_price=stop,
                target_price=target,
                r_distance=sig.stop_distance,
            )
        pending = None

        # ── 3. Ask the strategy for a signal on completed history ──
        if open_trade is None:
            history = df.iloc[: i + 1]
            pending = strategy.on_bar(history)

    # ── End of data: close any open position at last close (with costs) ──
    if open_trade is not None:
        last_ts = df.index[-1]
        raw = df.iloc[-1]["close"]
        fill = (
            costs.close_long_fill(raw)
            if open_trade.direction == LONG
            else costs.close_short_fill(raw)
        )
        _close(open_trade, last_ts, fill, "eod")
        result.trades.append(open_trade)

    return result


def _close(trade: Trade, ts: pd.Timestamp, fill: float, reason: str) -> None:
    trade.exit_ts = ts
    trade.exit_price = fill
    trade.exit_reason = reason
    pnl = (fill - trade.entry_price) * trade.direction
    trade.r_multiple = pnl / trade.r_distance if trade.r_distance else 0.0
