"""Strategy B: trend-continuation pullback (designed for H4).

The mirror bet to Strategy A — trends persist longer than random:

    Trend:    EMA50 > EMA200 (uptrend), and EMA50 rising over the
              last `slope_bars` (the trend must be alive, not stale)
    Pullback: within the last `pullback_lookback` bars, price touched
              or dipped below EMA20 (the discount)
    Trigger:  current close breaks above the previous bar's high
              (the resumption) — mirrored for downtrends
    Stop:     below the pullback low, ATR-padded (defines 1R)
    Exit:     NO fixed target. Trailing stop at `trail_atr_mult` × ATR
              ratchets behind the move. Losers die small; winners run.
    Filter:   skip when ATR is in its lowest `vol_floor_pct` percentile
              of the lookback — dead-vol "trends" are drift.

Expect an ugly win rate (30–40%) with fat winners. For this family the
R distribution is everything; the win rate is noise.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from src.strategy.base import LONG, SHORT, Signal

LOOKBACK = 260  # > EMA200 warmup with margin


class TrendPullback:
    name = "trend_pullback"

    def __init__(
        self,
        ema_fast: int = 50,
        ema_slow: int = 200,
        ema_pull: int = 20,
        slope_bars: int = 5,
        pullback_lookback: int = 5,
        atr_period: int = 14,
        stop_pad_atr: float = 0.5,
        trail_atr_mult: float = 3.0,
        vol_floor_pct: float = 20.0,
    ):
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.ema_pull = ema_pull
        self.slope_bars = slope_bars
        self.pullback_lookback = pullback_lookback
        self.atr_period = atr_period
        self.stop_pad_atr = stop_pad_atr
        self.trail_atr_mult = trail_atr_mult
        self.vol_floor_pct = vol_floor_pct

    def _atr_series(self, h: pd.DataFrame) -> pd.Series:
        tr = pd.concat(
            [
                h["high"] - h["low"],
                (h["high"] - h["close"].shift()).abs(),
                (h["low"] - h["close"].shift()).abs(),
            ],
            axis=1,
        ).max(axis=1)
        return tr.rolling(self.atr_period).mean()

    def on_bar(self, history: pd.DataFrame) -> Optional[Signal]:
        if len(history) < self.ema_slow + self.slope_bars + 2:
            return None
        h = history.iloc[-LOOKBACK:]

        close = h["close"]
        ema_fast = close.ewm(span=self.ema_fast, adjust=False).mean()
        ema_slow = close.ewm(span=self.ema_slow, adjust=False).mean()
        ema_pull = close.ewm(span=self.ema_pull, adjust=False).mean()
        atr_s = self._atr_series(h)
        atr = float(atr_s.iloc[-1])
        if not atr or atr <= 0:
            return None

        # Volatility floor: refuse the graveyard-shift "trends".
        vol_floor = float(atr_s.dropna().quantile(self.vol_floor_pct / 100.0))
        if atr < vol_floor:
            return None

        up = (
            ema_fast.iloc[-1] > ema_slow.iloc[-1]
            and ema_fast.iloc[-1] > ema_fast.iloc[-1 - self.slope_bars]
        )
        down = (
            ema_fast.iloc[-1] < ema_slow.iloc[-1]
            and ema_fast.iloc[-1] < ema_fast.iloc[-1 - self.slope_bars]
        )

        recent = h.iloc[-self.pullback_lookback :]
        prev_bar = h.iloc[-2]
        last = h.iloc[-1]

        if up:
            pulled = (recent["low"] <= ema_pull.iloc[-self.pullback_lookback :]).any()
            resumed = last["close"] > prev_bar["high"]
            if pulled and resumed:
                pull_low = float(recent["low"].min())
                dist = max(
                    float(last["close"]) - pull_low + self.stop_pad_atr * atr,
                    0.5 * atr,
                )
                return Signal(
                    direction=LONG,
                    stop_distance=dist,
                    target_r=None,
                    trail_distance=self.trail_atr_mult * atr,
                )

        if down:
            pulled = (recent["high"] >= ema_pull.iloc[-self.pullback_lookback :]).any()
            resumed = last["close"] < prev_bar["low"]
            if pulled and resumed:
                pull_high = float(recent["high"].max())
                dist = max(
                    pull_high - float(last["close"]) + self.stop_pad_atr * atr,
                    0.5 * atr,
                )
                return Signal(
                    direction=SHORT,
                    stop_distance=dist,
                    target_r=None,
                    trail_distance=self.trail_atr_mult * atr,
                )
        return None


def make(**kwargs) -> TrendPullback:
    return TrendPullback(**kwargs)
