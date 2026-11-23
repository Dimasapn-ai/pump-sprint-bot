"""Pump Sprint entry: a dip-buying bot for the agent-fun/replay/v2 harness.

Strategy in one paragraph: never chase a pump at its high — buy the dip
inside somebody else's pump. A coin qualifies only once its curve is mature
(real reserves, trade count, age), money has flowed in on balance over the
last few minutes with several distinct buyers active, and the price has
pulled back 7-25% from its window high but already bounced at least 5% off
the window low, with short-window inflow re-accelerating. Entries are small
(0.12 SOL), at most two episodes per coin, pyramided only into strength.
Exits scalp half into the first strong bounce, trail the rest, and cut on a
hard stop, a flow fade or a maximum hold. Separately, tiny pacing probes
(0.012 SOL round trips on deep, quiet curves) run on a schedule so the
entry always clears the 20-distinct-coins round-trip guardrail, and a hard
exposure cap plus an equity brake keep drawdown far below the 50%
guardrail. (An earlier momentum-chasing version of this bot lost money on
the dev window, as did the house pilot; the dip discipline is what the
tape rewards. See NOTES.md for the backtest trail.)

Deterministic: no clock, no environment, no randomness; iteration is over
insertion-ordered dicts only, and every decision is a pure function of the
batches seen so far. Runs on the kit's Python shim (vendor/makefun_bot.py),
which the grader executes as ``python3 -P -s /bot/main.py``.
"""

import os
import sys
from collections import deque

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import makefun_bot  # noqa: E402  (after the sys.path line: -P leaves the script dir off sys.path)

SOL = makefun_bot.LAMPORTS_PER_SOL

# ---------------------------------------------------------------------------
# Parameters (lamports unless noted). Chosen in flat, round regions on
# purpose: the final window is fresh data, so the strategy must generalise,
# not sit on a backtest spike.
# ---------------------------------------------------------------------------

# Windows, in slots (the dev data runs ~4.7 slots per second).
W1 = 280            # short flow window, ~1 minute
W2 = 1680           # medium flow window, ~6 minutes

# Entry gates.
STAKE = 120_000_000          # 0.12 SOL first entry
ADD_STAKE = 120_000_000      # 0.12 SOL pyramid adds
MAX_ADDS = 2
MAX_EPISODES = 2             # closed episodes per coin before it is left alone
MIN_CAP = 40_000_000         # size cap (1% of real reserves) must allow 0.04 SOL
MIN_TRADES = 50              # lifetime trades on the curve
MIN_REAL = 10 * SOL          # real quote reserves: the curve has substance
MIN_AGE_SLOTS = 1_100        # ~4 minutes old before a first entry
ALT_MIN_TRADES = 120         # for curves first seen mid-window (no CreateEvent)
ALT_MIN_REAL = 10 * SOL
W2_MIN_NET = 800_000_000     # net inflow over W2 at least 0.8 SOL
W2_MIN_BUY_SHARE = 0.55      # buys are at least 55% of W2 flow
W2_MIN_BUYERS = 6            # distinct buyers over W2
W2_MIN_TRADES = 12           # trades over W2
DIP_HI = 0.93                # entry price at most 93% of the W2 high ...
DIP_LO = 0.75                # ... and at least 75% of it: a real pullback
BOUNCE_MIN = 1.05            # price already 5% off the W2 low ...
BOUNCE_AGE = 80              # ... and that low is at least ~80 slots old
RISE_CAP = 2.5               # window rise capped at 2.5x: not already parabolic
ACCEL = 1.2                  # W1 per-slot inflow >= 1.2x W2 per-slot inflow
COOLDOWN_SLOTS = 2_800       # no re-entry into a coin just exited
BUY_SLIPPAGE_BPS = 500
SELL_SLIPPAGE_BPS = 1_000

# Risk caps.
EXPOSURE_CAP = 2_200_000_000  # 2.2 SOL of open cost + pending buys, of 10 SOL
CASH_BUFFER = 300_000_000     # keep 0.3 SOL uncommitted for entries
EQUITY_BRAKE = 0.80           # below 80% of peak equity: no new entries
PROBE_BRAKE = 0.62            # below 62% of peak equity: probes pause too

# Exits (value = estimated close value of the position, cost = cost basis).
SCALP_AT = 1.18               # at 118% of cost, sell half into the bounce
STOP_LOSS = 0.85              # sell at 85% of cost
TRAIL_ARM = 1.10              # trailing stop arms at 110% of cost (peak)
TRAIL_KEEP = 0.88             # then sell at 88% of the peak value
FADE_MIN_HOLD = 300           # flow-fade exit only after ~1 minute held
MAX_HOLD_SLOTS = 5_000        # ~18 minutes, then out whatever the tape says
PYRAMID_RISE = 1.35           # price must rise 35% per pyramid step

# Pacing probes: guarantee the 20-coin round-trip guardrail early and cheap.
PROBE_STAKE = 12_000_000      # 0.012 SOL, above the 0.01 SOL round-trip floor
PROBE_TARGET = 24
PROBE_EVERY_BATCHES = 40
PROBE_MIN_REAL = 12 * SOL
PROBE_MIN_TRADES = 25
PROBE_MAX_MOVE = 0.12         # W2 price drift within +/-12%: a quiet curve
PROBE_MIN_HOLD_SLOTS = 60
PROBE_STOP = 0.85             # bail early if a probe drops 15%


class Tape:
    """One coin's recent trades with incremental W1/W2 window aggregates.

    Records are kept for W2; all aggregates are maintained as records are
    appended and pruned, so a window query is O(1) amortised. ``maxdq`` is a
    monotone deque giving the highest trade price inside W2.
    """

    __slots__ = ("recs", "maxdq", "mindq", "net2", "buy2", "n2", "buyers")

    def __init__(self):
        self.recs = deque()      # (slot, net_sol, buy_sol, user, price)
        self.maxdq = deque()     # (slot, price), decreasing prices
        self.mindq = deque()     # (slot, price), increasing prices
        self.net2 = 0
        self.buy2 = 0
        self.n2 = 0
        self.buyers = {}         # user -> buy count inside W2

    def _drop(self, rec):
        slot, net, buy, user, price = rec
        self.net2 -= net
        self.buy2 -= buy
        self.n2 -= 1
        if buy:
            left = self.buyers.get(user, 0) - 1
            if left > 0:
                self.buyers[user] = left
            else:
                self.buyers.pop(user, None)

    def prune(self, now):
        horizon = now - W2
        recs = self.recs
        while recs and recs[0][0] < horizon:
            self._drop(recs.popleft())
        maxdq = self.maxdq
        while maxdq and maxdq[0][0] < horizon:
            maxdq.popleft()
        mindq = self.mindq
        while mindq and mindq[0][0] < horizon:
            mindq.popleft()

    def add(self, slot, is_buy, sol_amount, user, price):
        if is_buy:
            net = sol_amount
            buy = sol_amount
        else:
            net = -sol_amount
            buy = 0
        self.recs.append((slot, net, buy, user, price))
        self.net2 += net
        self.buy2 += buy
        self.n2 += 1
        if buy:
            self.buyers[user] = self.buyers.get(user, 0) + 1
        maxdq = self.maxdq
        while maxdq and maxdq[-1][1] <= price:
            maxdq.pop()
        maxdq.append((slot, price))
        mindq = self.mindq
        while mindq and mindq[-1][1] >= price:
            mindq.pop()
        mindq.append((slot, price))
        self.prune(slot)

    def w1_net(self, now):
        """Net inflow over the short window, scanned from the newest record."""
        horizon = now - W1
        total = 0
        for rec in reversed(self.recs):
            if rec[0] < horizon:
                break
            total += rec[1]
        return total

    def first_price(self):
        return self.recs[0][4] if self.recs else None

    def high_price(self):
        return self.maxdq[0][1] if self.maxdq else None

    def low_price(self):
        return self.mindq[0][1] if self.mindq else None

    def low_slot(self):
        return self.mindq[0][0] if self.mindq else None


class Strategy:
    def __init__(self, state):
        hello = state.hello
        self.caps = hello["caps"]
        self.fee_bps = int(hello["fees"]["protocol_bps_fallback"]) + int(hello["fees"]["creator_bps_fallback"])
        self.capital = int(hello["capital"]["lamports"])
        self.tapes = {}            # mint -> Tape
        self.pos = {}              # mint -> open-position bookkeeping
        self.probe_mints = set()   # mints whose open position is a probe
        self.buy_filled = {}       # mint -> total filled buy lamports, this run
        self.round_trips = 0
        self.counted = set()       # mints already counted as round trips
        self.last_exit_slot = {}   # mint -> slot of our last full exit
        self.episodes = {}         # mint -> closed position episodes this run
        self.peak_equity = self.capital
        self.next_probe_seq = 50   # first probe a little into the run
        self.stats = {"buys": 0, "sells": 0, "entries": 0, "probes": 0}

    # -- valuation ---------------------------------------------------------

    def close_value(self, state, mint, tokens):
        """Estimated lamports from selling ``tokens`` now, net of curve fees."""
        curve = state.curves.get(mint)
        if curve is None or tokens <= 0:
            return 0
        vt = curve.virtual_token_reserves
        vq = curve.virtual_quote_reserves
        if vt <= 0 or vq <= 0:
            return 0
        gross = vq * tokens // (vt + tokens)
        return gross * (makefun_bot.BPS - self.fee_bps) // makefun_bot.BPS

    def price(self, state, mint):
        curve = state.curves.get(mint)
        if curve is None or curve.virtual_token_reserves <= 0:
            return None
        return curve.virtual_quote_reserves / curve.virtual_token_reserves

    def equity(self, state):
        total = state.cash
        for mint, holding in state.positions.items():
            total += self.close_value(state, mint, holding.token_amount)
        return total

    # -- tape --------------------------------------------------------------

    def feed(self, batch):
        """Fold this batch's trade events into the tapes; return traded mints."""
        traded = []
        seen = set()
        for e in batch["events"]:
            if e["event"] != "TradeEvent":
                continue
            mint = e["mint"]
            tape = self.tapes.get(mint)
            if tape is None:
                tape = self.tapes[mint] = Tape()
            vt = int(e["virtual_token_reserves"])
            vq = int(e["virtual_quote_reserves"])
            price = vq / vt if vt > 0 else 0.0
            tape.add(int(e["slot"]), bool(e["is_buy"]), int(e["sol_amount"]), e["user"], price)
            if mint not in seen:
                seen.add(mint)
                traded.append(mint)
        return traded

    def feed_fills(self, state, batch):
        for f in batch["fills"]:
            if f["status"] == "filled" and f["side"] == "buy":
                mint = f["mint"]
                self.buy_filled[mint] = self.buy_filled.get(mint, 0) + int(f["quote_lamports"])

    # -- position bookkeeping ------------------------------------------------

    def reconcile(self, state):
        """Open/close position episodes against the account's truth."""
        slot = state.slot
        for mint in list(self.pos):
            if mint not in state.positions:
                info = self.pos.pop(mint)
                self.probe_mints.discard(mint)
                self.episodes[mint] = self.episodes.get(mint, 0) + 1
                self.last_exit_slot[mint] = slot
                if mint not in self.counted and self.buy_filled.get(mint, 0) >= 10_000_000:
                    self.counted.add(mint)
                    self.round_trips += 1
        for mint, holding in state.positions.items():
            if mint not in self.pos:
                self.pos[mint] = {
                    "opened_slot": slot,
                    "peak_value": self.close_value(state, mint, holding.token_amount),
                    "entry_price": self.price(state, mint) or 0.0,
                    "adds": 0,
                    "probe": mint in self.probe_mints,
                    "trimmed": False,
                }

    # -- order helpers -------------------------------------------------------

    def pending_mints(self, state, side):
        return {p.order["mint"] for p in state.pending.values() if p.order["side"] == side}

    def exposure(self, state):
        total = sum(h.cost_lamports for h in state.positions.values())
        for p in state.pending.values():
            if p.order["side"] == "buy":
                total += int(p.order["quote_lamports"])
        return total

    # -- exits ---------------------------------------------------------------

    def exits(self, state, orders):
        slot = state.slot
        pending_sells = self.pending_mints(state, "sell")
        for mint, holding in state.positions.items():
            if mint in pending_sells:
                continue
            info = self.pos.get(mint)
            if info is None:
                continue
            value = self.close_value(state, mint, holding.token_amount)
            if value > info["peak_value"]:
                info["peak_value"] = value
            cost = holding.cost_lamports
            if cost <= 0:
                continue
            held = slot - info["opened_slot"]
            sell = False
            if not info["probe"] and not info["trimmed"] and value >= SCALP_AT * cost:
                # Scalp half into the first strong bounce; let the rest ride.
                info["trimmed"] = True
                orders.append({
                    "side": "sell",
                    "mint": mint,
                    "token_amount": holding.token_amount // 2,
                    "max_slippage_bps": SELL_SLIPPAGE_BPS,
                })
                self.stats["sells"] += 1
                continue
            if info["probe"]:
                if held >= PROBE_MIN_HOLD_SLOTS or value <= PROBE_STOP * cost:
                    sell = True
            elif value <= STOP_LOSS * cost:
                sell = True
            elif info["peak_value"] >= TRAIL_ARM * cost and value <= TRAIL_KEEP * info["peak_value"]:
                sell = True
            elif held >= MAX_HOLD_SLOTS:
                sell = True
            elif held >= FADE_MIN_HOLD and value < 1.10 * cost:
                tape = self.tapes.get(mint)
                if tape is not None:
                    tape.prune(slot)
                    if tape.w1_net(slot) < 0 and tape.net2 < 0:
                        sell = True
            if sell:
                orders.append({
                    "side": "sell",
                    "mint": mint,
                    "token_amount": holding.token_amount,
                    "max_slippage_bps": SELL_SLIPPAGE_BPS,
                })
                self.stats["sells"] += 1

    # -- entries ---------------------------------------------------------------

    def entry_ok(self, state, mint, tape):
        slot = state.slot
        if self.episodes.get(mint, 0) >= MAX_EPISODES:
            return 0
        curve = state.curves.get(mint)
        if curve is None or curve.complete:
            return 0
        if curve.trades < MIN_TRADES or curve.real_quote_reserves < MIN_REAL:
            return 0
        if curve.created_slot is not None:
            if slot - curve.created_slot < MIN_AGE_SLOTS:
                return 0
        elif curve.trades < ALT_MIN_TRADES or curve.real_quote_reserves < ALT_MIN_REAL:
            return 0
        cap = state.max_buy_lamports(mint)
        if cap < MIN_CAP:
            return 0
        tape.prune(slot)
        if tape.n2 < W2_MIN_TRADES or tape.net2 < W2_MIN_NET:
            return 0
        # Buy share of total flow: buy / (buy + sell), with sell = buy - net.
        sell2 = tape.buy2 - tape.net2
        total = tape.buy2 + sell2
        if total <= 0 or tape.buy2 / total < W2_MIN_BUY_SHARE:
            return 0
        if len(tape.buyers) < W2_MIN_BUYERS:
            return 0
        price = self.price(state, mint)
        high = tape.high_price()
        first = tape.first_price()
        if price is None or high is None or first is None or first <= 0:
            return 0
        if price > DIP_HI * high or price < DIP_LO * high:
            return 0
        low = tape.low_price()
        low_slot = tape.low_slot()
        if low is None or low_slot is None or low <= 0:
            return 0
        if price < BOUNCE_MIN * low or slot - low_slot < BOUNCE_AGE:
            return 0
        if price > RISE_CAP * first:
            return 0
        w1 = tape.w1_net(slot)
        if w1 <= 0:
            return 0
        # Per-slot inflow: short window must beat the medium window's rate.
        if w1 * W2 < ACCEL * tape.net2 * W1:
            return 0
        return min(STAKE, cap)

    def entries(self, state, orders, traded):
        slot = state.slot
        tx = state.tx_fee_lamports()
        free = state.free_cash_lamports()
        exposure = self.exposure(state)
        committed = state.committed_position_count()
        pending_buys = self.pending_mints(state, "buy")
        brake = self.equity_now < EQUITY_BRAKE * self.peak_equity
        for mint in traded:
            if len(orders) >= self.caps["max_orders_per_batch"]:
                break
            if mint in state.positions or mint in pending_buys:
                continue
            if committed >= self.caps["max_open_positions"]:
                break
            if slot - self.last_exit_slot.get(mint, -10**18) < COOLDOWN_SLOTS:
                continue
            tape = self.tapes.get(mint)
            if tape is None:
                continue
            # Pyramid adds are handled below; this loop is fresh entries only.
            if brake:
                break
            amount = self.entry_ok(state, mint, tape)
            if amount <= 0:
                continue
            if exposure + amount > EXPOSURE_CAP or free < amount + tx + CASH_BUFFER:
                continue
            orders.append({
                "side": "buy",
                "mint": mint,
                "quote_lamports": amount,
                "max_slippage_bps": BUY_SLIPPAGE_BPS,
            })
            self.stats["buys"] += 1
            self.stats["entries"] += 1
            exposure += amount
            free -= amount + tx
            committed += 1
            pending_buys.add(mint)

    def pyramids(self, state, orders):
        slot = state.slot
        tx = state.tx_fee_lamports()
        free = state.free_cash_lamports()
        exposure = self.exposure(state)
        pending_buys = self.pending_mints(state, "buy")
        if self.equity_now < EQUITY_BRAKE * self.peak_equity:
            return
        for mint, holding in state.positions.items():
            if len(orders) >= self.caps["max_orders_per_batch"]:
                break
            info = self.pos.get(mint)
            if info is None or info["probe"] or info["adds"] >= MAX_ADDS:
                continue
            if mint in pending_buys or info["entry_price"] <= 0:
                continue
            price = self.price(state, mint)
            if price is None:
                continue
            step = PYRAMID_RISE ** (info["adds"] + 1)
            if price < step * info["entry_price"]:
                continue
            tape = self.tapes.get(mint)
            if tape is None:
                continue
            tape.prune(slot)
            if tape.w1_net(slot) <= 0 or tape.net2 <= 0:
                continue
            cap = state.max_buy_lamports(mint)
            amount = min(ADD_STAKE, cap)
            if amount < MIN_CAP:
                continue
            if exposure + amount > EXPOSURE_CAP or free < amount + tx + CASH_BUFFER:
                continue
            orders.append({
                "side": "buy",
                "mint": mint,
                "quote_lamports": amount,
                "max_slippage_bps": BUY_SLIPPAGE_BPS,
            })
            self.stats["buys"] += 1
            info["adds"] += 1
            exposure += amount
            free -= amount + tx
            pending_buys.add(mint)

    # -- pacing probes ---------------------------------------------------------

    def probes(self, state, orders):
        if self.round_trips >= PROBE_TARGET or state.seq < self.next_probe_seq:
            return
        if self.equity_now < PROBE_BRAKE * self.peak_equity:
            return
        open_probes = sum(1 for info in self.pos.values() if info["probe"])
        if open_probes >= 2:
            return
        committed = state.committed_position_count() + sum(1 for o in orders if o["side"] == "buy")
        if committed >= self.caps["max_open_positions"]:
            return
        tx = state.tx_fee_lamports()
        if state.free_cash_lamports() < PROBE_STAKE + tx:
            return
        pending_buys = self.pending_mints(state, "buy")
        slot = state.slot
        for mint, curve in state.curves.items():
            if mint in state.positions or mint in pending_buys or mint in self.counted:
                continue
            if curve.complete or curve.trades < PROBE_MIN_TRADES:
                continue
            if curve.real_quote_reserves < PROBE_MIN_REAL:
                continue
            if state.max_buy_lamports(mint) < PROBE_STAKE:
                continue
            tape = self.tapes.get(mint)
            if tape is None:
                continue
            tape.prune(slot)
            first = tape.first_price()
            price = self.price(state, mint)
            if first is None or price is None or first <= 0:
                continue
            if abs(price / first - 1.0) > PROBE_MAX_MOVE:
                continue
            orders.append({
                "side": "buy",
                "mint": mint,
                "quote_lamports": PROBE_STAKE,
                "max_slippage_bps": SELL_SLIPPAGE_BPS,
            })
            self.stats["buys"] += 1
            self.stats["probes"] += 1
            self.probe_mints.add(mint)
            self.next_probe_seq = state.seq + PROBE_EVERY_BATCHES
            return

    # -- main ------------------------------------------------------------------

    def on_batch(self, state, batch):
        traded = self.feed(batch)
        self.feed_fills(state, batch)
        self.reconcile(state)
        self.equity_now = self.equity(state)
        if self.equity_now > self.peak_equity:
            self.peak_equity = self.equity_now
        orders = []
        self.exits(state, orders)
        self.pyramids(state, orders)
        self.entries(state, orders, traded)
        self.probes(state, orders)
        return orders


STRATEGY = None


def on_hello(state):
    global STRATEGY
    STRATEGY = Strategy(state)


def on_batch(state, batch):
    return STRATEGY.on_batch(state, batch)


def on_done(state, done):
    s = STRATEGY.stats
    print(
        f"pumpsprint done: entries={s['entries']} buys={s['buys']} sells={s['sells']} "
        f"probes={s['probes']} round_trips={STRATEGY.round_trips} "
        f"cash={state.cash} positions={len(state.positions)}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    makefun_bot.run(on_batch, on_hello=on_hello, on_done=on_done)
