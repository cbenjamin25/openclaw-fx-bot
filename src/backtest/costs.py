"""Cost model — the honesty layer.

Every backtest claim dies or survives here. We store MID candles; real
fills happen at bid/ask plus slippage. This module makes every simulated
fill pay: half-spread on entry, half-spread on exit, plus slippage, all
in the adverse direction.

Numbers are deliberately conservative (slightly worse than Oanda's
typical practice-account spreads). If a strategy only works with
optimistic costs, it doesn't work.
"""

from __future__ import annotations

from dataclasses import dataclass

# Typical retail spreads (pips), padded ~20% pessimistic.
DEFAULT_SPREADS_PIPS = {
    "EUR_USD": 1.2,
    "GBP_USD": 1.6,
    "USD_JPY": 1.3,
    "AUD_USD": 1.4,
    "USD_CAD": 1.7,
    "USD_CHF": 1.6,
    "NZD_USD": 1.9,
    "EUR_GBP": 1.8,
    "EUR_JPY": 1.9,
    "GBP_JPY": 2.5,
}
DEFAULT_SPREAD_PIPS_FALLBACK = 2.5  # unknown instrument → assume worse

DEFAULT_SLIPPAGE_PIPS = 0.4  # per side, always adverse


def pip_size(instrument: str) -> float:
    """0.01 for JPY-quoted pairs, 0.0001 otherwise."""
    return 0.01 if instrument.upper().endswith("_JPY") else 0.0001


@dataclass(frozen=True)
class CostModel:
    instrument: str
    spread_pips: float
    slippage_pips: float

    @classmethod
    def for_instrument(cls, instrument: str) -> "CostModel":
        return cls(
            instrument=instrument,
            spread_pips=DEFAULT_SPREADS_PIPS.get(
                instrument.upper(), DEFAULT_SPREAD_PIPS_FALLBACK
            ),
            slippage_pips=DEFAULT_SLIPPAGE_PIPS,
        )

    @property
    def _pip(self) -> float:
        return pip_size(self.instrument)

    @property
    def entry_cost(self) -> float:
        """Adverse price adjustment on entry: half spread + slippage."""
        return (self.spread_pips / 2 + self.slippage_pips) * self._pip

    @property
    def exit_cost(self) -> float:
        """Adverse price adjustment on exit: half spread + slippage."""
        return (self.spread_pips / 2 + self.slippage_pips) * self._pip

    @property
    def round_trip_pips(self) -> float:
        return self.spread_pips + 2 * self.slippage_pips

    def buy_fill(self, mid_price: float) -> float:
        """Price actually paid when buying at this mid."""
        return mid_price + self.entry_cost

    def sell_fill(self, mid_price: float) -> float:
        """Price actually received when selling at this mid."""
        return mid_price - self.entry_cost

    def close_long_fill(self, mid_price: float) -> float:
        return mid_price - self.exit_cost

    def close_short_fill(self, mid_price: float) -> float:
        return mid_price + self.exit_cost
