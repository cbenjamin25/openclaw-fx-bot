"""The Risk Engine — absolute veto over every order. Nothing trades
around it; strategies propose, this layer disposes.

Spec (co-designed 2026-08-16, in project memory):
- Sizing: delegated to risk.kelly (quarter-Kelly, 1% cap, 0 floor).
- Daily breaker: realized day P&L <= -2% equity → flatten signal, halt
  entries, AUTO re-arm at next session open (21:00 UTC).
- Weekly breaker: realized week P&L <= -5% → halt, HUMAN-ONLY re-arm
  (requires explicit acknowledgment via rearm()).
- Exposure: max 3 concurrent; max 2.0 weight per bucket; same-direction
  USD positions weigh 2.0 against the usd_bloc budget; refused signals
  queue for freed slots (staleness enforced by caller re-validation).
- All halt state persists in SQLite: a restart never forgets a trip.

Clock is injectable (now_fn) so every time rule is testable.
FX session day rolls at 21:00 UTC; the "week" starts Sunday 21:00 UTC.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from src.data.store import conn
from src.risk import kelly
from src.risk.buckets import (
    MAX_CONCURRENT,
    MAX_PER_BUCKET,
    PAIRS,
    usd_direction,
)

DAILY_BREAKER_FRACTION = -0.02
WEEKLY_BREAKER_FRACTION = -0.05
SESSION_ROLL_UTC_HOUR = 21

SCHEMA = """
CREATE TABLE IF NOT EXISTS risk_trades (
    ts TEXT NOT NULL, pair TEXT NOT NULL, direction INTEGER NOT NULL,
    risk_dollars REAL NOT NULL, realized_r REAL, realized_pnl REAL,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS risk_halts (
    kind TEXT PRIMARY KEY,           -- 'daily' | 'weekly'
    tripped_at TEXT NOT NULL,
    details TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS risk_queue (
    ts TEXT NOT NULL, pair TEXT NOT NULL, direction INTEGER NOT NULL,
    payload TEXT NOT NULL
);
"""


def session_open(now: datetime) -> datetime:
    """Most recent 21:00 UTC at or before now."""
    anchor = now.replace(hour=SESSION_ROLL_UTC_HOUR, minute=0, second=0, microsecond=0)
    return anchor if now >= anchor else anchor - timedelta(days=1)


def week_open(now: datetime) -> datetime:
    """Most recent SUNDAY 21:00 UTC at or before now."""
    s = session_open(now)
    # weekday(): Mon=0 ... Sun=6. Walk back to Sunday.
    return s - timedelta(days=(s.weekday() - 6) % 7)


@dataclass
class Decision:
    approved: bool
    reason: str
    risk_dollars: float = 0.0


@dataclass
class OpenPosition:
    pair: str
    direction: int
    risk_dollars: float


class RiskEngine:
    def __init__(self, equity: float, now_fn: Callable[[], datetime] | None = None):
        self.equity = equity
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self.open_positions: list[OpenPosition] = []
        with conn() as c:
            c.executescript(SCHEMA)

    # ── realized P&L windows ──

    def _realized_since(self, since: datetime) -> float:
        with conn() as c:
            row = c.execute(
                "SELECT COALESCE(SUM(realized_pnl), 0) FROM risk_trades "
                "WHERE status='closed' AND ts >= ?",
                (since.isoformat(),),
            ).fetchone()
        return float(row[0])

    def day_pnl(self) -> float:
        return self._realized_since(session_open(self.now_fn()))

    def week_pnl(self) -> float:
        return self._realized_since(week_open(self.now_fn()))

    # ── halt state ──

    def _halt(self, kind: str, details: str) -> None:
        with conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO risk_halts VALUES (?,?,?)",
                (kind, self.now_fn().isoformat(), details),
            )

    def _get_halt(self, kind: str) -> Optional[tuple[datetime, str]]:
        with conn() as c:
            row = c.execute(
                "SELECT tripped_at, details FROM risk_halts WHERE kind=?", (kind,)
            ).fetchone()
        if not row:
            return None
        return datetime.fromisoformat(row[0]), row[1]

    def is_halted(self) -> Optional[str]:
        """None if clear to trade; else the reason string."""
        weekly = self._get_halt("weekly")
        if weekly:
            return f"WEEKLY halt since {weekly[0].isoformat()} — human re-arm required"
        daily = self._get_halt("daily")
        if daily:
            tripped_at, _ = daily
            if session_open(self.now_fn()) <= tripped_at:
                return f"DAILY halt since {tripped_at.isoformat()} — auto re-arms next session"
            # Session rolled: auto re-arm by clearing the stale halt.
            with conn() as c:
                c.execute("DELETE FROM risk_halts WHERE kind='daily'")
        return None

    def rearm(self, acknowledgment: str) -> bool:
        """Human-only weekly re-arm. Requires the exact phrase, forcing a
        deliberate act after reviewing the journal."""
        if acknowledgment.strip() != "I reviewed the trade journal":
            return False
        with conn() as c:
            c.execute("DELETE FROM risk_halts WHERE kind='weekly'")
            c.execute("DELETE FROM risk_halts WHERE kind='daily'")
        return True

    # ── breaker checks (call after every close) ──

    def check_breakers(self) -> Optional[str]:
        """Returns 'FLATTEN_ALL' if a breaker just tripped, else None."""
        if self.week_pnl() <= WEEKLY_BREAKER_FRACTION * self.equity:
            self._halt("weekly", f"week_pnl={self.week_pnl():.2f}")
            return "FLATTEN_ALL"
        if self.day_pnl() <= DAILY_BREAKER_FRACTION * self.equity:
            if not self._get_halt("daily"):
                self._halt("daily", f"day_pnl={self.day_pnl():.2f}")
            return "FLATTEN_ALL"
        return None

    # ── exposure accounting ──

    def _bucket_count(self, bucket: str, candidate: OpenPosition) -> int:
        positions = self.open_positions + [candidate]
        return sum(1 for p in positions if PAIRS[p.pair].bucket == bucket)

    def _usd_same_direction_count(self, candidate: OpenPosition) -> int:
        """How many OPEN positions already share the candidate's net-USD
        direction. Two same-direction dollar bets fill the USD budget —
        a third is refused regardless of bucket (the NFP rule)."""
        cand_dir = usd_direction(candidate.pair, candidate.direction)
        if cand_dir == 0:
            return 0
        return sum(
            1
            for p in self.open_positions
            if usd_direction(p.pair, p.direction) == cand_dir
        )

    # ── the gate every signal walks through ──

    def propose(self, pair: str, direction: int, recent_r: list[float]) -> Decision:
        halted = self.is_halted()
        if halted:
            self._queue(pair, direction, halted)
            return Decision(False, halted)

        if pair not in PAIRS:
            return Decision(False, f"unknown pair {pair} — no bucket mapping")

        if len(self.open_positions) >= MAX_CONCURRENT:
            self._queue(pair, direction, "max concurrent positions")
            return Decision(False, "max concurrent positions (3) — queued")

        candidate = OpenPosition(pair, direction, 0.0)
        bucket = PAIRS[pair].bucket
        if self._bucket_count(bucket, candidate) > MAX_PER_BUCKET:
            self._queue(pair, direction, f"bucket {bucket} full")
            return Decision(False, f"bucket {bucket} at capacity — queued")
        if self._usd_same_direction_count(candidate) >= MAX_PER_BUCKET:
            self._queue(pair, direction, "USD same-direction budget full")
            return Decision(
                False, "USD same-direction exposure at capacity (2) — queued"
            )

        dollars = kelly.risk_dollars(self.equity, recent_r)
        if dollars <= 0:
            return Decision(False, "rolling expectancy non-positive — size 0, not trading")

        return Decision(True, "approved", risk_dollars=dollars)

    # ── lifecycle recording ──

    def record_open(self, pair: str, direction: int, risk_dollars: float) -> None:
        self.open_positions.append(OpenPosition(pair, direction, risk_dollars))
        with conn() as c:
            c.execute(
                "INSERT INTO risk_trades VALUES (?,?,?,?,NULL,NULL,'open')",
                (self.now_fn().isoformat(), pair, direction, risk_dollars),
            )

    def record_close(self, pair: str, realized_r: float) -> Optional[str]:
        pos = next((p for p in self.open_positions if p.pair == pair), None)
        if pos:
            self.open_positions.remove(pos)
            pnl = realized_r * pos.risk_dollars
            with conn() as c:
                c.execute(
                    "INSERT INTO risk_trades VALUES (?,?,?,?,?,?,'closed')",
                    (self.now_fn().isoformat(), pair, pos.direction,
                     pos.risk_dollars, realized_r, pnl),
                )
        return self.check_breakers()

    def flatten_all(self) -> list[str]:
        flattened = [p.pair for p in self.open_positions]
        self.open_positions.clear()
        return flattened

    # ── the queue ──

    def _queue(self, pair: str, direction: int, reason: str) -> None:
        with conn() as c:
            c.execute(
                "INSERT INTO risk_queue VALUES (?,?,?,?)",
                (self.now_fn().isoformat(), pair, direction,
                 json.dumps({"reason": reason})),
            )

    def queued(self) -> list[tuple[str, str, int]]:
        with conn() as c:
            rows = c.execute(
                "SELECT ts, pair, direction FROM risk_queue ORDER BY ts"
            ).fetchall()
        return [(r[0], r[1], int(r[2])) for r in rows]
