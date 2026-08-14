"""Dummy strategy: naive SMA crossover.

Purpose: validate the ENGINE, not to make money. A bare SMA cross on H1
majors is a known roughly-coin-flip signal; after honest costs it should
show mildly negative expectancy. If our backtester reports it as a
winner, the backtester is broken (optimistic) — that outcome would be a
STOP-EVERYTHING bug.

This is the control sample. Real strategy candidates come later and
must beat not only zero but this baseline's cost drag.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from src.strategy.base import LONG, SHORT, Signal


class SmaCross:
    name = "dummy_sma_cross"

    def __init__(self, fast: int = 20, slow: int = 50, atr_period: int = 14):
        self.fast = fast
        self.slow = slow
        self.atr_period = atr_period

    def on_bar(self, history: pd.DataFrame) -> Optional[Signal]:
        if len(history) < self.slow + 2:
            return None
        # Bounded lookback: only the tail is ever needed. Without this,
        # every bar recomputes over ALL history — O(n²) across a long
        # backtest (the 2026-08-14 "why is it still running" lesson).
        history = history.iloc[-max(self.slow + 2, self.atr_period + 2, 200):]

        close = history["close"]
        fast_now = close.iloc[-self.fast :].mean()
        slow_now = close.iloc[-self.slow :].mean()
        fast_prev = close.iloc[-self.fast - 1 : -1].mean()
        slow_prev = close.iloc[-self.slow - 1 : -1].mean()

        # ATR-based stop distance: volatility-scaled 1R.
        tr = pd.concat(
            [
                history["high"] - history["low"],
                (history["high"] - history["close"].shift()).abs(),
                (history["low"] - history["close"].shift()).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr = tr.iloc[-self.atr_period :].mean()
        if not atr or atr <= 0:
            return None

        crossed_up = fast_prev <= slow_prev and fast_now > slow_now
        crossed_down = fast_prev >= slow_prev and fast_now < slow_now

        if crossed_up:
            return Signal(direction=LONG, stop_distance=1.5 * atr, target_r=1.5)
        if crossed_down:
            return Signal(direction=SHORT, stop_distance=1.5 * atr, target_r=1.5)
        return None


def make(**kwargs) -> SmaCross:
    return SmaCross(**kwargs)
