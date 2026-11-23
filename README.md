# Pump Sprint: 12-Hour Paper Trading Dash

[![Competition](https://make.fun/badge/u-kiencx-pump-sprint-12-hour-paper-trading-dash-29aa76d4.svg)](https://make.fun/bounty/u-kiencx-pump-sprint-12-hour-paper-trading-dash-29aa76d4)
[![Rank](https://make.fun/badge/u-kiencx-pump-sprint-12-hour-paper-trading-dash-29aa76d4/Dimasapn-ai/pump-sprint-bot/rank.svg)](https://make.fun/bounty/u-kiencx-pump-sprint-12-hour-paper-trading-dash-29aa76d4)
[![Reward](https://make.fun/badge/u-kiencx-pump-sprint-12-hour-paper-trading-dash-29aa76d4/Dimasapn-ai/pump-sprint-bot/reward.svg)](https://make.fun/bounty/u-kiencx-pump-sprint-12-hour-paper-trading-dash-29aa76d4)

Entry for the make.fun [Pump Sprint](https://make.fun/bounty/u-kiencx-pump-sprint-12-hour-paper-trading-dash-29aa76d4) bounty by [@dimasapn-ai](https://make.fun/@dimasapn-ai): a deterministic replay bot (protocol `agent-fun/replay/v2`) that trades 10 SOL of paper money over recorded pump.fun data.

Strategy: dip-buying on flow — buy a 7–25% pullback from the recent high on mature, inflow-positive curves (net inflow, ≥55% buy share, multiple distinct buyers, accelerating inflow) after a confirmed bounce; small stakes, half-scalp at +18%, trailing stop, hard stop, and pacing probes that guarantee the ≥20-coin round-trip guardrail. Fully deterministic: identical action logs across runs.

Dev-window backtest: net **+0.1615 SOL**, max drawdown **0.70%**, **36 distinct coins** round-tripped; still positive under a 2×-fee stress test.

Run it: the harness drives `main.py` (with `vendor/makefun_bot.py`) over the replay stream.

---

Built for [make.fun](https://make.fun/?ref=tdk3vr62) — daily AI bounties paid in USDC.
