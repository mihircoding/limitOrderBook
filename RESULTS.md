# Results

All 80 tests pass (`python -m pytest -q`). Numbers below are `python run_simulation.py`
(section 6 is `python latency_study.py`):
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

## 6. What being slow costs, in ticks

The list of things this project doesn't model has always started with latency, with the note
that its absence means nothing here touches the actual subject of low-latency trading. That is
now `src/latency.py` and `python latency_study.py`.

The model rests on one fact about a matching engine: **it does not know when you decided.** It
knows when your packet arrived, and it serves arrivals in order. `MessageBus` is a priority queue
keyed by (arrival, sequence), where arrival is submission time plus a per-participant latency
draw — a hard floor plus an exponential tail, because a symmetric distribution would imply
messages arriving faster than physics allows. The sequence number breaks ties, which is not a
detail: ordering a tie by submission time would hand it to whoever decided first, which is
exactly the advantage latency is supposed to take away.

The experiment is the smallest one containing the real effect. Two market makers quote 100
shares one tick either side of fair value. Identical logic, identical information, identical
size — the only difference between them is how long their messages take to reach the book. Fair
value jumps three ticks 25% of the time; informed flow reacts to the jump in 50µs; uninformed
flow arrives at random. Every fill is marked against fair value immediately afterwards, so one
tick per share means the full quoted edge was captured and nothing was lost to information.

The fast maker is fixed at 10µs. Only the slow one moves.

```
fast maker (10us)
 slow maker | quoted vol  toxic%  passive  took vol  taking     net      total
       10us |    362,766   34.0%    0.779    24,906   1.966   0.855    331,455
       25us |    456,710   21.1%    1.000    72,835   1.991   1.136    601,827
       50us |    451,833   21.3%    1.000    77,671   1.990   1.145    606,482
      100us |    445,532   21.6%    0.999    78,221   1.990   1.147    600,977
      250us |    423,473   22.7%    0.997    79,828   1.993   1.155    581,085
     1000us |    311,305   31.1%    0.983    89,023   1.993   1.207    483,365

slow maker
 slow maker | quoted vol  toxic%  passive  took vol  taking     net      total
       10us |    359,007   33.8%    0.793    26,799   2.005   0.878    338,578
       25us |    283,433   59.7%    0.234         0   0.000   0.234     66,427
       50us |    251,190   30.9%    0.082         0   0.000   0.082     20,579
      100us |    256,869   30.5%    0.102         0   0.000   0.102     26,171
      250us |    276,415   28.9%    0.186         0   0.000   0.186     51,281
     1000us |    373,766   23.8%    0.475       491  -0.754   0.473    176,982
```

`passive` is P&L per share on the maker's own resting quotes; `taking` is P&L per share where it
was the aggressor; `total` is the run's whole P&L in ticks.

**Fifteen microseconds is worth 80% of the business.** The first row is the control: identical
latency, and the two makers split the run almost exactly, 331,455 ticks against 338,578. Move one
of them from 10µs to 25µs and the split becomes 601,827 against 66,427. Nothing else changed —
same flow, same seed, same quoting logic, same size. At 50µs the slow maker keeps 20,579 ticks,
6% of what it earned at parity.

**And the total barely moves.** 670,033 ticks at parity, 668,254 at a 15µs gap. Latency does not
create value here; it decides who collects value that the flow was going to pay either way. That
is the cleanest statement this project can make about why firms spend money on microwave towers,
and it comes out of the accounting rather than from an argument.

**The slow maker does not trade less. It trades worse.** At 25µs it still fills 283,433 shares on
its own quotes — 79% of what it filled on them at parity — and earns 0.234 ticks on each instead of 0.793.
Look at the `toxic%` column on that row: 59.7% of what it traded came from informed flow, against
21.1% for the fast maker on the same run. It is not being excluded from the market. It is being
selected into the half of the market that costs money. A maker looking only at fill rates would
see a healthy business.

**The fast maker's second income stream is the interesting one.** Its `passive` number is 1.000 —
a perfect score, never adversely selected at all, because its cancel always beats the informed
order. But look at `took vol`: 72,835 shares where it was the aggressor, at 1.991 ticks each. Its
own requote is what takes them. After a three-tick jump down, the new ask it posts sits *below*
where the competition's stale bid still is, so posting it crosses. That is latency arbitrage, and
it was not written into the maker's logic — the maker only knows how to cancel and re-post around
fair value. It falls out of being first.

**Why the slow maker's curve flattens and then turns up, and why that isn't good news.** From
25µs onward it is already slower than the pick-off, and being slower still does not make the
pick-off worse: the loss per jump is capped at the size it quoted. Past that point the variation
in the column is not about toxicity at all, it is about queue position for ordinary flow — and at
1000µs the maker is quoting around a fair value a full round stale, which is a different problem
(uncompetitive rather than picked off) that this model's once-per-round value updates do not
represent well. The monotone part of the story is 10µs to 50µs. The tail of the sweep is in the
table because leaving it out would be a nicer chart and a worse result.

### 6b. The venue's fees, which are a fifth of the edge

Everything above trades for free. No exchange does. On US equity venues the maker-taker schedule
pays for a resting fill and charges for taking, and at a top tier that is roughly +$0.0020 and
−$0.0030 a share. The makers here quote one tick — one cent — either side of fair value, so the
rebate is **a fifth of the entire edge they are working with**, and the study is not describing
the business until it is charged. `src/fees.py` charges it, at three schedules: no fees, maker-taker,
and an inverted venue (−$0.0010 maker, +$0.0002 taker) of the kind that pays for aggressive flow.

Whole-run P&L in ticks, trading plus fees:

```
 slow maker |                no fees            maker-taker               inverted
            |       fast        slow       fast        slow       fast        slow
       10us |    331,455     338,578    396,536     402,340    295,676     303,213
       25us |    601,827      66,427    671,318     123,113    557,613      38,083
       50us |    606,482      20,579    673,547      70,817    562,852      -4,540
      100us |    600,977      26,171    666,617      77,545    557,988         484
      250us |    581,085      51,281    641,831     106,564    540,334      23,639
     1000us |    483,365     176,982    518,919     251,588    454,015     139,615
```

**The rebate does not change who wins, and it changes whether the loser has a business.** The fast
maker's advantage at 50µs is the same 30x with fees as without — fees are per share, and both
makers do similar volume, so the subsidy lands on both of them. But the slow maker at 50µs earns
20,579 ticks of trading P&L on 251,190 shares quoted, which is **$0.0008 a share**. A maker-taker
venue paying $0.0020 nearly quadruples that. An inverted venue charging $0.0010 takes more than the
strategy makes, and the run goes negative — the single negative cell in the table.

That number has a name here: `breakeven_maker_rate()` solves for the maker rate at which a run nets
to exactly zero.

```
 slow maker |                         fast |                         slow
            |   trading P&L     break-even |   trading P&L     break-even
       10us |       331,455       -0.0089$ |       338,578       -0.0092$
       25us |       601,827       -0.0127$ |        66,427       -0.0023$
       50us |       606,482       -0.0129$ |        20,579       -0.0008$
      100us |       600,977       -0.0130$ |        26,171       -0.0010$
      250us |       581,085       -0.0132$ |        51,281       -0.0019$
     1000us |       483,365       -0.0147$ |       176,982       -0.0047$
```

Negative means it can afford to *pay* the venue that much per share. The fast maker can pay well
over a cent; the 50µs maker can pay eight hundredths of a cent, which is less than an inverted
venue charges. **Latency and venue choice are the same decision.** A slow maker belongs where
quoting is subsidised; the fast one is indifferent, which is why it can afford to quote where the
aggressive flow is.

Two caveats worth stating. Rate cards are tiered by monthly volume and run to several pages —
these are top-line numbers, not a quote. And a real maker facing an inverted venue's fee would
widen its quote rather than keep paying, which this study's fixed one-tick edge does not let it do.

### What this does not model

- **One-way latency, applied once.** No gateway queueing, no serialization delay that grows with
  message size, no matching-engine processing time, and no separate market-data latency — the
  makers here see the jump instantly and only their *outbound* messages are delayed. Real
  co-location is bought mostly to see faster, and that half is missing.
- **The makers are stationary.** No inventory skew, no spread widening when toxicity rises, no
  pulling out of the market entirely. A real slow maker's response to this table would be to
  quote wider, and quoting wider is how it survives — which means the 50µs row, where it keeps 6%
  of its parity P&L, is what happens to a maker that refuses to adapt, not a law.
- **Fair value is exogenous and public.** Both makers see the same jump at the same instant, which
  is deliberate: it isolates latency from information. In a real market the fast participant
  usually has both.

## What isn't modeled

- One symbol, one venue. No routing, no NBBO, no Reg NMS.
- Limit, market, IOC and FOK orders — no stops, icebergs, pegged, or auction orders.
- No opening/closing auction, which is where a large share of real volume actually trades, under
  entirely different rules.
- ~~No latency~~ — `src/latency.py` puts messages on the wire and the book serves them in
  arrival order; section 6 measures what a 15µs disadvantage does to a market maker. The
  simulator in sections 1-5 still runs with no latency at all, so every number in those sections
  describes a market where everyone is infinitely fast.
- ~~No fees or rebates~~ — `src/fees.py` charges maker-taker and inverted schedules, and
  section 6b shows the rebate is a fifth of the quoted edge and decides whether the slow maker has
  a business at all. Sections 1-5 still trade for free. Self-trade prevention exists now (`participant_id` + `StpPolicy` on every
  order type - see README's Design notes and `tests/test_stp.py`), but no other risk checks
  (position limits, fat-finger checks). The zero-intelligence simulator in sections 1-5 still doesn't
  assign participant identities to its agents, so none of the numbers above exercise it.
- Agents have no memory, no inventory, and no information — which is exactly what makes it a
  valid null model, and exactly what makes the depth magnitudes wrong.
