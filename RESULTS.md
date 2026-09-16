# Results

All 69 tests pass (`python -m pytest -q`). Numbers below are `python run_simulation.py`:
50,000 events, seed 7, book seeded with 5 levels of 100 shares either side of 100.00.

```
Trades executed : 35,128
Volume traded   : 1,850,207
Mid             : 100.00 -> 100.08  (range 99.94 - 100.15)
```

**Nothing below was programmed in.** The simulator specifies an event mix (60% limit, 25%
market, 15% cancel), a random side, and a random price offset. It never specifies a spread, a
depth profile, an impact function, or a price process. Everything that follows is a consequence
of price-time priority matching.

---

## 1. Spread

| | |
|---|---|
| mean | 0.0198 |
| median | 0.0200 |
| min | 0.0100 (one tick) |
| max | 0.1300 |
| share of time at the 1-tick minimum | **37.9%** |

The spread is an emergent equilibrium between two forces the simulator *does* specify: market
orders eat depth at the touch, and passive limit orders replace it. When consumption outruns
replacement the touch empties and the spread widens; when replacement wins, someone posts inside
and it collapses back to one tick.

This is worth sitting with, because it's the whole argument for building the null model. If you
had only ever seen the output you might reach for an explanation involving market makers
managing inventory risk and adverse selection. There are no market makers here. There is no
inventory. There is no information. A distribution of spreads with a hard floor at the tick and
a long right tail is what a matching engine does when you feed it noise.

## 2. Depth profile (final book)

| Level | Bid px | Bid qty | Ask px | Ask qty |
|---|---|---|---|---|
| 1 | 100.07 | 940 | 100.10 | 739 |
| 2 | 100.06 | 5,867 | 100.11 | 6,772 |
| 3 | 100.05 | 43,400 | 100.12 | 102,217 |
| 4 | 100.04 | 76,773 | 100.13 | 74,866 |
| 5 | 100.03 | 64,406 | 100.14 | 47,915 |
| 6 | 100.02 | 35,342 | 100.15 | 20,416 |
| 7 | 100.01 | 19,458 | 100.16 | 12,214 |
| 8 | 100.00 | 11,387 | 100.17 | 6,875 |

Depth is thin at the touch and hump-shaped a few ticks out. Real books have this shape too.

**But this is the least realistic output here, and the reason is instructive.** Zero-intelligence
agents never reprice. A real trader who posts three ticks away and watches the market leave
pulls the order and reposts; here it sits forever until a random cancel happens to select it.
So liquidity accumulates far from the mid at levels that would never survive in a real book —
100,000 shares three ticks out is not a market, it's an artifact of agents with no memory.

The honest summary: the *shape* is emergent, the *magnitude* is an artifact. Distinguishing
those two is the entire skill in reading a simulation.

## 3. Price impact

| Market order size | Mean \|mid move\| | n |
|---|---|---|
| 1–50 | 0.000407 | 2,640 |
| 51–100 | 0.001099 | 3,321 |
| 101–150 | 0.001840 | 3,346 |
| 151–200 | 0.002573 | 3,284 |

Power-law fit: **impact ∝ size^0.95**.

Bigger orders move the price more — obviously — but **sublinearly**. Doubling the order size
costs less than double the impact. That concavity is one of the most robust empirical facts in
market microstructure, and it emerges here from nothing but walking a book with a hump-shaped
depth profile: the deeper you go, the more size sits at each successive level.

Real markets sit closer to **square-root impact** (exponent ≈ 0.5), which is much more concave
than 0.95. The gap is the depth profile again: real liquidity replenishes strategically after a
large trade, and real large orders are worked in slices rather than fired at once. The mechanism
producing concavity is the same; the strength of it depends on behavior this model doesn't have.

## 4. Is the mid a random walk?

Lag-1 autocorrelation of mid changes: **−0.0440**.

| Horizon k | var(mid₍t+k₎ − mid₍t₎) | Ratio to k=1 | Random walk would be |
|---|---|---|---|
| 1 | 0.000010 | 1.00 | 1.00 |
| 2 | 0.000019 | 1.91 | 2.00 |
| 4 | 0.000034 | 3.45 | 4.00 |
| 8 | 0.000057 | 5.77 | 8.00 |
| 16 | 0.000087 | 8.89 | 16.00 |
| 32 | 0.000128 | 13.00 | 32.00 |
| 64 | 0.000172 | 17.57 | 64.00 |
| 128 | 0.000220 | 22.46 | 128.00 |

Fitted scaling: **var ∝ k^0.64**, i.e. **Hurst exponent H = 0.32** against 0.50 for a true
random walk.

A random walk's variance grows linearly in the horizon — double the time, double the variance.
This doesn't. At k = 128 the variance is 22× the one-step variance, not 128×. Combined with the
negative lag-1 autocorrelation, the mid is **sub-diffusive: it mean-reverts at short horizons.**

Two mechanisms, both mechanical:

1. **Bid-ask bounce.** The mid ticks up when the best ask is consumed and down when the best bid
   is, and those events alternate roughly at random. That alternation is negative
   autocorrelation by construction.
2. **The restoring force of resting depth.** A market buy that lifts the touch leaves a *thinner*
   ask and a *fatter* bid behind it. The book is now asymmetric in a way that makes the next move
   more likely to go back down. Depth pushes back.

**This is the most useful result in the project, and it is a warning.** Real tick data shows the
same signature, and a naive reading says "the mid mean-reverts, therefore buy dips at the tick
level." But you cannot trade the mid — you buy at the ask and sell at the bid. The mean reversion
here is 0.001-ish per event against a mean spread of 0.0198. **The signal is roughly twenty
times smaller than the cost of acting on it.** Predictability and profitability are different
things, and short-horizon microstructure is where people most reliably confuse them.

It also means: if your alpha model finds mean reversion at the tick level, you have probably
rediscovered bid-ask bounce. Test against a zero-intelligence null before believing otherwise.

---

## Correctness

The matching rules are tested as scenarios rather than as functions — `tests/test_orderbook.py`
reads as the specification:

- Marketable limit orders fill at the **maker's** price, not the taker's limit.
- FIFO within a price level: two 100-share sells at 101.00, a 150-share buy arrives, and the
  fills are 100 from the first and 50 from the second, in that order.
- Price priority beats time priority across levels: a better ask that arrives *later* still
  fills first.
- Partial fill remainder rests as the new best on the opposite side.
- Matching stops when the price no longer crosses — a 300-share buy at 100.00 against asks at
  100.00 and 102.00 fills 100 and rests 200.
- Cancels preserve the queue position of everything else at the level.
- A filled order cannot be cancelled.

## 5. What it costs to run

Everything above is about what the book *produces*. This is about what it costs, which is the
other half of the reason exchanges are interesting: a matching engine is a latency-sensitive
piece of infrastructure before it is a source of stylized facts.

`benchmark.py` sweeps book depth and times two implementations in the same process — the
current one, and `ScanBook`, which overrides exactly one method to restore the previous
`max(bids)` / `min(asks)` lookup. Everything else is shared, so any difference is attributable
to best-price lookup alone.

| levels | scan (ev/s) | heap (ev/s) |
|---|---|---|
| 10 | 73,727 | 80,720 |
| 50 | 68,411 | 81,856 |
| 100 | 62,227 | 79,588 |
| 500 | 30,576 | 82,596 |
| 1,000 | 17,492 | 79,948 |

Scanning the price dict costs 4.2x throughput going from 10 to 1,000 levels. The current book
costs nothing measurable over the same range — 80,720 against 79,948, which is inside the noise.
In isolation the lookup itself is 29x faster at 1,000 levels (8,669ns → 302ns).

That right-hand column used to fall off too, by 2.2x across the same sweep. What was left after
the heaps fixed best-price lookup was cancellation, and this is the section where it got found.

### The cancel tail, and what fixed it

Per-operation latency at 500 levels, microseconds, as it stood before:

| operation | n | p50 | p99 | max |
|---|---|---|---|---|
| add | 12,063 | 4.22 | 15.49 | 157.15 |
| cancel | 4,946 | 1.94 | **170.38** | 469.48 |
| market | 2,991 | 13.44 | 43.92 | 704.00 |

Cancel was the fastest median operation in the book and had by far the worst tail — an 88x
p50-to-p99 gap. The cause is one line: `cancel()` found the order in O(1) through `_by_id` and
then called `deque.remove()`, which is O(orders resting at that price level). Cancelling at the
front of a queue was instant; cancelling at the back of a deep one walked the whole queue. The
caller neither chose their queue position nor can see it, so the same API call cost 2μs or 170μs
depending on something invisible — and in real equity flow, well over 90% of orders are cancelled
rather than filled, so this was the hot path wearing the worst distribution.

The fix is the same idea the heaps already used: **don't delete, tombstone**. `cancel()` flags
the order inactive, drops its id, decrements the level's live-order count, and returns. The
order stays physically in its deque until the matching loop reaches it and throws it away, which
is the one moment the work was unavoidable anyway. Per-level live counts (`_bid_live` /
`_ask_live`) are what keep this honest: when the last live order at a price goes, the price key
is deleted in O(1) — so `best_bid()`, `depth()` and the heaps never see a level that nobody is
quoting, and the dropped deque takes its tombstones with it.

Same flow, both implementations timed in the same process (`EagerCancelBook` restores the old
`deque.remove()` and overrides nothing else):

| cancel() | p50 | p99 | max | p99/p50 |
|---|---|---|---|---|
| eager (splice the deque) | 2.09 | 79.62 | 131.81 | 38x |
| lazy (tombstone) | **0.89** | **3.21** | **43.62** | **4x** |

The median halves, which was not the point. The 99th percentile falls by 25x and the ratio
between them goes from 38x to 4x, which was. A matching engine is judged on its tail: a venue
whose cancels are usually fast and occasionally 80μs is a venue that occasionally fails to pull
a quote in time, and the times it fails are exactly the busy ones.

The cost is memory, and rather than assert it stays bounded the benchmark counts it: after
20,000 events at 500 levels the book is holding **1,787 tombstones against 4,065 live orders**,
worst single level 368. They are freed two ways — the matching loop discards them as it passes,
and an emptied level drops its whole deque at once.

`tests/test_lazy_cancel.py` runs the lazy book and the eager one through identical random flow
and compares every trade and both quotes after every event, because an optimization that changes
an answer is not an optimization.

These are single-run numbers from CPython on a shared VM and they wobble 10-20% between runs.
The shape of the curves is the result; the absolute figures are not.

## What isn't modeled

- One symbol, one venue. No routing, no NBBO, no Reg NMS.
- Limit, market, IOC and FOK orders — no stops, icebergs, pegged, or auction orders.
- No opening/closing auction, which is where a large share of real volume actually trades, under
  entirely different rules.
- No latency, so nothing in this project touches the actual subject of low-latency trading.
- No fees or rebates. Self-trade prevention exists now (`participant_id` + `StpPolicy` on every
  order type - see README's Design notes and `tests/test_stp.py`), but no other risk checks
  (position limits, fat-finger checks). The zero-intelligence simulator below still doesn't
  assign participant identities to its agents, so none of the numbers below exercise it.
- Agents have no memory, no inventory, and no information — which is exactly what makes it a
  valid null model, and exactly what makes the depth magnitudes wrong.
