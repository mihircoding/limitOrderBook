# Interview notes — limit order books and microstructure

This project maps onto two different interview tracks. For **HFT and market-making** firms it's
domain knowledge, and they will go deep. For **general quant dev** roles it's a data-structures
and systems-design question wearing a finance costume — and it's a very good one, because the
right answer depends on the access pattern.

---

## The 60-second version

> I built a price-time priority matching engine — the core of an exchange — with limit orders,
> market orders, partial fills and cancels, and one shared matching loop so market and limit
> orders can't drift apart. Then I ran 50,000 zero-intelligence events through it: agents that
> flip coins with no strategy at all. The book produced a realistic spread distribution, concave
> price impact at size^0.95, and a mid that mean-reverts with a Hurst exponent of 0.32. None of
> that was programmed in. The useful conclusion is negative: if a book full of coin-flippers
> reproduces your stylized fact, the fact isn't evidence of trader behavior. And the tick-level
> mean reversion is about twenty times smaller than the spread, so it's predictable and not
> tradable.

---

## Mechanics you must have cold

### Price-time priority

1. **Price first** — better prices fill first, regardless of arrival time.
2. **Time second** — FIFO among orders at the same price.

**Why does this matter economically?** Because queue position is an asset. If 5,000 shares sit
ahead of you at the best bid and only 3,000 trade there before the price moves, you don't get
filled at all. Market makers work hard to get to the front and stay there, and most cancels in
real markets are repositioning rather than changes of mind.

Know the alternatives, and that price-time is not universal:
- **Pro-rata** — fills split proportionally to size across all orders at the level. Common in
  short-dated interest rate futures. Incentivizes posting *large*, which inflates displayed size
  well beyond real intent.
- **Price-size-time**, **size-pro-rata with a time priority top order** — hybrids used to blunt
  the gaming each pure rule invites.

### Maker vs taker

The resting order is the **maker** (provided liquidity); the incoming order is the **taker**
(consumed it). **Trades print at the maker's price**, so the taker gets price improvement if they
were willing to cross further.

The fee model follows: makers often earn a rebate, takers pay a fee. This is why "who is the
maker" is not an accounting detail — it's the venue's whole incentive structure, and maker-taker
vs taker-maker vs flat pricing is an actual competitive dimension between exchanges.

Note the subtlety worth volunteering: maker/taker is a property of **each fill**, not of an
order. A 500-share buy that consumes 300 resting shares and then rests 200 was a taker for the
first fill and a maker for whatever hits it next.

### The partial-fill scenario

Be able to walk this without hesitating:

```
Book:     SELL 300 @ 101.10
Incoming: BUY  500 @ 101.10
Result:   300 trade at 101.10; ask level empty;
          leftover 200 rests as the new best BID at 101.10
```

### Market order = limit order without the price check

Both go through one matching loop; only the price condition differs. Say this — it demonstrates
you factored the code rather than copy-pasting the loop, and duplicated matching logic is a
genuine source of production incidents when the two paths diverge under maintenance.

---

## The systems-design questions

### "What data structure would you use, and why?"

The answer they want is *"it depends on the access pattern"*, followed by the pattern.

| Operation | Frequency in real markets | Needs to be |
|---|---|---|
| Cancel | **>90% of all orders** | O(1) |
| Add resting order | very high | O(1) |
| Best bid/ask lookup | every event | O(1) |
| Match at the touch | high | O(1) per fill |
| Deep book scan | rare | can be slow |

So the design is driven by cancels and best-price lookup, not by matching.

**What I built:** `dict[price → deque[Order]]` plus an id→order dict. Best price is
`max(bids)` — O(number of price levels). Fine at this scale, honest about not being O(1).

**What production does**, in rough order of sophistication:

1. **Array indexed by tick.** Prices are discrete, so make the price *be* the index. Best bid is
   a pointer that walks up or down a few slots — effectively O(1), perfectly cache-friendly,
   which matters more than complexity class at these latencies. Costs memory proportional to the
   price range, which is why it's used for instruments with bounded, dense price grids.
2. **Intrusive doubly-linked list per level**, with the id→node map pointing at nodes rather than
   orders. Cancel becomes true O(1) — unlink, no search.
3. **Lazy deletion.** Flag the order dead and skip it when it surfaces at the front of the queue.
   O(1) cancel, at the cost of memory and a periodic compaction. Given the >90% cancel rate,
   this is usually the right trade.

**Never a heap.** This is the trap, because "priority queue" pattern-matches to "heap". A heap
gives O(log n) push/pop but cancelling an arbitrary interior element is O(n) unless you maintain
a separate index, and the FIFO tie-break at a price level needs auxiliary state anyway. Given
the access pattern, a heap is worse than the naive dict on the operation that actually dominates.

### "Why integer ticks instead of floats?"

`0.1 + 0.2 != 0.3`. If prices are dict keys or equality-compared, a float that's one ULP off
silently creates a phantom price level that nothing will ever match against. Production engines
store integer minor units — ticks or cents — and never touch a float in the matching path.

I use `to_tick()` rounding here as a readable compromise and the code says so. Volunteering the
limitation is better than being caught by it.

### "How would you make it fast?"

Progression from obvious to real:

1. Fix the algorithmic complexity — O(1) best price, O(1) cancel (above).
2. **Avoid allocation in the hot path.** Pre-allocate order objects in a pool; a GC pause or a
   malloc in the matching loop is a latency spike.
3. **Cache locality.** Contiguous arrays over pointer-chasing. At these timescales, cache misses
   dominate instruction counts.
4. **Kernel bypass** for the network path (Solarflare/Onload, DPDK) — often a bigger win than
   anything in the book itself.
5. **FPGA** for the truly latency-critical path. Firms do put matching and risk checks in
   hardware.
6. Measure first. The right answer to "how would you optimize this" always starts with "profile
   it", and the honest version of my own book is that `max(self.bids)` is the obvious first
   target and I'd want a benchmark before assuming it's the bottleneck.

### "Single-threaded or concurrent?"

Real matching engines are typically **single-threaded per symbol**, deliberately. Matching must
be deterministic and sequentially consistent — the sequence of events *is* the audit trail, and
"who was first in the queue" has legal meaning. Locking gives that up for throughput you don't
need, because you scale by **sharding across symbols** instead, which is embarrassingly parallel.
LMAX Disruptor is the canonical write-up of this argument.

---

## Microstructure concepts

### What determines the spread?

The classical decomposition, in three parts:

1. **Order processing costs** — the cost of running the operation. Small and shrinking.
2. **Inventory risk** — a market maker who accumulates a position is exposed to it, and widens
   to compensate.
3. **Adverse selection** — the big one. Some of the flow you trade against knows something. You
   quote both sides; the informed only take the side that's about to be right. The spread is the
   fee the uninformed pay to cover the maker's losses to the informed.

**Glosten-Milgrom** formalizes 3: even a risk-neutral, zero-cost, perfectly competitive market
maker must quote a spread, purely because trades carry information. **Kyle (1985)** models the
informed trader optimizing execution against a market maker who can't distinguish them, and
gives you lambda — price impact per unit of order flow.

Then say what my simulation shows: **none of these mechanisms are in my model, and a spread
appeared anyway.** Zero-intelligence agents produced a mean spread of 0.0198 with a hard floor at
the tick. The mechanical component — depth being consumed faster than it's replaced — is real and
separable from the informational one. That's a genuinely useful thing to have measured.

### Price impact

**Temporary vs permanent** is the distinction to lead with. Temporary impact is you consuming
liquidity and the book refilling behind you. Permanent impact is the market inferring
information from your trade and repricing for good. Execution algorithms are entirely about
minimizing the first without leaking enough to cause the second.

**Square-root law**: impact ≈ `Y · σ · sqrt(Q / V)` — volatility times the square root of order
size over daily volume. Remarkably robust across markets and decades. Concave, so slicing a
large order helps; the concavity is why VWAP/TWAP/implementation-shortfall algorithms exist.

My measurement: impact ∝ size^0.95, which is concave but much less so than the empirical 0.5.
Be ready to explain the gap — real liquidity replenishes strategically after a large trade and
real large orders are worked in slices, neither of which this model has. The *mechanism*
producing concavity (a hump-shaped depth profile) is the same; the strength depends on behavior.

### "Is the price a random walk?"

At long horizons, close to it — that's efficient markets, and any strong autocorrelation would
be arbitraged away.

At **tick level, no**, and I have the numbers: lag-1 autocorrelation −0.044, variance scaling
k^0.64 (Hurst 0.32) against 1.0 for a true random walk. The mid mean-reverts.

Two mechanical causes:
1. **Bid-ask bounce** — the mid ticks up when the ask is consumed and down when the bid is, and
   those alternate roughly at random.
2. **Depth asymmetry** — a buy that lifts the touch leaves a thin ask and a fat bid, which biases
   the next move back down.

**Then deliver the punchline, because this is what separates a good answer from a great one:**
you cannot trade the mid. You buy at the ask and sell at the bid. The mean reversion I measured
is ~0.001 per event against a mean spread of 0.0198 — **the signal is twenty times smaller than
the cost of acting on it.** Predictability is not profitability, and short-horizon microstructure
is where that confusion is most expensive.

Corollary worth saying: if an alpha model finds mean reversion at the tick level, it has probably
rediscovered bid-ask bounce. Test against a zero-intelligence null first.

### Adverse selection, concretely

If you're quoting both sides and someone lifts your offer, ask why. Uninformed flow is
profitable to trade against; informed flow isn't. **Order flow toxicity** is the measurement of
this (VPIN is one attempt), and it's why market makers widen or pull quotes around news and
around large directional flow. It's also the honest answer to "why does the spread widen in a
crash" — it isn't panic, it's that the ratio of informed to uninformed flow spiked.

---

## Questions I'd expect

**"Walk me through what happens when a marketable buy limit arrives."**
Wrap it in an Order with an id and sequence number. Look up the best ask. While the order has
quantity left and its limit price is at or above the best ask: take the front order from that
level's FIFO queue, fill `min(remaining, resting.quantity)`, print a trade at the *resting*
order's price, decrement both, and if the resting order hits zero pop it and clean up the level
if it's now empty. When the loop ends — either filled out or no longer crossing — rest any
remainder at its own limit price.

**"Where would this break under load?"**
`max(self.bids)` on every quote lookup is O(levels), and quote lookups happen on every event.
That's the first thing I'd profile. After that, cancels doing an O(level size) `deque.remove()`
— which matters much more in reality than my simulation shows, because real markets cancel over
90% of orders.

**"How do you test a matching engine?"**
Scenario tests as the spec — each rule of the algorithm as an executable case (maker pricing,
FIFO at a level, sweeping levels in price order, remainder resting, cancels preserving queue
position). Then property-based tests over random event sequences with invariants that must
always hold: best bid < best ask, total resting quantity equals submitted minus filled minus
cancelled, no trade prints outside the crossing range. Then replay against captured production
data if you have it — the real test is a byte-identical trade log.

**"What's missing from your book compared to a real exchange?"**
Auctions (a large share of daily volume trades at the open and close under different rules —
a single clearing price, not continuous matching), order types beyond limit/market, self-trade
prevention, fees, risk checks, multi-venue routing and NBBO obligations, and latency — which
erases the entire subject of low-latency trading from the model.

**"Why do >90% of orders get cancelled?"**
Mostly repositioning, not indecision. Market makers reprice as the mid moves, chase queue
position, and pull when the flow looks toxic. Some is genuine strategy churn and a small amount
is gaming. The design consequence is what matters: cancel is the hot path, so the data structure
should be chosen to make cancel O(1) even at the cost of memory.

---

## Things to admit before they ask

- Best-price lookup is O(levels), not O(1). I know the fixes and haven't profiled to justify one.
- Ticked floats, not integer ticks. I know why that's wrong in production.
- No latency model at all, so nothing here is about low-latency trading despite the topic.
- The depth *magnitudes* are an artifact: zero-intelligence agents never reprice, so passive
  orders pile up far from the touch in a way no real book does. The shape is emergent; the size
  isn't.
- Impact exponent 0.95 vs the empirical ~0.5. The mechanism is right, the strength isn't.
- Single symbol, no auctions, no fees, no order types beyond the two basic ones.
