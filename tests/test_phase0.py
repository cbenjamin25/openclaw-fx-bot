"""Phase 0 tests: storage idempotency + config safety guards.

Per the build plan, the risk layer gets tests before features — starting
with the guard that makes accidental live trading structurally impossible.
"""

import os

import pytest

import src.config as config
from src.data import store


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("FXBOT_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("FXBOT_ENV", "practice")
    monkeypatch.setenv("FXBOT_OANDA_TOKEN", "test-token")
    monkeypatch.setenv("FXBOT_OANDA_ACCOUNT_ID", "test-acct")
    config.settings.cache_clear()
    yield
    config.settings.cache_clear()


ROW = ("EUR_USD", "H1", "2026-08-14T00:00:00.000000000Z",
       1.10, 1.11, 1.09, 1.105, 1234)


def test_schema_and_insert():
    store.ensure_schema()
    assert store.insert_candles([ROW]) == 1
    assert store.latest_ts("EUR_USD", "H1") == ROW[2]


def test_insert_is_idempotent():
    store.ensure_schema()
    store.insert_candles([ROW])
    assert store.insert_candles([ROW]) == 0  # re-run adds nothing


def test_load_df_roundtrip():
    store.ensure_schema()
    store.insert_candles([ROW])
    df = store.load_df("EUR_USD", "H1")
    assert len(df) == 1
    assert float(df.iloc[0]["close"]) == 1.105
    assert df.index.tz is not None  # UTC-aware index


def test_practice_is_default():
    os.environ.pop("FXBOT_ENV", None)
    config.settings.cache_clear()
    assert config.settings().env == "practice"
    assert "fxpractice" in config.settings().rest_host


def test_live_requires_double_opt_in(monkeypatch):
    monkeypatch.setenv("FXBOT_ENV", "live")
    monkeypatch.delenv("FXBOT_I_UNDERSTAND_LIVE", raising=False)
    config.settings.cache_clear()
    with pytest.raises(RuntimeError, match="FXBOT_I_UNDERSTAND_LIVE"):
        config.settings()


def test_missing_token_fails_loudly(monkeypatch):
    monkeypatch.delenv("FXBOT_OANDA_TOKEN", raising=False)
    monkeypatch.setattr(config, "_from_ssm", lambda name: None)
    config.settings.cache_clear()
    with pytest.raises(RuntimeError, match="oanda_token"):
        config.settings()
