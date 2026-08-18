"""Correlation buckets — the map that stops eight positions from being
one giant dollar bet in eight costumes.

Hand-assigned from known structure (v1). Roadmap: monthly rolling-
correlation check flags drift between this map and measured reality.

usd_long_when_long: True if being LONG the pair means being LONG USD.
(Long USD_JPY = long USD. Long EUR_USD = SHORT USD.)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PairInfo:
    bucket: str
    usd_long_when_long: bool


PAIRS = {
    "EUR_USD": PairInfo("usd_bloc", False),
    "GBP_USD": PairInfo("usd_bloc", False),
    "AUD_USD": PairInfo("usd_bloc", False),
    "NZD_USD": PairInfo("usd_bloc", False),
    "USD_CAD": PairInfo("usd_bloc", True),
    "USD_CHF": PairInfo("usd_bloc", True),
    "USD_JPY": PairInfo("jpy", True),
    "EUR_GBP": PairInfo("crosses", False),  # usd flag unused for crosses
    # Crypto (future): its own bucket — correlated to each other, not USD-FX
    "BTC_USD": PairInfo("crypto", False),
    "ETH_USD": PairInfo("crypto", False),
}

MAX_CONCURRENT = 3
MAX_PER_BUCKET = 2
USD_SAME_DIRECTION_WEIGHT = 2.0  # same-direction dollar bets count double
NORMAL_WEIGHT = 1.0


def usd_direction(pair: str, direction: int) -> int:
    """+1 if this position is net LONG USD, -1 if net SHORT, 0 if neither."""
    info = PAIRS.get(pair)
    if info is None or info.bucket == "crosses" or info.bucket == "crypto":
        return 0
    return direction if info.usd_long_when_long else -direction
