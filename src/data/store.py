"""SQLite candle store. Deliberately boring.

SQLite is the Phase 0-2 choice (single writer, local research). If/when we
need concurrent writers or multi-instrument streaming at scale, the plan
allows migration — but don't build for that day before it arrives.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager

from src.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
    instrument  TEXT NOT NULL,
    granularity TEXT NOT NULL,
    ts          TEXT NOT NULL,   -- RFC3339 UTC from Oanda
    open        REAL NOT NULL,
    high        REAL NOT NULL,
    low         REAL NOT NULL,
    close       REAL NOT NULL,
    volume      INTEGER NOT NULL,
    PRIMARY KEY (instrument, granularity, ts)
);
CREATE INDEX IF NOT EXISTS idx_candles_lookup
    ON candles (instrument, granularity, ts);
"""


@contextmanager
def conn():
    path = settings().db_path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    c = sqlite3.connect(path)
    try:
        yield c
        c.commit()
    finally:
        c.close()


def ensure_schema() -> None:
    with conn() as c:
        c.executescript(SCHEMA)


def insert_candles(rows: list[tuple]) -> int:
    """INSERT OR IGNORE; returns number of NEW rows."""
    if not rows:
        return 0
    with conn() as c:
        before = c.execute("SELECT COUNT(*) FROM candles").fetchone()[0]
        c.executemany(
            "INSERT OR IGNORE INTO candles VALUES (?,?,?,?,?,?,?,?)", rows
        )
        after = c.execute("SELECT COUNT(*) FROM candles").fetchone()[0]
    return after - before


def latest_ts(instrument: str, granularity: str) -> str | None:
    with conn() as c:
        row = c.execute(
            "SELECT MAX(ts) FROM candles WHERE instrument=? AND granularity=?",
            (instrument, granularity),
        ).fetchone()
    return row[0] if row else None


def load_df(instrument: str, granularity: str):
    """Load candles as a pandas DataFrame indexed by UTC timestamp."""
    import pandas as pd

    with conn() as c:
        df = pd.read_sql_query(
            "SELECT ts, open, high, low, close, volume FROM candles "
            "WHERE instrument=? AND granularity=? ORDER BY ts",
            c,
            params=(instrument, granularity),
        )
    df["ts"] = pd.to_datetime(df["ts"], utc=True, format="ISO8601")
    return df.set_index("ts")
