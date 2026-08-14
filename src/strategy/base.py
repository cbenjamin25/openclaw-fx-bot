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
    stop_distance: distance from entry to stop, in PRICE units (not pips).
                   Defines 1R for this trade.
    target_r: take-profit distance as a multiple of R (e.g. 1.5).
    """

    direction: int
    stop_distance: float
    target_r: float = 1.5


class Strategy(Protocol):
    """A strategy is: name + a function of history → optional Signal.

    `history` is all COMPLETED candles up to and including the decision
    bar. The engine executes any signal at the NEXT bar's open — a
    strategy can never see or act on information from the future.
    """

    name: str

    def on_bar(self, history: pd.DataFrame) -> Optional[Signal]: ...
