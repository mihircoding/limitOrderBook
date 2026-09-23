"""Fee arithmetic, and the break-even rate that decides where to quote.

Small module, but the sign convention is the kind of thing that is wrong for
a week before anyone notices, and the number it feeds - what a maker can
afford to pay a venue - is only meaningful if the sign is right. Everything
here is stated from the participant's point of view: positive is received.
"""
import math

import pytest

from src.fees import (FLAT, INVERTED, MAKER_TAKER, FeeSchedule,
                      breakeven_maker_rate)


def test_maker_taker_pays_the_maker_and_charges_the_taker():
    assert MAKER_TAKER.maker > 0 and MAKER_TAKER.taker < 0
    assert MAKER_TAKER.maker_credit(10_000) == pytest.approx(20.0)
    assert MAKER_TAKER.taker_charge(10_000) == pytest.approx(-30.0)


def test_an_inverted_venue_is_the_other_way_round():
    assert INVERTED.maker < 0 and INVERTED.taker > 0
    assert INVERTED.net(10_000, 0) < 0          # quoting costs money there
    assert INVERTED.net(0, 10_000) > 0          # taking is paid


def test_no_fees_is_free_and_no_volume_is_nothing():
    assert FLAT.net(50_000, 50_000) == 0.0
    assert MAKER_TAKER.per_share(0, 0) == 0.0
    assert MAKER_TAKER.net(0, 0) == 0.0


def test_per_share_blends_both_roles():
    sched = FeeSchedule("test", maker=+0.002, taker=-0.003)
    # 3:1 passive to aggressive -> (3*0.002 - 1*0.003) / 4
    assert sched.per_share(3_000, 1_000) == pytest.approx(0.00075)


def test_break_even_rate_is_the_rate_that_nets_to_zero():
    pnl, passive, aggressive, taker_fee = -250.0, 100_000, 20_000, -0.0030
    rate = breakeven_maker_rate(pnl, passive, aggressive, taker_fee)
    assert pnl + rate * passive + taker_fee * aggressive == pytest.approx(0.0)
    assert rate > 0                              # losing money: needs a rebate


def test_a_profitable_maker_can_afford_to_pay():
    """A negative break-even rate is the most the venue could charge it."""
    rate = breakeven_maker_rate(500.0, 100_000, 0, taker_fee=-0.0030)
    assert rate == pytest.approx(-0.005)
    afford = FeeSchedule("expensive", maker=rate, taker=-0.0030)
    assert 500.0 + afford.net(100_000, 0) == pytest.approx(0.0)
    # charge a hundredth of a cent more and it is underwater
    worse = FeeSchedule("worse", maker=rate - 0.0001, taker=-0.0030)
    assert 500.0 + worse.net(100_000, 0) < 0


def test_no_passive_volume_means_no_rate_can_help():
    assert breakeven_maker_rate(-100.0, 0, 5_000, -0.0030) == -math.inf


def test_taking_more_raises_the_rebate_needed():
    a = breakeven_maker_rate(-100.0, 50_000, 1_000, taker_fee=-0.0030)
    b = breakeven_maker_rate(-100.0, 50_000, 20_000, taker_fee=-0.0030)
    assert b > a
