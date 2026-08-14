"""Oanda v20 candle fetcher.

Phase 0 exit criteria:
    python -m src.data.fetch EUR_USD H1 5000
pulls candles from the practice API and stores them in SQLite.

Design notes:
- Oanda caps `count` at 5000/request; we paginate backwards using `to`.
- Mid prices for research; the backtest cost model re-applies spread
  explicitly (never trust mid-price backtests).
- Idempotent: PRIMARY KEY (instrument, granularity, ts) + INSERT OR IGNORE,
  safe to re-run for incremental top-ups.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone

import requests

from src.config import settings
from src.data.store import ensure_schema, insert_candles, latest_ts

MAX_PER_REQUEST = 5000
RETRY_STATUSES = {429, 500, 502, 503, 504}


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "Authorization": f"Bearer {settings().oanda_token}",
            "Accept-Datetime-Format": "RFC3339",
        }
    )
    return s


def _request_candles(
    sess: requests.Session,
    instrument: str,
    granularity: str,
    count: int,
    to: str | None = None,
) -> list[dict]:
    url = f"{settings().rest_host}/v3/instruments/{instrument}/candles"
    params: dict = {"granularity": granularity, "price": "M", "count": count}
    if to:
        params["to"] = to

    for attempt in range(5):
        resp = sess.get(url, params=params, timeout=30)
        if resp.status_code in RETRY_STATUSES:
            wait = 2**attempt
            print(f"  HTTP {resp.status_code}, backing off {wait}s", file=sys.stderr)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json().get("candles", [])
    raise RuntimeError(f"Gave up fetching {instrument} after 5 attempts")


def _normalize(instrument: str, granularity: str, raw: list[dict]) -> list[tuple]:
    rows = []
    for c in raw:
        if not c.get("complete", False):
            continue  # never store the still-forming bar
        mid = c["mid"]
        rows.append(
            (
                instrument,
                granularity,
                c["time"],
                float(mid["o"]),
                float(mid["h"]),
                float(mid["l"]),
                float(mid["c"]),
                int(c["volume"]),
            )
        )
    return rows


def fetch_history(instrument: str, granularity: str, total: int) -> int:
    """Fetch `total` most-recent complete candles, paginating backwards."""
    ensure_schema()
    sess = _session()
    fetched = 0
    cursor: str | None = None  # RFC3339 upper bound, walks backwards

    while fetched < total:
        batch_size = min(MAX_PER_REQUEST, total - fetched)
        raw = _request_candles(sess, instrument, granularity, batch_size, to=cursor)
        if not raw:
            break
        rows = _normalize(instrument, granularity, raw)
        inserted = insert_candles(rows)
        fetched += len(rows)
        cursor = raw[0]["time"]  # oldest candle in batch → next upper bound
        print(
            f"  batch: {len(rows)} candles (new: {inserted}) "
            f"oldest={cursor} total={fetched}/{total}"
        )
        if len(raw) < batch_size:
            break  # ran out of history
        time.sleep(0.25)  # be polite to the practice API

    return fetched


def main() -> None:
    if len(sys.argv) != 4:
        print("usage: python -m src.data.fetch INSTRUMENT GRANULARITY COUNT")
        print("   ex: python -m src.data.fetch EUR_USD H1 5000")
        sys.exit(1)

    instrument, granularity, count = sys.argv[1], sys.argv[2], int(sys.argv[3])
    print(
        f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] "
        f"fetching {count} x {granularity} candles for {instrument} "
        f"({settings().env})"
    )
    n = fetch_history(instrument, granularity, count)
    print(f"done: {n} candles processed, latest stored ts = "
          f"{latest_ts(instrument, granularity)}")


if __name__ == "__main__":
    main()
