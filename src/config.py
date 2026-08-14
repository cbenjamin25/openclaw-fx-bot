"""Central config. Secrets come from AWS SSM Parameter Store (SecureString).

Never put the Oanda token in a file or env var on the EC2 box. The instance
role grants ssm:GetParameter on /fxbot/* only.

Local dev fallback: export FXBOT_OANDA_TOKEN / FXBOT_OANDA_ACCOUNT_ID.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

SSM_PREFIX = "/fxbot"

OANDA_HOSTS = {
    "practice": "https://api-fxpractice.oanda.com",
    "live": "https://api-fxtrade.oanda.com",
}
OANDA_STREAM_HOSTS = {
    "practice": "https://stream-fxpractice.oanda.com",
    "live": "https://stream-fxtrade.oanda.com",
}


@dataclass(frozen=True)
class Settings:
    env: str                # "practice" | "live"
    oanda_token: str
    oanda_account_id: str
    db_path: str

    @property
    def rest_host(self) -> str:
        return OANDA_HOSTS[self.env]

    @property
    def stream_host(self) -> str:
        return OANDA_STREAM_HOSTS[self.env]


def _from_ssm(name: str) -> str | None:
    try:
        import boto3  # optional locally

        ssm = boto3.client("ssm")
        resp = ssm.get_parameter(Name=f"{SSM_PREFIX}/{name}", WithDecryption=True)
        return resp["Parameter"]["Value"]
    except Exception:
        return None


def _get(name: str, env_var: str) -> str:
    val = os.environ.get(env_var) or _from_ssm(name)
    if not val:
        raise RuntimeError(
            f"Missing secret '{name}': set {env_var} or create SSM param {SSM_PREFIX}/{name}"
        )
    return val


@lru_cache(maxsize=1)
def settings() -> Settings:
    env = os.environ.get("FXBOT_ENV", "practice")
    if env not in OANDA_HOSTS:
        raise RuntimeError(f"FXBOT_ENV must be practice|live, got {env!r}")
    # Hard guard: live requires explicit double opt-in.
    if env == "live" and os.environ.get("FXBOT_I_UNDERSTAND_LIVE") != "yes":
        raise RuntimeError(
            "FXBOT_ENV=live requires FXBOT_I_UNDERSTAND_LIVE=yes. "
            "Phase 7 gate: do not set this until the demo gauntlet passes."
        )
    return Settings(
        env=env,
        oanda_token=_get("oanda_token", "FXBOT_OANDA_TOKEN"),
        oanda_account_id=_get("oanda_account_id", "FXBOT_OANDA_ACCOUNT_ID"),
        db_path=os.environ.get("FXBOT_DB", os.path.expanduser("~/fxbot/candles.db")),
    )
