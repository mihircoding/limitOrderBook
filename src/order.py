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


@dataclass
class Order:
    """A limit order. `quantity` is mutated as the order fills — the remaining
    (unfilled) size. `timestamp` is a simple int sequence number: lower = older,
    which is all time priority needs."""

    order_id: int
    side: Side
    price: float
    quantity: int
    timestamp: int

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
