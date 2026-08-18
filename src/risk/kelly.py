"""Sizing law (permanent, per project policy 2026-08-16):

    1R = min(quarter-Kelly from rolling realized expectancy, 1% of equity)

- Kelly f* = p - (1-p)/b, computed from the last N realized trades.
- Quarter it (estimation-error margin).
- FLOOR AT ZERO: negative rolling edge → size 0 → bot stops trading.
  Never flip, never fade own signals.
- HARD CAP 1%: growth comes from breadth, never from size.
- Bootstrap: until MIN_TRADES realized trades exist, size at half the
  cap (0.5%) — conservative until there's evidence to size from.
"""

from __future__ import annotations

HARD_CAP_FRACTION = 0.01       # 1% of equity, permanent
BOOTSTRAP_FRACTION = 0.005     # until enough realized trades exist
MIN_TRADES_FOR_KELLY = 30
KELLY_FRACTION = 0.25          # quarter-Kelly


def kelly_fraction(r_multiples: list[float]) -> float:
    """Full Kelly f* from realized R-multiples. Returns 0 if no edge."""
    if not r_multiples:
        return 0.0
    wins = [r for r in r_multiples if r > 0]
    losses = [-r for r in r_multiples if r <= 0]
    if not wins:
        return 0.0
    if not losses:
        # All wins in window: Kelly is undefined-high; defer to the cap.
        return 1.0
    p = len(wins) / len(r_multiples)
    avg_win = sum(wins) / len(wins)
    avg_loss = sum(losses) / len(losses)
    if avg_loss <= 0:
        return 1.0
    b = avg_win / avg_loss
    f = p - (1 - p) / b
    return max(0.0, f)


def risk_fraction(recent_r_multiples: list[float]) -> float:
    """The fraction of equity to risk on the next trade."""
    if len(recent_r_multiples) < MIN_TRADES_FOR_KELLY:
        return BOOTSTRAP_FRACTION
    quarter = KELLY_FRACTION * kelly_fraction(recent_r_multiples)
    return min(quarter, HARD_CAP_FRACTION)


def risk_dollars(equity: float, recent_r_multiples: list[float]) -> float:
    return round(equity * risk_fraction(recent_r_multiples), 2)
