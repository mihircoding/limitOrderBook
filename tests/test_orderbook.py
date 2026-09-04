"""The matching spec as executable scenarios. Read these as the rulebook."""

import pytest

from src.order import Side
from src.orderbook import LimitOrderBook


@pytest.fixture
def book():
    return LimitOrderBook()


class TestRestingOrders:
    def test_empty_book_has_no_quotes(self, book):
        assert book.best_bid() is None
        assert book.best_ask() is None

    def test_non_crossing_orders_rest(self, book):
        book.add_limit_order(Side.BUY, 99.00, 100)
        book.add_limit_order(Side.SELL, 101.00, 100)
        assert book.best_bid() == 99.00
        assert book.best_ask() == 101.00
        assert book.spread() == pytest.approx(2.00)
        assert book.mid_price() == pytest.approx(100.00)

    def test_best_bid_is_highest_best_ask_is_lowest(self, book):
        for p in (98.00, 99.50, 99.00):
            book.add_limit_order(Side.BUY, p, 10)
        for p in (102.00, 100.50, 101.00):
            book.add_limit_order(Side.SELL, p, 10)
        assert book.best_bid() == 99.50
        assert book.best_ask() == 100.50

    def test_depth_aggregates_and_orders_levels(self, book):
        book.add_limit_order(Side.BUY, 99.00, 100)
        book.add_limit_order(Side.BUY, 99.00, 50)   # same level, sums
        book.add_limit_order(Side.BUY, 98.50, 200)
        assert book.depth(Side.BUY, levels=2) == [(99.00, 150), (98.50, 200)]


class TestMatching:
    def test_marketable_limit_fills_at_maker_price(self, book):
        book.add_limit_order(Side.SELL, 101.00, 100)
        _, trades = book.add_limit_order(Side.BUY, 101.50, 100)  # willing to pay more
        assert len(trades) == 1
        assert trades[0].price == 101.00  # maker's price, not 101.50
        assert trades[0].quantity == 100
        assert book.best_ask() is None  # ask fully consumed
        assert book.best_bid() is None  # buy fully filled, nothing rests

    def test_time_priority_fifo_at_same_price(self, book):
        first_id, _ = book.add_limit_order(Side.SELL, 101.00, 100)
        second_id, _ = book.add_limit_order(Side.SELL, 101.00, 100)
        _, trades = book.add_limit_order(Side.BUY, 101.00, 150)
        assert [t.maker_id for t in trades] == [first_id, second_id]
        assert [t.quantity for t in trades] == [100, 50]

    def test_price_priority_sweeps_levels_in_order(self, book):
        book.add_limit_order(Side.SELL, 101.00, 100)
        book.add_limit_order(Side.SELL, 100.50, 100)  # better ask, arrives later
        _, trades = book.add_limit_order(Side.BUY, 101.00, 150)
        assert [t.price for t in trades] == [100.50, 101.00]
        assert [t.quantity for t in trades] == [100, 50]

    def test_partial_fill_remainder_rests_as_new_best(self, book):
        """The scenario from the README — get this right and most follow."""
        book.add_limit_order(Side.SELL, 101.10, 300)
        _, trades = book.add_limit_order(Side.BUY, 101.10, 500)
        assert sum(t.quantity for t in trades) == 300
        assert book.best_ask() is None
        assert book.best_bid() == 101.10   # leftover 200 rests at the old ask price
        assert book.depth(Side.BUY, 1) == [(101.10, 200)]

    def test_stops_matching_when_price_no_longer_crosses(self, book):
        book.add_limit_order(Side.SELL, 100.00, 100)
        book.add_limit_order(Side.SELL, 102.00, 100)
        _, trades = book.add_limit_order(Side.BUY, 100.00, 300)
        assert sum(t.quantity for t in trades) == 100  # 102 ask is above the limit
        assert book.best_bid() == 100.00               # remainder 200 rests
        assert book.best_ask() == 102.00


class TestMarketOrdersAndCancels:
    def test_market_order_walks_the_book(self, book):
        book.add_limit_order(Side.SELL, 100.50, 100)
        book.add_limit_order(Side.SELL, 101.00, 100)
        trades = book.market_order(Side.BUY, 150)
        assert [t.price for t in trades] == [100.50, 101.00]
        assert [t.quantity for t in trades] == [100, 50]

    def test_market_order_on_empty_side_discards(self, book):
        assert book.market_order(Side.BUY, 100) == []

    def test_cancel_removes_resting_order(self, book):
        oid, _ = book.add_limit_order(Side.BUY, 99.00, 100)
        assert book.cancel(oid) is True
        assert book.best_bid() is None
        assert book.cancel(oid) is False  # already gone

    def test_cancel_preserves_others_at_level(self, book):
        first_id, _ = book.add_limit_order(Side.SELL, 101.00, 100)
        second_id, _ = book.add_limit_order(Side.SELL, 101.00, 70)
        book.cancel(first_id)
        _, trades = book.add_limit_order(Side.BUY, 101.00, 100)
        assert [t.maker_id for t in trades] == [second_id]
        assert trades[0].quantity == 70

    def test_filled_order_cannot_be_cancelled(self, book):
        oid, _ = book.add_limit_order(Side.SELL, 101.00, 100)
        book.market_order(Side.BUY, 100)
        assert book.cancel(oid) is False


class TestImmediateOrCancel:
    def test_ioc_fills_what_it_can_and_discards_the_rest(self, book):
        book.add_limit_order(Side.SELL, 101.00, 40)
        trades = book.add_ioc_order(Side.BUY, 101.00, 100)
        assert len(trades) == 1 and trades[0].quantity == 40
        # the unfilled 60 never rested - book is empty on the buy side
        assert book.best_bid() is None

    def test_ioc_fully_fills_like_a_marketable_limit_order(self, book):
        book.add_limit_order(Side.SELL, 101.00, 100)
        trades = book.add_ioc_order(Side.BUY, 101.00, 60)
        assert trades[0].quantity == 60
        assert book.depth(Side.SELL)[0] == (101.00, 40)

    def test_ioc_with_nothing_to_match_produces_no_trades_and_no_resting_order(self, book):
        trades = book.add_ioc_order(Side.BUY, 100.00, 50)
        assert trades == []
        assert book.best_bid() is None


class TestFillOrKill:
    def test_fok_kills_when_size_is_short(self, book):
        book.add_limit_order(Side.SELL, 101.00, 40)
        trades = book.add_fok_order(Side.BUY, 101.00, 100)
        assert trades == []
        # nothing traded - the resting sell order is untouched
        assert book.depth(Side.SELL)[0] == (101.00, 40)

    def test_fok_fills_completely_when_size_is_available(self, book):
        book.add_limit_order(Side.SELL, 101.00, 60)
        book.add_limit_order(Side.SELL, 101.50, 40)
        trades = book.add_fok_order(Side.BUY, 101.50, 100)
        assert sum(t.quantity for t in trades) == 100
        assert book.best_ask() is None

    def test_fok_checks_liquidity_within_the_limit_price_only(self, book):
        # 100 total resting, but only 60 of it is at a price this buy is
        # willing to cross - the other 40 sits above the limit and shouldn't
        # count toward "available"
        book.add_limit_order(Side.SELL, 101.00, 60)
        book.add_limit_order(Side.SELL, 102.00, 40)
        trades = book.add_fok_order(Side.BUY, 101.00, 100)
        assert trades == []
        assert book.depth(Side.SELL, levels=2) == [(101.00, 60), (102.00, 40)]

    def test_fok_never_rests_a_remainder(self, book):
        trades = book.add_fok_order(Side.BUY, 100.00, 10)
        assert trades == []
        assert book.best_bid() is None
