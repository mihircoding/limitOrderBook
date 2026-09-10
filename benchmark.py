"""How fast is the book, and what is it spending its time on?

The header comment in src/orderbook.py used to say that scanning the price
dict for the best price is "fine at this scale" and that a sorted structure is
"a profiling-driven optimization, not where you start." This is the profiling.

Two books are measured side by side in one process, so the comparison is not
across machines, Python versions or moods:

    LimitOrderBook   - heap of live price levels, O(1) best-price read
    ScanBook         - the previous implementation, min()/max() over the dict

ScanBook overrides exactly one method. Everything else - matching, priority,
cancels, STP - is shared, so any difference in the numbers is attributable to
best-price lookup and nothing else.

Usage:
    python benchmark.py            # the full sweep, ~30s
    python benchmark.py --quick    # fewer events, for a sanity check
"""

from __future__ import annotations

import argparse
import statistics
import time

import numpy as np

from src.order import Side, to_tick
from src.orderbook import LimitOrderBook
from src.simulator import seed_book, simulate

TICK = 0.01


class ScanBook(LimitOrderBook):
    """The book as it was before the heaps: best price by scanning the keys.

    Kept here rather than deleted from history because "this optimization was
    worth it" is a claim, and a claim needs a control to measure against.
    """

    def _best_price(self, side: Side) -> float | None:
        book = self.bids if side is Side.BUY else self.asks
        if not book:
            return None
        return max(book) if side is Side.BUY else min(book)


def build(book_cls, levels: int, qty: int = 100, mid: float = 100.0):
    """A book with `levels` price levels resting on each side."""
    book = book_cls()
    for i in range(1, levels + 1):
        book.add_limit_order(Side.BUY, to_tick(mid - TICK * i), qty)
        book.add_limit_order(Side.SELL, to_tick(mid + TICK * i), qty)
    return book


def time_quotes(book_cls, levels: int, n: int = 200_000) -> float:
    """Nanoseconds per best_bid() call at a given book depth.

    Timed as the best of three passes, not the average. A slow pass means the
    OS scheduled something else on this core; it says nothing about the code.
    The fastest pass is the one least contaminated by that.
    """
    book = build(book_cls, levels)
    best_bid = book.best_bid  # bind once; attribute lookup is not what we're timing

    for _ in range(1000):     # warm up: first call also pays for lazy heap cleanup
        best_bid()

    best = float("inf")
    for _ in range(3):
        t0 = time.perf_counter()
        for _ in range(n):
            best_bid()
        best = min(best, (time.perf_counter() - t0) / n * 1e9)
    return best


def time_simulation(book_cls, levels: int, n_events: int, seed: int = 7) -> float:
    """Events per second for the full zero-intelligence run.

    This is the number that matters. best_bid() in a loop is a microbenchmark;
    this is the same work the project actually does, with matching, resting,
    cancels and mid-price reads all mixed in at their real frequencies.
    """
    best = 0.0
    for _ in range(3):        # best of three, same reasoning as time_quotes
        book = book_cls()
        seed_book(book, mid=100.0, levels=levels, qty=100)
        t0 = time.perf_counter()
        simulate(book, n_events=n_events, seed=seed)
        best = max(best, n_events / (time.perf_counter() - t0))
    return best


def latency_percentiles(book_cls, levels: int, n: int = 20_000, seed: int = 3) -> dict:
    """Per-operation latency distribution, in microseconds.

    Percentiles rather than a mean, because the mean of a latency distribution
    is the one statistic nobody trading on it cares about. The tail is where
    the interesting behavior is: an add that happens to create a new price
    level pays a heap push, and a market order that sweeps several levels pays
    for each one.
    """
    rng = np.random.default_rng(seed)
    book = build(book_cls, levels)
    samples: dict[str, list[float]] = {"add": [], "cancel": [], "market": []}
    live: list[int] = list(book._by_id)

    for _ in range(n):
        roll = rng.random()

        if roll < 0.6:  # passive add, away from the touch
            side = Side.BUY if rng.random() < 0.5 else Side.SELL
            offset = (1 + abs(rng.normal(0, 3))) * TICK
            price = to_tick(100.0 - offset if side is Side.BUY else 100.0 + offset)
            t0 = time.perf_counter_ns()
            oid, _ = book.add_limit_order(side, price, 100)
            samples["add"].append((time.perf_counter_ns() - t0) / 1000)
            if oid in book._by_id:
                live.append(oid)

        elif roll < 0.85 and live:
            oid = live.pop(int(rng.integers(len(live))))
            t0 = time.perf_counter_ns()
            book.cancel(oid)
            samples["cancel"].append((time.perf_counter_ns() - t0) / 1000)

        else:
            side = Side.BUY if rng.random() < 0.5 else Side.SELL
            t0 = time.perf_counter_ns()
            book.market_order(side, int(rng.integers(10, 400)))
            samples["market"].append((time.perf_counter_ns() - t0) / 1000)

    out = {}
    for op, xs in samples.items():
        xs.sort()
        if not xs:
            continue
        out[op] = {
            "n": len(xs),
            "p50": statistics.median(xs),
            "p99": xs[int(0.99 * len(xs))],
            "max": xs[-1],
        }
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="fewer events")
    args = parser.parse_args()

    depths = (10, 50, 100, 500, 1000)
    n_events = 10_000 if args.quick else 50_000
    n_quotes = 50_000 if args.quick else 200_000

    print("Best-price lookup, nanoseconds per call")
    print("-" * 56)
    print(f"{'levels':>7} {'scan (ns)':>12} {'heap (ns)':>12} {'speedup':>10}")
    for levels in depths:
        scan = time_quotes(ScanBook, levels, n_quotes)
        heap = time_quotes(LimitOrderBook, levels, n_quotes)
        print(f"{levels:>7} {scan:>12.0f} {heap:>12.0f} {scan / heap:>9.1f}x")

    print(f"\nFull simulation, {n_events:,} zero-intelligence events")
    print("-" * 56)
    print(f"{'levels':>7} {'scan (ev/s)':>13} {'heap (ev/s)':>13} {'speedup':>10}")
    sim = {}
    for levels in depths:
        scan = time_simulation(ScanBook, levels, n_events)
        heap = time_simulation(LimitOrderBook, levels, n_events)
        sim[levels] = (scan, heap)
        print(f"{levels:>7} {scan:>13,.0f} {heap:>13,.0f} {heap / scan:>9.1f}x")

    scan_first, heap_first = sim[depths[0]]
    scan_last, heap_last = sim[depths[-1]]
    print(f"\nThroughput falling off with depth, {depths[0]} -> {depths[-1]} levels:")
    print(f"  scan  {scan_first:,.0f} -> {scan_last:,.0f} ev/s  "
          f"({scan_first / scan_last:.1f}x worse)")
    print(f"  heap  {heap_first:,.0f} -> {heap_last:,.0f} ev/s  "
          f"({heap_first / heap_last:.1f}x worse)")

    print("\nPer-operation latency at 500 levels, microseconds")
    print("-" * 56)
    print(f"{'operation':<12} {'n':>8} {'p50':>9} {'p99':>9} {'max':>9}")
    for op, st in latency_percentiles(LimitOrderBook, 500).items():
        print(f"{op:<12} {st['n']:>8,} {st['p50']:>9.2f} {st['p99']:>9.2f} {st['max']:>9.2f}")


if __name__ == "__main__":
    main()
