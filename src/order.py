"""Order and Trade types.

Prices are floats rounded to a tick (see TICK) so they can be dict keys safely.
Real systems store integer ticks; the round-trip through round() here is the
readable compromise and the docstrings say why.
"""

from dataclasses import dataclass, field
from enum import Enum

TICK = 0.01  # minimum price increment
_DECIMALS = 2


def to_tick(price: float) -> float:
    """Snap a price to the grid so equal prices compare equal as dict keys."""
    return round(round(price / TICK) * TICK, _DECIMALS)


class Side(Enum):
    BUY = "BUY"
    SELL = "SELL"


class StpPolicy(Enum):
    """What to do when an incoming order would trade against a resting order
    from the same participant_id. See LimitOrderBook._match for where this
    actually gets applied — it's a look-before-you-cross check, not a
    post-trade cleanup, because a trade that already printed can't be
    un-printed."""

    CANCEL_RESTING = "CANCEL_RESTING"  # pull the maker's resting order; the taker keeps matching
    CANCEL_NEWEST = "CANCEL_NEWEST"    # kill the whole incoming order the instant it would self-match


@dataclass
class Order:
    """A limit order. `quantity` is mutated as the order fills — the remaining
    (unfilled) size. `timestamp` is a simple int sequence number: lower = older,
    which is all time priority needs.

    participant_id is optional and defaults to None, which means "don't run
    self-trade prevention for this order" — every existing caller that never
    passes one keeps matching against its own resting orders exactly as
    before. Pass the same participant_id on two orders and the book will
    refuse to cross them; see StpPolicy for how."""

    order_id: int
    side: Side
    price: float
    quantity: int
    timestamp: int
    participant_id: str | None = None

    # Set False by LimitOrderBook.cancel(). The order stays physically in its
    # deque until the matching loop reaches it and throws it away - a
    # tombstone, which is how production engines get an O(1) cancel. Nothing
    # outside the book should read this: a cancelled order is gone as far as
    # every public method is concerned, and this flag is the mechanism, not
    # the interface.
    active: bool = True

    def __post_init__(self):
        self.price = to_tick(self.price)
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")


@dataclass(frozen=True)
class Trade:
    """One fill. Printed at the MAKER's (resting order's) price."""

    price: float
    quantity: int
    maker_id: int  # the resting order that supplied liquidity
    taker_id: int  # the incoming order that took it
