"""Tests for the latency model and the message bus.

The bus is twelve lines of heap operations, so the tests are not about whether
a priority queue works. They are about the three properties the latency study's
conclusions rest on: arrival order beats submission order, ties are broken the
way a venue breaks them, and a message can schedule another message without
the ordering coming apart.
"""

import pytest

from src.latency import LatencyModel, MessageBus
from src.order import Side
from src.orderbook import LimitOrderBook


def test_arrival_order_beats_submission_order():
    """The whole point. The slow participant submits first and is served
    second, because a matching engine does not know when you decided."""
    log = []
    bus = MessageBus({"fast": LatencyModel(10.0), "slow": LatencyModel(250.0)})
    bus.send(0.0, "slow", lambda: log.append("slow"))
    bus.send(0.0, "fast", lambda: log.append("fast"))
    bus.deliver_until(1_000.0)
    assert log == ["fast", "slow"]


def test_a_later_decision_can_still_win():
    """Deciding 100us later and arriving 140us earlier. This is the asymmetry
    that makes latency worth paying for."""
    log = []
    bus = MessageBus({"fast": LatencyModel(10.0), "slow": LatencyModel(250.0)})
    bus.send(0.0, "slow", lambda: log.append("slow"))
    bus.send(100.0, "fast", lambda: log.append("fast"))
    bus.deliver_until(1_000.0)
    assert log == ["fast", "slow"]


def test_ties_break_on_submission_sequence_not_on_the_action():
    """Two messages arriving in the same nanosecond have to be ordered
    somehow. Ordering them by the callable would be a TypeError; the sequence
    number is what a real venue's port ordering stands in for here."""
    log = []
    bus = MessageBus({"a": LatencyModel(5.0), "b": LatencyModel(5.0)})
    bus.send(0.0, "a", lambda: log.append("a"))
    bus.send(0.0, "b", lambda: log.append("b"))
    bus.deliver_until(100.0)
    assert log == ["a", "b"]


def test_an_unregistered_participant_is_instantaneous():
    """The default that keeps every existing caller of the book unaffected -
    and a real case, since the exchange's own housekeeping has no wire to
    cross."""
    bus = MessageBus()
    assert bus.send(0.0, "exchange", lambda: None) == 0.0


def test_nothing_is_delivered_before_it_arrives():
    log = []
    bus = MessageBus({"slow": LatencyModel(250.0)})
    bus.send(0.0, "slow", lambda: log.append("slow"))
    bus.deliver_until(100.0)
    assert log == []
    assert bus.pending() == 1
    bus.deliver_until(300.0)
    assert log == ["slow"]


def test_an_action_can_schedule_another_action():
    """A strategy reacting to its own fill. The reaction has to land after the
    thing it reacted to, which is only true if delivery re-reads the heap
    rather than iterating a snapshot of it."""
    log = []
    bus = MessageBus({"m": LatencyModel(10.0)})

    def first():
        log.append("first")
        bus.send(bus.now_us, "m", lambda: log.append("second"))

    bus.send(0.0, "m", first)
    bus.drain()
    assert log == ["first", "second"]


def test_jitter_never_goes_below_the_floor():
    """Exponential rather than normal jitter, because a symmetric
    distribution would produce messages arriving faster than physics
    allows."""
    model = LatencyModel(10.0, jitter_us=50.0, seed=0)
    draws = [model.sample() for _ in range(2_000)]
    assert min(draws) >= 10.0
    assert max(draws) > 10.0


def test_zero_jitter_is_deterministic():
    model = LatencyModel(42.0)
    assert {model.sample() for _ in range(10)} == {42.0}


def test_negative_latency_is_rejected():
    with pytest.raises(ValueError):
        LatencyModel(-1.0)
    with pytest.raises(ValueError):
        LatencyModel(10.0, jitter_us=-1.0)


def test_the_faster_participant_wins_queue_priority_in_the_book():
    """The bus and the book, together: two makers decide to quote the same
    price at the same instant, and the one with the shorter wire rests in
    front. Queue position is the thing latency actually buys, and this is the
    smallest test that shows it."""
    book = LimitOrderBook()
    bus = MessageBus({"fast": LatencyModel(10.0), "slow": LatencyModel(250.0)})
    ids = {}

    for name in ("slow", "fast"):
        bus.send(0.0, name, lambda n=name: ids.__setitem__(
            n, book.add_limit_order(Side.BUY, 99.99, 100, participant_id=n)[0]))
    bus.drain()

    trades = book.market_order(Side.SELL, 100)
    assert len(trades) == 1
    assert trades[0].maker_id == ids["fast"]


def test_a_cancel_that_arrives_too_late_is_a_fill():
    """Adverse selection in four lines. The maker decides to pull its quote at
    the same instant the taker decides to hit it; whose message arrives first
    decides whether that decision mattered at all."""
    for maker_latency, expect_filled in ((10.0, False), (250.0, True)):
        book = LimitOrderBook()
        bus = MessageBus({"maker": LatencyModel(maker_latency),
                          "taker": LatencyModel(50.0)})
        oid, _ = book.add_limit_order(Side.BUY, 99.99, 100, participant_id="maker")
        trades = []
        bus.send(0.0, "maker", lambda: book.cancel(oid))
        bus.send(0.0, "taker",
                 lambda: trades.extend(book.market_order(Side.SELL, 100)))
        bus.drain()
        assert bool(trades) is expect_filled
