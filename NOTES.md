# Pump Sprint — entry notes

Competition: `u-kiencx-pump-sprint-12-hour-paper-trading-dash-29aa76d4`
Protocol: `agent-fun/replay/v2` (harness-run replay over recorded pump.fun data).

## Layout (as prescribed by the kit)

- `main.py` — the bot. The grader runs `python3 -P -s /bot/main.py` from the
  repo root; the file inserts `vendor/` into `sys.path` itself because `-P`
  leaves the script directory off the path.
- `vendor/makefun_bot.py` — the kit's Python shim, copied verbatim from
  `sdk/python/makefun_bot.py`. All protocol I/O goes through it.

## Contract summary (from the kit)

- Start: 10 SOL paper money. Orders decided on slot `s` fill at `s+2` against
  that mint's last recorded trade in the fill slot; my fills move the curve
  (persistent self-impact via reserve offsets).
- Buys are exact-in; curve fee is 125 bps (95 protocol + 30 creator fallback)
  on buys and sells; every filled or failed order also pays a 105,000-lamport
  transaction fee (rejected orders pay nothing); 2% of orders fail by a seeded
  draw; a slippage guard rejects fills worse than `max_slippage_bps` against
  decision-time reserves.
- Per-mint buy cap per batch: 1% of real quote reserves. Max 5 open
  positions, max 16 orders per batch, no shorts. Positions auto-close at
  curve completion and are flattened at window end.
- Ranking is net PnL in lamports; a prize requires net PnL > 0 after fees.
  Guardrails: round trips whose buy fills total >= 0.01 SOL on at least 20
  distinct coins, and max drawdown <= 50% (per-batch equity: cash plus
  close-value of open positions). The entry runs twice and the two action
  logs must be byte-identical — the bot is a pure function of the batches it
  has seen (no clock, no environment, no randomness, insertion-ordered
  iteration only).

## Strategy

Dip-buying inside other people's pumps — never chasing a high.

- **Tape**: per mint, an incremental rolling tape over two windows
  (W1 ≈ 280 slots ≈ 1 min, W2 ≈ 1680 slots ≈ 6 min): net/buy SOL flow,
  distinct-buyer counts, and monotone deques for the window high and low.
- **Entries** require a mature curve (≥50 lifetime trades, ≥10 SOL real
  reserves, ≥~4 min old; stricter for curves first seen mid-window), strong
  W2 inflow (net ≥ 0.8 SOL, buy share ≥ 55%, ≥6 distinct buyers, inflow
  accelerating ≥1.2× from W2 to W1), and the dip setup itself: price at
  75–93% of the W2 high, already ≥5% off a W2 low that is ≥80 slots old,
  and the whole window rise capped at 2.5×. Stake 0.12 SOL, at most 2 closed
  episodes per coin, 2,800-slot re-entry cooldown, pyramids of 0.12 SOL only
  after +35% price steps (max 2 adds).
- **Exits**: sell half at 118% of cost (scalp the bounce), trailing stop
  (arms at 110% of cost on peak close value, sells at 88% of peak), hard
  stop at 85% of cost, flow-fade exit (both windows net-negative while under
  110% of cost), max hold 5,000 slots (~18 min).
- **Guardrail insurance**: pacing probes — 0.012 SOL round trips on deep,
  quiet curves every 40 batches (up to 2 concurrent) until 24 distinct coins
  have round-tripped, so the 20-coin bar is cleared early even in a short or
  quiet final window. Exposure cap 2.2 SOL, 0.3 SOL cash buffer, equity
  brake (no entries below 80% of peak equity; probes pause below 62%).

## Backtest (dev window, full 24 h dataset: 128,058 events, 63,369 batches)

Backtested with a local driver: a TypeScript port of the kit's simulation
engine (the same port shipped as the pilot baseline's `tools/sim.ts`,
extended to all three dataset parts) spawning the real bot and speaking the
wire protocol. (The kit's own `grade-local` drives Python bots through
Docker, which is not available in this build environment.)

| run | net PnL | max DD | distinct coins | round trips |
|---|---|---|---|---|
| base | **+0.1615 SOL** | 0.70% | 36 | 44 |
| failures disabled | +0.1610 SOL | 0.70% | 36 | 44 |
| 2× fees (stress) | +0.0315 SOL | 0.87% | 36 | 44 |

Window segments, same parameters: first ~8 h alone +0.020 SOL (21 coins);
remaining ~16 h alone +0.112 SOL (24 coins). Both segments positive, and
the 20-coin guardrail clears even in the short segment. Determinism: two
executions produced byte-identical action logs (sha256
`933c6ddf…a81517bd10c8` over the reply stream). Neighbouring parameter sets
(scalp 1.15–1.22, trail-keep 0.85–0.90, stop 0.85–0.88, W2 net gate
0.6–0.8 SOL) all land between +0.10 and +0.16 SOL — the chosen point sits on
a plateau, not a spike. Excluding the three best coins, the full-window run
is still ≈ +0.016 SOL: the result is not carried by a couple of lucky coins.

## How it got here (kept honest)

- The house pilot momentum bot loses −9.38 SOL on its own backtest window;
  my first version (momentum-chasing near window highs) lost −1.39 SOL on
  the full dev window, and a tightened momentum version −0.88 SOL. Gains on
  this tape are extremely concentrated and momentum entries mostly buy the
  top of moves that immediately decay.
- Inverting the entry — same flow gates, but buying a 7–25% pullback that
  has started to bounce — flipped the sign (−0.48 → −0.33 → +0.10 as the
  bounce confirmation, two-episode cap and half-scalp were added), and the
  faster probe cadence lifted it to +0.16 with the guardrail safe in every
  segment.

## Risks

- The final runs on a fresh 6 h window; dev-window expectancy (+1.6% on
  capital) is thin and could land slightly negative on a hostile tape. The
  mitigations are structural, not fitted: tiny drawdown (0.7% vs the 50%
  bar), hard exposure cap, equity brake, and per-coin episode caps.
- The 2×-fees stress stays positive but close to zero (+0.03 SOL): the
  strategy's edge per trade is small relative to the 125 bps curve fee, so
  a fee regime change would hurt more than a volatility change.
- Entries depend on other traders' flow existing in the window; in a dead
  window the bot mostly sits out (probes still clear the coin guardrail).
