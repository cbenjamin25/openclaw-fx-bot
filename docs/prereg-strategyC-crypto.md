# Pre-Registration: Frozen Strategy B on Crypto (BTC/ETH)

**Registered:** 2026-08-18, BEFORE any crypto price data has been
fetched or examined by any decision-maker in this project.
**Status:** LOCKED. Changes to this document after data contact void
the test.

## Hypothesis (mechanism, stated in advance)

Trend-continuation strategies feed on markets where prices are not
pinned by policy convergence and where participant behavior produces
persistent directional moves. Crypto (BTC, ETH) structurally resembles
pre-efficiency FX / 20th-century commodities: retail-heavy, narrative-
driven, no central bank targeting the price, 24/7 sessions. Academic
literature (Cambridge-affiliated, 2020) documents strong trend-following
returns in crypto's first decade with commodity-like characteristics.

**Prediction:** the FROZEN trend_pullback strategy (exactly as buried in
the FX graveyard, parameters unchanged, full grid unchanged) shows
positive out-of-sample expectancy on BTC_USD and ETH_USD under the
walk-forward protocol — on BOTH horizons.

## What is frozen

- Strategy: `src/strategy/trend.py` as of commit at registration.
- Grid: `TREND_GRID_FULL` unchanged.
- Walk-forward: 12mo train / 3mo test, H4 granularity, min-train-trades 12.
- Gates: the five, unchanged. Portfolio aggregation allowed across
  BTC+ETH only (declared now), exact trade-level replay required.

## Dual-horizon rule (project standing rule, 2026-08-18)

- Recent era: last ~5 years.
- Deep history: ALL available exchange history (BTC ~2013+, ETH ~2016+).
- Gates apply to the DEEP result. A recent-only pass is not acceptance.

## Cost model (declared before testing)

Crypto costs differ from FX and must be pessimistic:
- Spread: 2 bps BTC, 4 bps ETH (padded retail spot spreads).
- Taker fee: 10 bps per side (retail exchange tier, no discounts).
- Slippage: 2 bps per side.
- Total round trip ≈ 28–32 bps — encoded in a CryptoCostModel before
  any backtest runs. If realized historical spreads were worse in early
  years (they were), results are flattered; interpret accordingly.

## Data plan

- Source: public exchange OHLCV via ccxt (Coinbase or Kraken spot),
  H4 candles, full history, stored in the same SQLite vault under
  instruments BTC_USD / ETH_USD.
- Data-quality rule: gaps > 2 consecutive H4 bars are logged; windows
  containing exchange-outage gaps are flagged in the audit.

## One-shot rule

This test runs ONCE per horizon. No parameter changes, no pair
swapping, no grid edits afterward against this data. If it fails, the
mechanism hypothesis is rejected for this implementation and the
result goes to the graveyard with cause of death. Any NEW crypto
hypothesis afterward requires a fresh pre-registration naming what
changed and why, and burns its own one shot.

## Interpretation table (committed in advance)

| Outcome (deep horizon) | Reading |
|---|---|
| All 5 gates pass | Certified candidate → Monte Carlo → demo gauntlet design |
| G1+ but PF/DD short | Real but thin: portfolio-component candidate only; no solo deployment |
| G1 negative | Mechanism rejected for this implementation; graveyard |
| Recent-era pass, deep fail | Era luck again; graveyard; the standing rule exists for this |
