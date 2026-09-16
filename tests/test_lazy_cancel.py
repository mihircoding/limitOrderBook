"""Cancellation is lazy: cancel() flags the order and leaves it in its queue.

That is a real change to how the book works internally, and the whole
justification for it is that nothing outside the book can tell. So these tests
are mostly about invisibility - a cancelled order must not fill, must not show
up in depth, must not count as liquidity a fill-or-kill can reach, and must not
hold a price level open once it is the only thing left there.

The last class is the one that carries the weight: identical random flow
through the lazy book and through the old splicing version, checking that every
trade and every quote comes out the same. An optimization that changes an
answer is not an optimization.
"""

from collections import deque

import numpy as np
import pytest

from src.order import Side, StpPolicy, to_tick
from src.orderbook import LimitOrderBook


class EagerCancelBook(LimitOrderBook):
    """cancel() the way it was written before tombstones: splice the order out
    of the middle of its deque, O(orders at that level). Correct, slow in the
    tail, and the reference the lazy version has to agree with."""

    def cancel(self, order_id: int) -> bool:
        order = self._by_id.pop(order_id, None)
        if order is None or not order.active:
            return False
        book = self.bids if order.side is Side.BUY else self.asks
        queue = book.get(order.price)
        if queue is None:
            return False
        try:
            queue.remove(order)
        except ValueError:
            return False
        order.active = False
        self._drop_live(order.side, order.price)
        return True


@pytest.fixture
def book():
    return LimitOrderBook()


class TestCancelledOrdersAreGone:
    def test_a_cancelled_order_never_fills(self, book):
        oid, _ = book.add_limit_order(Side.SELL, 101.00, 100)
        book.cancel(oid)
        trades = book.market_order(Side.BUY, 100)
        assert trades == []

    def test_cancelled_size_is_not_in_depth(self, book):
        keep, _ = book.add_limit_order(Side.BUY, 100.00, 300)
        drop, _ = book.add_limit_order(Side.BUY, 100.00, 700)
        book.cancel(drop)
        assert book.depth(Side.BUY) == [(100.00, 300)]
        assert keep in book._by_id

    def test_emptying_a_level_by_cancelling_removes_the_level(self, book):
        a, _ = book.add_limit_order(Side.BUY, 100.00, 100)
        b, _ = book.add_limit_order(Side.BUY, 100.00, 100)
        book.add_limit_order(Side.BUY, 99.50, 100)
        book.cancel(a)
        assert book.best_bid() == 100.00  # b is still there
        book.cancel(b)
        assert book.best_bid() == 99.50
        assert 100.00 not in book.bids
        assert book.depth(Side.BUY) == [(99.50, 100)]

    def test_cancel_is_idempotent_and_honest_about_it(self, book):
        oid, _ = book.add_limit_order(Side.BUY, 100.00, 100)
        assert book.cancel(oid) is True
        assert book.cancel(oid) is False
        assert book.cancel(9999) is False

    def test_a_filled_order_cannot_be_cancelled(self, book):
        oid, _ = book.add_limit_order(Side.SELL, 101.00, 100)
        book.market_order(Side.BUY, 100)
        assert book.cancel(oid) is False

    def test_an_order_pulled_by_stp_cannot_then_be_cancelled(self, book):
        """Self-trade prevention removes a resting order without a trade. It has
        to leave it in the same state a cancel would, or the two paths disagree
        about what 'gone' means."""
        resting, _ = book.add_limit_order(Side.SELL, 101.00, 100, participant_id="mm")
        book.add_limit_order(Side.BUY, 101.00, 100, participant_id="mm",
                             stp_policy=StpPolicy.CANCEL_RESTING)
        assert book.cancel(resting) is False


class TestQueuePriority:
    def test_cancelling_the_middle_does_not_reorder_the_rest(self, book):
        first, _ = book.add_limit_order(Side.SELL, 101.00, 100)
        middle, _ = book.add_limit_order(Side.SELL, 101.00, 100)
        last, _ = book.add_limit_order(Side.SELL, 101.00, 100)
        book.cancel(middle)

        trades = book.market_order(Side.BUY, 200)
        assert [t.maker_id for t in trades] == [first, last]

    def test_cancelling_the_front_promotes_the_next_in_line(self, book):
        front, _ = book.add_limit_order(Side.BUY, 100.00, 100)
        behind, _ = book.add_limit_order(Side.BUY, 100.00, 100)
        book.cancel(front)
        trades = book.market_order(Side.SELL, 100)
        assert [t.maker_id for t in trades] == [behind]


class TestFillOrKillSeesThroughTombstones:
    def test_fok_does_not_count_cancelled_liquidity(self, book):
        """A FOK checks reachable size before it fires. Counting a tombstone
        would make it fire and then discover mid-fill that the size wasn't
        there - which is the one thing a fill-or-kill may never do."""
        book.add_limit_order(Side.SELL, 101.00, 100)
        dead, _ = book.add_limit_order(Side.SELL, 101.00, 400)
        book.cancel(dead)

        assert book.add_fok_order(Side.BUY, 101.00, 500) == []   # only 100 is real
        trades = book.add_fok_order(Side.BUY, 101.00, 100)
        assert sum(t.quantity for t in trades) == 100


class TestTombstonesDoNotAccumulate:
    def test_the_matching_loop_reclaims_them(self, book):
        """Tombstones ahead of a live order are freed when matching walks past.

        Three cancels queued in front of a survivor, so the level stays open
        and the tombstones really are held. One partial fill later they are
        gone, and the survivor is still there with the right size - the loop
        discards them, it does not trade them.
        """
        queued = [book.add_limit_order(Side.SELL, 101.00, 100)[0] for _ in range(3)]
        survivor, _ = book.add_limit_order(Side.SELL, 101.00, 100)
        for oid in queued:
            book.cancel(oid)
        assert len(book.asks[101.00]) == 4  # three tombstones ahead of one live order

        trades = book.market_order(Side.BUY, 40)
        assert [t.maker_id for t in trades] == [survivor]
        assert len(book.asks[101.00]) == 1
        assert book.depth(Side.SELL) == [(101.00, 60)]

    def test_an_emptied_level_drops_its_tombstones_whole(self, book):
        """The other way they get freed, and the one that bounds the memory:
        when the last live order at a price goes, the price key is deleted and
        the deque goes with it - however many tombstones were still in it."""
        for _ in range(5):
            book.cancel(book.add_limit_order(Side.SELL, 101.00, 100)[0])
        assert 101.00 not in book.asks

    def test_add_and_cancel_forever_does_not_grow_the_book(self, book):
        """The pathological case for lazy deletion: a price nobody ever trades
        at, churned thousands of times. Each cancel empties the level, which
        deletes the key and takes the deque with it, so nothing survives to
        pile up."""
        book.add_limit_order(Side.BUY, 99.00, 100)  # a real bid, further out
        for _ in range(2_000):
            oid, _ = book.add_limit_order(Side.BUY, 100.00, 100)
            book.cancel(oid)

        assert 100.00 not in book.bids
        assert book.best_bid() == 99.00
        assert len(book._by_id) == 1
        assert len(book._bid_heap) <= 2  # one entry per distinct price, stale or not

    def test_live_counts_stay_in_step_with_the_book(self, book):
        """Every price key must have a live count and vice versa. A level with
        a count of zero, or a count with no level, is a corrupt book - and
        would show up as a phantom quote long before anyone found the cause."""
        rng = np.random.default_rng(11)
        live: list[int] = []
        for _ in range(3_000):
            roll = rng.random()
            side = Side.BUY if rng.random() < 0.5 else Side.SELL
            if roll < 0.55:
                oid, _ = book.add_limit_order(side, to_tick(100 + rng.normal(0, 0.08)),
                                              int(rng.integers(10, 200)))
                if oid in book._by_id:
                    live.append(oid)
            elif roll < 0.8 and live:
                book.cancel(live.pop(int(rng.integers(len(live)))))
            else:
                book.market_order(side, int(rng.integers(10, 300)))

            for side_book, counts in ((book.bids, book._bid_live),
                                      (book.asks, book._ask_live)):
                assert set(side_book) == set(counts)
                for price, queue in side_book.items():
                    assert counts[price] == sum(1 for o in queue if o.active) > 0


class TestAgreesWithTheEagerVersion:
    @pytest.mark.parametrize("seed", [0, 1, 2, 3, 4])
    def test_identical_trades_and_quotes_under_random_flow(self, seed):
        """The claim in one test. Same seed, same events, both books; every
        trade and both quotes compared after every single event."""
        lazy, eager = LimitOrderBook(), EagerCancelBook()
        rng = np.random.default_rng(seed)
        live: list[int] = []

        for _ in range(2_000):
            roll = rng.random()
            side = Side.BUY if rng.random() < 0.5 else Side.SELL

            if roll < 0.55:
                price = to_tick(100.0 + rng.normal(0, 0.08))
                qty = int(rng.integers(10, 200))
                (oid, t_lazy), (_, t_eager) = (lazy.add_limit_order(side, price, qty),
                                               eager.add_limit_order(side, price, qty))
                if oid in lazy._by_id:
                    live.append(oid)
            elif roll < 0.8 and live:
                oid = live.pop(int(rng.integers(len(live))))
                t_lazy, t_eager = lazy.cancel(oid), eager.cancel(oid)
            else:
                qty = int(rng.integers(10, 300))
                t_lazy, t_eager = lazy.market_order(side, qty), eager.market_order(side, qty)

            assert t_lazy == t_eager
            assert lazy.best_bid() == eager.best_bid()
            assert lazy.best_ask() == eager.best_ask()
            assert lazy.depth(Side.BUY, 5) == eager.depth(Side.BUY, 5)
            assert lazy.depth(Side.SELL, 5) == eager.depth(Side.SELL, 5)
