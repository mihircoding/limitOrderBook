# Limit Order Book & Matching Engine

**[Live site &rarr;](https://mihircoding.github.io/limitOrderBook/)** — includes a browser port of the matching engine you can send orders to and watch price-time priority work.

A price-time priority matching engine — the piece of infrastructure that *is* a modern
exchange — plus a zero-intelligence order flow simulator to run through it.

The interesting result is what comes out of the simulator. The agents flip coins; they have no
strategy at all. The book still produces a realistic spread distribution, concave price impact,
and a mid price that mean-reverts at short horizons exactly the way real equity data does. None
of those were programmed in. They're properties of the matching rules.

69 tests. Results in [RESULTS.md](RESULTS.md), interview notes in [INTERVIEW.md](INTERVIEW.md).

```bash
pip install -r requirements.txt
python -m pytest -q          # 69 passed
python run_simulation.py     # 50k events, writes simulation.png
python benchmark.py          # throughput and latency, against the older implementations
```

![Emergent spread, impact and variance scaling](simulation.png)

---

## How a matching engine works

### The book

An order book is two sorted collections of resting limit orders — bids below, asks above — and
the gap between the best of each is the **spread**. Everything else is bookkeeping.

```
         asks (sellers)          Someone willing to sell at 101.00
  101.00 |████ 300              is offering; someone willing to buy
  100.60 |██ 150                at 100.50 is bidding. Nothing trades
  100.50 |█ 100     <- best ask  until one side reaches across.
  ---------------------- spread = 0.05
  100.45 |██ 200    <- best bid
  100.40 |████ 400
  100.30 |███ 250
         bids (buyers)
```

Two order types cover almost everything:

- **Limit order** — "buy 100 at no more than 100.50." Executes immediately against anything
  better; whatever's left *rests* in the book and waits. Provides liquidity.
- **Market order** — "buy 100, whatever it costs." Executes immediately against the best
  available prices, walking down the book if it needs to. Consumes liquidity.

The key realization in the implementation: **these are the same operation.** A market order is a
limit order without the price condition. Both go through one `_match()` loop; the only
difference is whether a price ceiling is checked. Keeping matching in exactly one place is what
stops the two paths from silently disagreeing.

### Price-time priority

The rule almost every equity venue uses to decide who fills first:

1. **Price priority** — better prices execute first. A 100.50 bid always fills before a 100.45
   bid, regardless of who arrived first.
2. **Time priority** — among orders at the same price, first-in fills first (FIFO).

Time priority at a price level is why a `deque` per level is the natural structure: append to
the back on arrival, pop from the front when filling. The queue *is* the priority.

This rule is why **queue position is a tradable asset**. If you're 5,000 shares deep in the
queue at the best bid, and only 3,000 shares trade there before the price moves, you don't get
filled at all. Market makers spend real effort getting to the front and staying there — which is
also why most cancels in real markets aren't changes of mind, they're repositioning.

### Trades print at the maker's price

If a resting sell order at 101.00 meets an incoming buy willing to pay 101.50, the trade prints
at **101.00**. The buyer gets price improvement.

The resting order set its terms first and the incoming order accepted them. This isn't just
convention — the whole **maker/taker** economic model rests on it. The maker (resting, provided
liquidity) often earns a rebate; the taker (crossing, consumed liquidity) pays a fee. Get this
backwards and you've inverted the incentive structure of the entire venue.

### Partial fills

The case worth getting right, because most of the others follow from it:

```
Book:     SELL 300 @ 101.10
Incoming: BUY  500 @ 101.10

Result:   300 trade at 101.10, the ask is fully consumed,
          and the leftover 200 REST as the new best BID at 101.10
```

The incoming order was aggressive enough to clear the level and then became the passive side.
An order's identity as maker or taker is not a property of the order — it's a property of each
individual fill.

### Cancels

`cancel()` does **lazy deletion**, which is what production engines do: flag the order dead,
drop its id, decrement the level's live-order count, return. The order stays physically in its
queue until the matching loop reaches it and throws it away.

It used to splice the order out of its deque immediately, which is O(orders at that price level)
— instant at the front of a queue, a full walk from the back. `benchmark.py` measured that as a
2μs median against a 170μs p99, on the operation that is most of real order flow: in equity
markets **well over 90% of orders are cancelled rather than filled**, so cancel is the hot path,
not fill. Tombstoning takes the p99 to 3μs. The trade is memory, and the level's live count is
what bounds it — when the last live order at a price goes, the price key is deleted and the
deque goes with it, tombstones and all.

---

## The simulator: zero intelligence

Gode & Sunder (1993) showed that "zero-intelligence" traders — submitting random orders with no
strategy whatsoever — reproduce most of a real market's aggregate behavior. The realism lives in
the exchange rules, not in the traders.

The event mix here:

| | Share | What it does |
|---|---|---|
| Limit order | 60% | random side, price offset ~ \|N(0, 3 ticks)\| from the mid; 80% passive, 20% crossing |
| Market order | 25% | random side, size uniform 10–200 |
| Cancel | 15% | a uniformly random resting order |

This is a **null model**, and that's the point of it. Any stylized fact it reproduces needs no
behavioral explanation — you cannot claim your favorite market phenomenon reveals something
about trader psychology if a book full of coin-flippers produces it too.

What it produced over 50,000 events (details and numbers in [RESULTS.md](RESULTS.md)):

- **A spread distribution** — mean 0.0198, at the 1-tick minimum 37.9% of the time. The spread
  is not a parameter anywhere in the code.
- **Concave price impact** — impact ∝ size^0.95. Bigger orders move the mid more, sublinearly.
- **A sub-diffusive mid** — lag-1 autocorrelation of −0.044, and variance growing like k^0.64
  rather than k (Hurst exponent 0.32 against 0.50 for a true random walk). The mid mean-reverts
  at short horizons, which is exactly the signature real tick data shows.

---

## Layout

```
├── run_simulation.py    # driver: spread, depth, impact, variance scaling
├── src/
│   ├── order.py         # Order and Trade types, tick rounding
│   ├── orderbook.py     # the matching engine
│   └── simulator.py     # zero-intelligence order flow
├── benchmark.py         # profiling: throughput vs depth, latency percentiles
└── tests/               # 69 tests, written as matching scenarios
```

`tests/test_orderbook.py` is worth reading as the specification — each test is one rule of the
matching algorithm stated as an executable scenario (maker pricing, FIFO at a level, sweeping
levels in price order, partial fill remainder resting, cancels preserving queue position).

## Design notes

**Prices as ticked floats.** `to_tick()` snaps every price to the 0.01 grid so equal prices
compare equal as dict keys. Real engines store **integer ticks** and never touch a float,
because `0.1 + 0.2 != 0.3` and a price that fails an equality check silently creates a phantom
level. The rounding here is a readable compromise, and the code says so.

**Timestamps as a sequence number.** Time priority needs only an ordering, not a clock. A
monotonically increasing integer is both sufficient and immune to clock skew — which is exactly
what real venues use for sequencing.

**`Order` is mutable, `Trade` is frozen.** An order's remaining quantity changes as it fills;
a trade is a historical fact and must never change after it prints.

**IOC and FOK reuse `_match`, not a copy of it.** `add_ioc_order` is a limit order that skips
`_rest` - same crossing rules, it just discards whatever doesn't fill instead of resting it.
`add_fok_order` is the one order type that has to look before it leaps: `_match` mutates the
book as it walks it, so there's no undoing a partial fill if the size comes up short. It checks
the fillable quantity within the limit price read-only first, and only calls `_match` if that
clears the requested size.

**Self-trade prevention is a look-before-you-cross check, not a cleanup.** Every order can
carry an optional `participant_id` (default `None` - the book crosses two orders from the same
untracked identity exactly like it always did, no behavior change for anyone who doesn't opt in).
Pass a `participant_id` and `_match` refuses to print a trade against a resting order with the
same one, one of two ways (`StpPolicy`): `CANCEL_RESTING` pulls the maker's order and the taker
keeps matching everyone else; `CANCEL_NEWEST` kills the entire incoming order the instant it
would self-match, nothing further fills or rests. The reason this can't be a post-trade check:
once a `Trade` is appended it already happened - a matching engine can't un-print a fill any more
than a real exchange can. FOK needed the most care here, for the same reason it did for the
non-STP case: `_fillable_quantity` has to walk the book in the same price/time priority `_match`
uses and apply the same self-trade rule while counting, or a FOK could fire and then discover
mid-fill that some of what it counted was about to be cancelled instead of traded.

## Performance

`src/orderbook.py` used to carry a comment saying the best-price lookup — `max(bids)` /
`min(asks)` over the price dict — was "fine at this scale," and that a sorted structure was "a
profiling-driven optimization, not where you start." `benchmark.py` is that profiling, and it
disagreed.

The lookup is read on every quote, every pass of the matching loop, and every event the
simulator generates. Scanning the keys is O(price levels), so its cost grows with depth while
everything else in the book stays O(1):

```
 levels    scan (ns)    heap (ns)    speedup
     10          416          303       1.4x
     50          708          307       2.3x
    100         1118          308       3.6x
    500         4531          318      14.2x
   1000         8669          302      28.7x
```

That is visible end to end, not just in a microbenchmark. The same 50,000-event
zero-intelligence run, seeded to different depths:

```
 levels   scan (ev/s)   heap (ev/s)
     10        73,727        80,720
    100        62,227        79,588
   1000        17,492        79,948
```

A book with a thousand price levels is not exotic — that is a liquid name with a penny tick.
Scanning turns it into a 4x slowdown for a data structure that should barely notice.

**The fix:** each side keeps a binary heap of its live price levels alongside the dict, so the
best price is a peek instead of a scan. Deletion is lazy — emptying a level doesn't touch the
heap; the entry is discarded when it surfaces at the top and turns out to be gone — because
removing an arbitrary element from a heap is O(L) and would give back exactly what was just
won. A membership set keeps at most one entry per distinct price, so a level that is created
and destroyed a thousand times can't grow the heap a thousand entries.

`tests/test_best_price.py` runs the heap and the old scan side by side over random order flow
and asserts they agree after every single event, because an optimization that changes an answer
is not an optimization.

**What the benchmark found next, and what it cost to fix.** Cancel had the fastest median in
the book and by far the worst tail — 1.94μs at p50 against 170.38μs at p99, an 88x gap. That is
`deque.remove()` being O(orders at that level): cancelling the order at the front of a deep queue
is instant, cancelling the one at the back walks the whole thing, and the caller can neither see
nor choose which one they are.

Same fix as the heaps, one layer down: tombstone instead of delete. Both versions timed in the
same process, same flow, at 500 levels:

```
cancel()               p50       p99       max   p99/p50
eager (deque)         2.09     79.62    131.81       38x
lazy (tombstone)      0.89      3.21     43.62        4x
```

The median halving is incidental. The p99 falling 25x is the result, and the p99/p50 ratio going
from 38x to 4x is how you'd say it to a venue: a matching engine is judged on its tail, and the
moments it blows out are the busy ones, which are the moments a resting quote most needs pulling.

It also flattened the throughput curve. The heaps left a 2.2x fall-off from 10 to 1,000 levels;
cancel was what remained of it, and the sweep is now flat end to end (80,720 ev/s at 10 levels,
79,948 at 1,000).

The memory is measured rather than assumed: after 20,000 events at 500 levels the book holds
1,787 tombstones against 4,065 live orders, worst single level 368.

Numbers above are one run on one machine (CPython 3.10, shared VM), and they move around by
10-20% between runs. The *shape* is the finding — flat versus linear in depth — not the
absolute nanoseconds.

## Known simplifications

- Single symbol, single venue. No cross-venue routing, no NBBO, no Reg NMS.
- No order types beyond limit, market, IOC, and FOK: no stops, icebergs, pegged, or
  auction-only orders.
- No opening or closing auction — a large share of real daily volume trades in exactly those,
  under different rules (a single clearing price, not continuous matching).
- ~~No self-trade prevention~~ - `participant_id` + `StpPolicy` on every order type (see Design
  notes). Still no fee/rebate model, no other risk checks (position limits, fat-finger checks).
- No latency. Every order arrives instantly and in submission order, which erases the entire
  subject matter of low-latency trading.
- Zero-intelligence agents never reprice, so passive orders pile up far from the touch in a way
  real books don't — the depth profile is the least realistic output here, and RESULTS.md says
  why.

## Reading

- Gode & Sunder (1993), *Allocative Efficiency of Markets with Zero-Intelligence Traders* — the
  original result this simulator reproduces.
- Bouchaud, Bonart, Donier & Gould, *Trades, Quotes and Prices* — the modern reference on
  microstructure and price impact.
- Larry Harris, *Trading and Exchanges* — how venues actually work, institutionally.
- O'Hara, *Market Microstructure Theory* — the adverse-selection models (Glosten-Milgrom, Kyle).
