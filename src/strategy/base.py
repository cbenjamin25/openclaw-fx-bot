"""Strategy interface.

Strategies emit signals ONLY: direction + stop distance. They never
size positions, never touch orders. (Sizing belongs to the risk engine
in live trading; in backtests we normalize to 1R per trade.)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

import pandas as pd

LONG = 1
SHORT = -1


@dataclass(frozen=True)
class Signal:
    """A trade intent at a specific bar.

    direction: LONG (+1) or SHORT (-1)
    stop_distance: distance from entry to initial stop, in PRICE units.
                   Defines 1R for this trade.
    target_r: take-profit distance as a multiple of R (e.g. 1.5), or
              None for no fixed target (trend-following exits).
    trail_distance: if set, a trailing stop in PRICE units — the stop
              ratchets to (best price since entry − trail_distance) for
              longs (mirrored for shorts), never loosening.
    """

    direction: int
    stop_distance: float
    target_r: Optional[float] = 1.5
    trail_distance: Optional[float] = None


class Strategy(Protocol):
    """A strategy is: name + a function of history → optional Signal.

    `history` is all COMPLETED candles up to and including the decision
    bar. The engine executes any signal at the NEXT bar's open — a
    strategy can never see or act on information from the future.
    """

    name: str

    def on_bar(self, history: pd.DataFrame) -> Optional[Signal]: ...
