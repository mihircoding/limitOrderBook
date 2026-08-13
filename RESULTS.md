# Results

All 17 tests pass (`python -m pytest -q`). Numbers below are `python run_simulation.py`:
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

## What isn't modeled

- One symbol, one venue. No routing, no NBBO, no Reg NMS.
- Limit and market orders only — no stops, icebergs, IOC/FOK, pegged, or auction orders.
- No opening/closing auction, which is where a large share of real volume actually trades, under
  entirely different rules.
- No latency, so nothing in this project touches the actual subject of low-latency trading.
- No fees, rebates, self-trade prevention, or risk checks.
- Agents have no memory, no inventory, and no information — which is exactly what makes it a
  valid null model, and exactly what makes the depth magnitudes wrong.
