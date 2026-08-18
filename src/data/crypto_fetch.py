"""Crypto candle fetcher — ccxt / Bitstamp, per the pre-registration
(docs/prereg-strategyC-crypto.md, amended source 2026-08-18).

Usage (on the instance, venv active, ccxt installed):
    python -m src.data.crypto_fetch BTC/USD
    python -m src.data.crypto_fetch ETH/USD

Stores into the SAME vault as FX (instrument BTC_USD / ETH_USD,
granularity H4) so the entire courtroom — engine, walk-forward,
portfolio, gates — works unchanged.

Data-quality rule (pre-registered): gaps > 2 consecutive H4 bars are
counted and reported at the end of the run.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone

from src.data.store import ensure_schema, insert_candles, latest_ts

TIMEFRAME = "4h"
BATCH = 1000
FOUR_H_MS = 4 * 3600 * 1000


def _to_rfc3339(ms: int) -> str:
    return (
        datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%S.000000000Z")
    )


def fetch_all(symbol: str) -> None:
    import ccxt  # imported here so the module is importable without it

    ensure_schema()
    ex = ccxt.bitstamp({"enableRateLimit": True})
    instrument = symbol.replace("/", "_")

    since = ex.parse8601("2011-01-01T00:00:00Z")
    total = 0
    gaps = 0
    prev_ms: int | None = None
    THIRTY_DAYS_MS = 30 * 24 * 3600 * 1000

    while since < ex.milliseconds() - FOUR_H_MS:
        candles = ex.fetch_ohlcv(symbol, TIMEFRAME, since=since, limit=BATCH)
        if not candles:
            # Pre-data era or a dead stretch: walk forward in time.
            # (Early-2010s crypto has genuinely empty periods.)
            since += THIRTY_DAYS_MS
            time.sleep(ex.rateLimit / 1000)
            continue
        rows = []
        for ms, o, h, l, c, v in candles:
            if prev_ms is not None and ms - prev_ms > 3 * FOUR_H_MS:
                gaps += 1  # gap of >2 missing bars (pre-registered rule)
            prev_ms = ms
            rows.append(
                (instrument, "H4", _to_rfc3339(ms),
                 float(o), float(h), float(l), float(c), float(v or 0))
            )
        # Drop the still-forming final bar if it's the current period.
        now_ms = ex.milliseconds()
        if rows and candles[-1][0] + FOUR_H_MS > now_ms:
            rows = rows[:-1]
        inserted = insert_candles(rows)
        total += len(rows)
        print(
            f"  batch: {len(rows)} candles (new: {inserted}) "
            f"through {rows[-1][2] if rows else '—'} total={total}"
        )
        # Sparse eras return partial batches that are NOT the end of
        # data — always keep walking from the last received candle.
        since = candles[-1][0] + 1
        time.sleep(ex.rateLimit / 1000)

    print(
        f"done: {total} candles processed, gaps(>2 bars)={gaps}, "
        f"latest stored ts = {latest_ts(instrument, 'H4')}"
    )


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: python -m src.data.crypto_fetch BTC/USD")
        sys.exit(1)
    print(
        f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] "
        f"fetching ALL {TIMEFRAME} history for {sys.argv[1]} from Bitstamp"
    )
    fetch_all(sys.argv[1])


if __name__ == "__main__":
    main()
