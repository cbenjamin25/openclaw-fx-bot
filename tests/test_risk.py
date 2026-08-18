"""Risk engine test suite — per the plan: every rule gets a test BEFORE
any live order code exists. The clock is injected so every time rule is
provable.

Scenario names reference the design session (2026-08-16)."""

from datetime import datetime, timedelta, timezone

import pytest

import src.config as config
from src.risk import kelly
from src.risk.engine import RiskEngine, session_open, week_open


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("FXBOT_DB", str(tmp_path / "risk.db"))
    monkeypatch.setenv("FXBOT_ENV", "practice")
    monkeypatch.setenv("FXBOT_OANDA_TOKEN", "t")
    monkeypatch.setenv("FXBOT_OANDA_ACCOUNT_ID", "a")
    config.settings.cache_clear()
    yield
    config.settings.cache_clear()


# A Tuesday 14:00 UTC — mid-session, mid-week.
T0 = datetime(2026, 8, 18, 14, 0, tzinfo=timezone.utc)
WINNING_HISTORY = [1.5, -1.0, 2.0, -1.0, 1.5, -1.0] * 10  # 60 trades, +edge
LOSING_HISTORY = [-1.0, 0.8, -1.0, -1.0, 0.7, -1.0] * 10  # negative edge


class Clock:
    def __init__(self, start):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


# ── session/week boundary math ──

def test_session_rolls_at_2100_utc():
    before = datetime(2026, 8, 18, 20, 59, tzinfo=timezone.utc)
    after = datetime(2026, 8, 18, 21, 1, tzinfo=timezone.utc)
    assert session_open(before).day == 17
    assert session_open(after).day == 18


def test_week_opens_sunday_2100():
    wed = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
    wo = week_open(wed)
    assert wo.weekday() == 6 and wo.hour == 21  # Sunday 21:00
    assert wo == datetime(2026, 8, 16, 21, 0, tzinfo=timezone.utc)


# ── sizing law ──

def test_bootstrap_sizing_before_30_trades():
    assert kelly.risk_dollars(10_000, [1.0] * 10) == 50.0  # 0.5%


def test_cap_binds_on_strong_edge():
    # Strong edge → quarter-Kelly way above 1% → cap rules.
    assert kelly.risk_dollars(10_000, WINNING_HISTORY) == 100.0


def test_negative_edge_sizes_zero_never_flips():
    assert kelly.risk_dollars(10_000, LOSING_HISTORY) == 0.0


def test_engine_refuses_on_zero_size():
    eng = RiskEngine(10_000, Clock(T0))
    d = eng.propose("USD_JPY", 1, LOSING_HISTORY)
    assert not d.approved and "size 0" in d.reason


# ── daily breaker: the two-loss day ──

def test_daily_breaker_trips_at_minus_2pct_and_auto_rearms():
    clock = Clock(T0)
    eng = RiskEngine(10_000, clock)
    # Two full losses at $100 risk ≈ -$210 realized < -$200 tripwire.
    for pair in ["USD_JPY", "USD_CAD"]:
        d = eng.propose(pair, 1, WINNING_HISTORY)
        assert d.approved
        eng.record_open(pair, 1, d.risk_dollars)
        action = eng.record_close(pair, -1.05)
    assert action == "FLATTEN_ALL"
    assert "DAILY halt" in (eng.is_halted() or "")
    # Still halted later the same session:
    clock.advance(hours=3)
    assert eng.is_halted() is not None
    # Auto re-arm after 21:00 UTC roll:
    clock.advance(hours=5)  # 22:00 — next session
    assert eng.is_halted() is None


def test_wins_bank_headroom_before_breaker():
    clock = Clock(T0)
    eng = RiskEngine(10_000, clock)
    d = eng.propose("USD_JPY", 1, WINNING_HISTORY)
    eng.record_open("USD_JPY", 1, d.risk_dollars)
    assert eng.record_close("USD_JPY", 1.5) is None  # +$150
    for r in (-1.05, -1.05):  # two losses: day at -$60 then... 
        d = eng.propose("USD_CAD", 1, WINNING_HISTORY)
        assert d.approved  # still trading
        eng.record_open("USD_CAD", 1, d.risk_dollars)
        action = eng.record_close("USD_CAD", r)
    assert action is None  # +150 -105 -105 = -60 > -200: no trip


# ── weekly breaker: human-only ceremony ──

def test_weekly_breaker_requires_exact_acknowledgment():
    clock = Clock(T0)
    eng = RiskEngine(10_000, clock)
    for i in range(5):  # five full losses = -$525 < -$500 weekly wire
        d = eng.propose("USD_JPY", 1, WINNING_HISTORY)
        if not d.approved:  # daily halt mid-way: jump to next session
            clock.advance(days=1)
            d = eng.propose("USD_JPY", 1, WINNING_HISTORY)
        eng.record_open("USD_JPY", 1, d.risk_dollars)
        eng.record_close("USD_JPY", -1.05)
    assert "WEEKLY halt" in (eng.is_halted() or "")
    clock.advance(days=10)  # calendar does NOT clear it
    assert "WEEKLY halt" in (eng.is_halted() or "")
    assert not eng.rearm("yeah ok")            # sloppy ack refused
    assert eng.rearm("I reviewed the trade journal")
    assert eng.is_halted() is None


def test_halt_survives_restart():
    clock = Clock(T0)
    eng = RiskEngine(10_000, clock)
    for _ in range(2):
        d = eng.propose("USD_JPY", 1, WINNING_HISTORY)
        eng.record_open("USD_JPY", 1, d.risk_dollars)
        eng.record_close("USD_JPY", -1.05)
    # New engine instance, same DB — a restart must not forget the trip.
    eng2 = RiskEngine(10_000, clock)
    assert eng2.is_halted() is not None


# ── exposure caps: the NFP scenario ──

def test_global_cap_three_positions():
    eng = RiskEngine(10_000, Clock(T0))
    for pair in ["USD_JPY", "EUR_GBP", "USD_CAD"]:
        d = eng.propose(pair, 1, WINNING_HISTORY)
        assert d.approved
        eng.record_open(pair, 1, d.risk_dollars)
    d = eng.propose("GBP_USD", -1, WINNING_HISTORY)
    assert not d.approved and "max concurrent" in d.reason


def test_bucket_cap_two_per_bucket():
    eng = RiskEngine(10_000, Clock(T0))
    # Two usd_bloc positions, OPPOSITE dollar direction (long+short USD):
    eng.record_open("USD_CAD", 1, 100)   # long USD
    eng.record_open("AUD_USD", 1, 100)   # short USD
    d = eng.propose("USD_CHF", 1, WINNING_HISTORY)  # third in usd_bloc
    assert not d.approved and "bucket usd_bloc" in d.reason


def test_nfp_rule_same_direction_dollar_capped_across_buckets():
    """8:30 NFP: everything screams long-dollar. Two get in; the third
    is refused even though it's in a DIFFERENT bucket (jpy)."""
    eng = RiskEngine(10_000, Clock(T0))
    eng.record_open("EUR_USD", -1, 100)  # short EUR_USD = long USD
    eng.record_open("USD_CAD", 1, 100)   # long USD
    d = eng.propose("USD_JPY", 1, WINNING_HISTORY)  # long USD via jpy bucket
    assert not d.approved and "USD same-direction" in d.reason


def test_opposite_dollar_direction_not_double_counted():
    eng = RiskEngine(10_000, Clock(T0))
    eng.record_open("EUR_USD", -1, 100)  # long USD
    eng.record_open("USD_CAD", -1, 100)  # SHORT USD
    d = eng.propose("USD_JPY", 1, WINNING_HISTORY)  # long USD: only 1 same-dir
    assert d.approved


# ── the queue ──

def test_refused_signals_are_queued():
    eng = RiskEngine(10_000, Clock(T0))
    for pair in ["USD_JPY", "EUR_GBP", "USD_CAD"]:
        d = eng.propose(pair, 1, WINNING_HISTORY)
        eng.record_open(pair, 1, d.risk_dollars)
    eng.propose("GBP_USD", -1, WINNING_HISTORY)
    q = eng.queued()
    assert len(q) == 1 and q[0][1] == "GBP_USD"
