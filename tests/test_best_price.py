"""The price-level heaps, checked against the thing they replaced.

An optimization that changes an answer is not an optimization. These tests
don't assert what the best price should be in hand-written scenarios - the
rest of the suite already does that. They assert that the fast path and the
obvious path agree in every state a random sequence of orders can reach,
which is the property that actually matters and the one a heap with lazy
deletion is most likely to break.
"""

import numpy as np
import pytest

from src.order import Side, to_tick
from src.orderbook import LimitOrderBook


def scan_best(book: LimitOrderBook, side: Side) -> float | None:
    """Best price the slow, obviously-correct way."""
    levels = book.bids if side is Side.BUY else book.asks
    if not levels:
        return None
    return max(levels) if side is Side.BUY else min(levels)


def random_flow(book: LimitOrderBook, n: int, seed: int, check=None) -> None:
    """Push n random events through the book, optionally checking after each."""
    rng = np.random.default_rng(seed)
    live: list[int] = []

    for _ in range(n):
        roll = rng.random()
        side = Side.BUY if rng.random() < 0.5 else Side.SELL

        if roll < 0.55:
            price = to_tick(100.0 + rng.normal(0, 0.08))
            oid, _ = book.add_limit_order(side, price, int(rng.integers(10, 200)))
            if oid in book._by_id:
                live.append(oid)
        elif roll < 0.8 and live:
            book.cancel(live.pop(int(rng.integers(len(live)))))
        else:
            book.market_order(side, int(rng.integers(10, 300)))

        if check is not None:
            check()


class TestAgreesWithTheScan:
    @pytest.mark.parametrize("seed", [0, 1, 2, 3, 4])
    def test_best_price_matches_after_every_event(self, seed):
        book = LimitOrderBook()
        for i in range(1, 6):
            book.add_limit_order(Side.BUY, to_tick(100 - 0.01 * i), 100)
            book.add_limit_order(Side.SELL, to_tick(100 + 0.01 * i), 100)

        def check():
            assert book.best_bid() == scan_best(book, Side.BUY)
            assert book.best_ask() == scan_best(book, Side.SELL)

        random_flow(book, 400, seed, check)

    def test_empty_book_has_no_best_price(self):
        book = LimitOrderBook()
        assert book.best_bid() is None and book.best_ask() is None

    def test_best_price_returns_after_a_level_dies_and_another_appears(self):
        # the case lazy deletion is most likely to get wrong: a level goes
        # away while its heap entry is still sitting in the heap
        book = LimitOrderBook()
        oid, _ = book.add_limit_order(Side.BUY, 100.00, 100)
        book.cancel(oid)
        assert book.best_bid() is None
        book.add_limit_order(Side.BUY, 99.50, 100)
        assert book.best_bid() == 99.50


class TestHeapStaysBounded:
    def test_recreating_a_level_does_not_grow_the_heap(self):
        """The reason _in_heap exists.

        Add and cancel at the same price 2,000 times, with a better bid
        resting in front of it the whole time. Without the membership set
        that is 2,000 heap entries for one price level, none of which ever
        surfaces at the top where the lazy cleanup could throw it away.
        """
        book = LimitOrderBook()
        book.add_limit_order(Side.BUY, 100.00, 100)  # always the best bid

        for _ in range(2_000):
            oid, _ = book.add_limit_order(Side.BUY, 99.00, 50)
            book.cancel(oid)

        assert len(book._bid_heap) <= 2

    def test_heap_stays_proportional_to_distinct_prices(self):
        book = LimitOrderBook()
        random_flow(book, 3_000, seed=9)
        prices = {o.price for o in book._by_id.values()}
        # stale entries are allowed - at most one per distinct price ever seen
        assert len(book._bid_heap) + len(book._ask_heap) <= 2 * (len(prices) + 40)
