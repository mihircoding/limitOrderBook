"""Self-trade prevention: an incoming order must never print a trade against
a resting order from the same participant_id. See src/orderbook.py (_match,
_fillable_quantity) and src/order.py (StpPolicy) for the mechanics.

Every test here uses two participants, "alice" and "bob", unless the point
of the test is specifically the no-STP default.
"""

import pytest

from src.order import Side, StpPolicy
from src.orderbook import LimitOrderBook


class TestNoStpByDefault:
    """participant_id defaults to None everywhere - self-crossing must work
    exactly like it did before this feature existed, for every order type."""

    def test_limit_order_still_self_crosses_with_no_participant_id(self):
        book = LimitOrderBook()
        book.add_limit_order(Side.SELL, price=100.0, quantity=50)
        _, trades = book.add_limit_order(Side.BUY, price=100.0, quantity=50)
        assert len(trades) == 1
        assert trades[0].quantity == 50

    def test_market_order_still_self_crosses_with_no_participant_id(self):
        book = LimitOrderBook()
        book.add_limit_order(Side.SELL, price=100.0, quantity=50)
        trades = book.market_order(Side.BUY, quantity=50)
        assert len(trades) == 1


class TestCancelRestingPolicy:
    """The resting (maker) order gets pulled; the taker keeps matching
    against everyone else at that price and beyond."""

    def test_own_resting_order_is_pulled_without_a_trade(self):
        book = LimitOrderBook()
        resting_id, _ = book.add_limit_order(Side.SELL, price=100.0, quantity=50,
                                             participant_id="alice")
        _, trades = book.add_limit_order(Side.BUY, price=100.0, quantity=50,
                                         participant_id="alice",
                                         stp_policy=StpPolicy.CANCEL_RESTING)
        assert trades == []
        assert book.cancel(resting_id) is False  # already gone
        assert book.best_ask() is None

    def test_taker_falls_through_to_the_next_participants_order(self):
        book = LimitOrderBook()
        book.add_limit_order(Side.SELL, price=100.0, quantity=50, participant_id="alice")
        book.add_limit_order(Side.SELL, price=100.0, quantity=30, participant_id="bob")

        _, trades = book.add_limit_order(Side.BUY, price=100.0, quantity=50,
                                         participant_id="alice",
                                         stp_policy=StpPolicy.CANCEL_RESTING)

        # Alice's own 50 at the front is pulled, not filled; the incoming 50
        # then fills entirely against Bob's 30 (partial - only 30 available)
        # ... wait, only Bob's 30 remains at this level, so 30 fills and 20
        # rests as the new best bid.
        assert len(trades) == 1
        assert trades[0].maker_id != trades[0].taker_id
        assert trades[0].quantity == 30
        assert book.best_ask() is None  # both asks consumed (alice pulled, bob filled)
        assert book.best_bid() == pytest.approx(100.0)
        assert book.depth(Side.BUY)[0][1] == 20  # leftover 50-30 rests

    def test_market_order_skips_own_liquidity_under_cancel_resting(self):
        book = LimitOrderBook()
        book.add_limit_order(Side.SELL, price=100.0, quantity=50, participant_id="alice")
        book.add_limit_order(Side.SELL, price=101.0, quantity=50, participant_id="bob")

        trades = book.market_order(Side.BUY, quantity=50, participant_id="alice",
                                   stp_policy=StpPolicy.CANCEL_RESTING)

        assert len(trades) == 1
        assert trades[0].price == pytest.approx(101.0)  # alice's 100 level was pulled, not taken
        assert trades[0].quantity == 50


class TestCancelNewestPolicy:
    """The whole incoming order dies the instant it would self-match -
    nothing fills, nothing rests, even if other participants' liquidity was
    sitting right behind it."""

    def test_whole_incoming_limit_order_is_killed_not_just_reduced(self):
        book = LimitOrderBook()
        book.add_limit_order(Side.SELL, price=100.0, quantity=20, participant_id="alice")
        book.add_limit_order(Side.SELL, price=100.0, quantity=100, participant_id="bob")

        order_id, trades = book.add_limit_order(Side.BUY, price=100.0, quantity=50,
                                                participant_id="alice",
                                                stp_policy=StpPolicy.CANCEL_NEWEST)

        assert trades == []
        assert book.cancel(order_id) is False  # never rested - there's nothing to cancel
        # Both asks are untouched - alice's own order was hit first in time
        # priority and that killed the taker before bob's liquidity mattered.
        assert book.depth(Side.SELL) == [(100.0, 120)]

    def test_market_order_is_fully_discarded_on_contact(self):
        book = LimitOrderBook()
        book.add_limit_order(Side.SELL, price=100.0, quantity=20, participant_id="alice")
        trades = book.market_order(Side.BUY, quantity=20, participant_id="alice",
                                   stp_policy=StpPolicy.CANCEL_NEWEST)
        assert trades == []

    def test_other_participants_still_trade_normally_afterwards(self):
        """STP killing one order must not corrupt the book for anyone else."""
        book = LimitOrderBook()
        book.add_limit_order(Side.SELL, price=100.0, quantity=20, participant_id="alice")
        book.add_limit_order(Side.BUY, price=100.0, quantity=20, participant_id="alice",
                             stp_policy=StpPolicy.CANCEL_NEWEST)  # killed, book still has alice's ask

        _, trades = book.add_limit_order(Side.BUY, price=100.0, quantity=20,
                                         participant_id="bob")
        assert len(trades) == 1
        assert trades[0].quantity == 20
        assert book.best_ask() is None


class TestFokWithStp:
    """FOK has to pre-check fillability honestly under STP, or it either
    fires and then silently self-cancels mid-fill (defeating the point of
    Fill-Or-Kill) or rejects an order that could actually have filled."""

    def test_fok_counts_only_external_liquidity_under_cancel_resting(self):
        book = LimitOrderBook()
        book.add_limit_order(Side.SELL, price=100.0, quantity=1000, participant_id="alice")
        book.add_limit_order(Side.SELL, price=100.0, quantity=30, participant_id="bob")

        # Only Bob's 30 is honestly reachable for Alice under CANCEL_RESTING
        # - her own 1000 doesn't count, however big it looks in the book.
        killed = book.add_fok_order(Side.BUY, price=100.0, quantity=50,
                                    participant_id="alice", stp_policy=StpPolicy.CANCEL_RESTING)
        assert killed == []

        filled = book.add_fok_order(Side.BUY, price=100.0, quantity=30,
                                    participant_id="alice", stp_policy=StpPolicy.CANCEL_RESTING)
        assert len(filled) == 1
        assert filled[0].quantity == 30

    def test_fok_stops_counting_at_the_self_order_under_cancel_newest(self):
        book = LimitOrderBook()
        # Alice's order sits AHEAD of Bob's in time priority at this level.
        book.add_limit_order(Side.SELL, price=100.0, quantity=10, participant_id="alice")
        book.add_limit_order(Side.SELL, price=100.0, quantity=1000, participant_id="bob")

        # Even though 1000+ shares exist at this price, Alice's FOK would hit
        # her own order first and die - so nothing beyond her own 10 counts.
        killed = book.add_fok_order(Side.BUY, price=100.0, quantity=20,
                                    participant_id="alice", stp_policy=StpPolicy.CANCEL_NEWEST)
        assert killed == []
        # Book is untouched - a killed FOK must leave no trace.
        assert book.depth(Side.SELL) == [(100.0, 1010)]

    def test_fok_with_stp_never_partially_fills(self):
        """Regression guard for the exact bug this feature could introduce:
        _match applying STP mid-fill on an order _fillable_quantity already
        cleared would leave a FOK partially filled, which must never happen."""
        book = LimitOrderBook()
        book.add_limit_order(Side.SELL, price=100.0, quantity=10, participant_id="alice")
        book.add_limit_order(Side.SELL, price=100.0, quantity=40, participant_id="bob")

        filled = book.add_fok_order(Side.BUY, price=100.0, quantity=40,
                                    participant_id="alice", stp_policy=StpPolicy.CANCEL_RESTING)
        assert len(filled) == 1
        assert filled[0].quantity == 40
        assert book.depth(Side.SELL) == []  # alice's pulled, bob's fully filled


class TestStpDoesNotBreakTimePriority:
    """A pulled or killed order must not disturb FIFO ordering among the
    orders that are NOT involved in the self-trade."""

    def test_fifo_preserved_among_the_other_participants_orders(self):
        book = LimitOrderBook()
        book.add_limit_order(Side.SELL, price=100.0, quantity=10, participant_id="bob")
        book.add_limit_order(Side.SELL, price=100.0, quantity=10, participant_id="alice")
        book.add_limit_order(Side.SELL, price=100.0, quantity=10, participant_id="carol")

        # Alice's IOC pulls her own order out of the middle of the queue;
        # Bob (first in) and Carol (last in) must still fill in that order.
        trades = book.add_ioc_order(Side.BUY, price=100.0, quantity=20,
                                    participant_id="alice",
                                    stp_policy=StpPolicy.CANCEL_RESTING)

        assert len(trades) == 2
        assert [t.quantity for t in trades] == [10, 10]
        makers_in_order = [t.maker_id for t in trades]
        assert makers_in_order[0] < makers_in_order[1]  # bob's id, then carol's
