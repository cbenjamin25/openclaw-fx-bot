"""Strategy A: consecutive-signal mean reversion.

The honest implementation of the mechanic Galileo FX dresses up —
"consecutive signal detection for reversals" — built per the plan:

    Condition:  RSI(14) beyond threshold
                AND price outside Bollinger(20, 2σ)
                AND N consecutive bars confirming the stretch
    Entry:      counter-trend at next bar's open (engine enforces)
    Stop:       ATR-scaled (1R)
    Target:     1.5R default
    Session:    London/NY overlap only (12:00–17:00 UTC) — tightest
                spreads, cleanest mean reversion
    Direction:  fade the stretch (price stretched DOWN → go LONG)

Parameters are exposed for the walk-forward harness to tune. Nothing
here is assumed to work: this is a CANDIDATE awaiting judgment, and
the default parameters are textbook values, not fitted ones.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from src.strategy.base import LONG, SHORT, Signal

LOOKBACK = 220  # bounded history slice; > max(bb, rsi, atr) with margin


class MeanReversion:
    name = "mr_consecutive"

    def __init__(
        self,
        rsi_period: int = 14,
        rsi_low: float = 30.0,
        rsi_high: float = 70.0,
        bb_period: int = 20,
        bb_std: float = 2.0,
        confirm_bars: int = 2,
        atr_period: int = 14,
        atr_stop_mult: float = 1.5,
        target_r: float = 1.5,
        session_start_utc: int = 12,
        session_end_utc: int = 17,
    ):
        self.rsi_period = rsi_period
        self.rsi_low = rsi_low
        self.rsi_high = rsi_high
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.confirm_bars = confirm_bars
        self.atr_period = atr_period
        self.atr_stop_mult = atr_stop_mult
        self.target_r = target_r
        self.session_start_utc = session_start_utc
        self.session_end_utc = session_end_utc

    # ── indicators (computed on the bounded tail only) ──

    @staticmethod
    def _rsi(close: pd.Series, period: int) -> float:
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(period).mean()
        loss = (-delta.clip(upper=0)).rolling(period).mean()
        rs = gain / loss.replace(0, 1e-12)
        rsi = 100 - 100 / (1 + rs)
        return float(rsi.iloc[-1])

    def _atr(self, h: pd.DataFrame) -> float:
        tr = pd.concat(
            [
                h["high"] - h["low"],
                (h["high"] - h["close"].shift()).abs(),
                (h["low"] - h["close"].shift()).abs(),
            ],
            axis=1,
        ).max(axis=1)
        return float(tr.iloc[-self.atr_period :].mean())

    # ── the decision ──

    def on_bar(self, history: pd.DataFrame) -> Optional[Signal]:
        if len(history) < max(self.bb_period, self.rsi_period, self.atr_period) + self.confirm_bars + 2:
            return None
        h = history.iloc[-LOOKBACK:]

        # Session filter: only decide during the overlap window.
        hour = h.index[-1].hour
        if not (self.session_start_utc <= hour < self.session_end_utc):
            return None

        close = h["close"]
        mid = close.rolling(self.bb_period).mean()
        sd = close.rolling(self.bb_period).std()
        upper = mid + self.bb_std * sd
        lower = mid - self.bb_std * sd

        rsi = self._rsi(close, self.rsi_period)
        atr = self._atr(h)
        if not atr or atr <= 0:
            return None

        # N consecutive bars closing beyond the band = confirmed stretch.
        last_closes = close.iloc[-self.confirm_bars :]
        below = (last_closes < lower.iloc[-self.confirm_bars :]).all()
        above = (last_closes > upper.iloc[-self.confirm_bars :]).all()

        stop = self.atr_stop_mult * atr

        if below and rsi <= self.rsi_low:
            return Signal(direction=LONG, stop_distance=stop, target_r=self.target_r)
        if above and rsi >= self.rsi_high:
            return Signal(direction=SHORT, stop_distance=stop, target_r=self.target_r)
        return None


def make(**kwargs) -> MeanReversion:
    return MeanReversion(**kwargs)
