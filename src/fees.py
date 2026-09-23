"""Exchange fees, which decide whether quoting is worth doing at all.

Everything else in this project measures P&L in ticks, as if trading were
free. It isn't. On US equity venues the exchange pays you to post a resting
order that gets filled (a maker rebate) and charges you to take liquidity off
the book (a taker fee), and those numbers are large next to the edge a market
maker is working with. A tenth of a cent per share is a tenth of a tick, and
this book's makers quote one tick wide - so a rebate is not a rounding error
on the strategy, it is a fifth of it.

Two schedules exist in the market and they are not variations on each other:

    maker-taker   pay the maker, charge the taker. The usual one. Quoting is
                  subsidised, so queues are long and it can be worth posting
                  at a price you would not otherwise quote.
    inverted      charge the maker, pay the taker. Taking is subsidised, so
                  these venues attract aggressive flow, which is exactly the
                  flow that knows something. A maker on an inverted venue is
                  paying for the privilege of being adversely selected - and
                  gets a shorter queue in exchange, which is sometimes worth
                  it.

Both are expressed here with the same sign convention, from the point of view
of the participant: positive is money received, negative is money paid.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FeeSchedule:
    """Per-share economics of one venue.

    maker / taker are dollars per share received. A maker rebate of two
    hundredths of a cent is maker=+0.0002; a thirty-mil taker fee is
    taker=-0.0030.
    """

    name: str
    maker: float
    taker: float

    def maker_credit(self, shares: int | float) -> float:
        """Dollars received (negative: paid) for `shares` filled passively."""
        return self.maker * shares

    def taker_charge(self, shares: int | float) -> float:
        """Dollars received (negative: paid) for `shares` taken aggressively."""
        return self.taker * shares

    def net(self, maker_shares: int | float, taker_shares: int | float) -> float:
        """Total fee P&L in dollars, positive meaning the venue paid you."""
        return self.maker_credit(maker_shares) + self.taker_charge(taker_shares)

    def per_share(self, maker_shares: int | float, taker_shares: int | float) -> float:
        """Fee P&L per share traded, over both roles. Zero volume is zero."""
        total = maker_shares + taker_shares
        return self.net(maker_shares, taker_shares) / total if total else 0.0


# Representative US equity schedules, in dollars per share. Real rate cards
# are tiered by monthly volume and run to several pages; these are the top
# line of each kind, which is what a study of the effect needs.
MAKER_TAKER = FeeSchedule("maker-taker", maker=+0.0020, taker=-0.0030)
INVERTED = FeeSchedule("inverted", maker=-0.0010, taker=+0.0002)
FLAT = FeeSchedule("no fees", maker=0.0, taker=0.0)

SCHEDULES = (FLAT, MAKER_TAKER, INVERTED)


def breakeven_maker_rate(trading_pnl: float, maker_shares: int | float,
                         taker_shares: int | float, taker_fee: float) -> float:
    """The per-share maker rate at which a quoting strategy exactly breaks even.

    Solve

        trading_pnl + rate * maker_shares + taker_fee * taker_shares = 0

    for `rate`. The sign is the useful part. A positive answer is a rebate the
    strategy *needs* to survive, and can be held against what venues actually
    pay - if it needs more than the market offers, it is not a strategy waiting
    for a better rate card. A negative answer is the most it could afford to
    *pay* per share and still break even, which is the number that decides
    whether an inverted venue is worth quoting on.

    Returns -inf when there is no passive volume: with nothing filled
    passively the maker rate is irrelevant at any price.
    """
    if maker_shares <= 0:
        return float("-inf")
    return -(trading_pnl + taker_fee * taker_shares) / maker_shares
