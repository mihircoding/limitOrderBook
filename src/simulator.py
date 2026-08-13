"""Zero-intelligence order flow through the book.

Verify with:  pytest tests/test_simulator.py

"Zero intelligence" (Gode & Sunder, 1993): agents submit random orders with no
strategy whatsoever. The remarkable result is how much realistic market behavior
— spreads, depth profiles, price impact, a random-walking mid — emerges from the
matching mechanics alone. If a book of coin-flipping traders looks like a real
one, the realism was in the exchange rules, not the traders.

That is a genuinely useful null hypothesis. Any "stylized fact" a zero-intelligence
book reproduces is not evidence of anything about trader behavior.
"""

import numpy as np

from .order import Side, to_tick
from .orderbook import LimitOrderBook

TICK = 0.01

# event mix — roughly the shape of real equity flow, where cancels are common
P_LIMIT, P_MARKET, P_CANCEL = 0.60, 0.25, 0.15
P_PASSIVE = 0.80          # share of limit orders placed inside their own side
OFFSET_TICKS = 3.0        # scale of |N(0, .)| price offset from the mid
MARKET_QTY = (10, 200)
LIMIT_QTY = (10, 200)


def seed_book(book: LimitOrderBook, mid: float = 100.0, levels: int = 5,
              qty: int = 100) -> None:
    """Pre-load `levels` price levels either side of `mid`.

    Without this the book starts empty, the mid is undefined, and there is
    nothing for the first market order to trade against.
    """
    for i in range(1, levels + 1):
        book.add_limit_order(Side.BUY, to_tick(mid - TICK * i), qty)
        book.add_limit_order(Side.SELL, to_tick(mid + TICK * i), qty)


def simulate(book: LimitOrderBook, n_events: int = 5000, seed: int = 0) -> dict:
    """Push random events through the book and record what happens.

    Returns {'mids', 'spreads', 'n_trades', 'volume', 'impacts'} where `impacts`
    holds (market order size, absolute mid move) pairs — enough to measure price
    impact against order size afterwards.
    """
    rng = np.random.default_rng(seed)

    mids: list[float] = []
    spreads: list[float] = []
    impacts: list[tuple[int, float]] = []
    n_trades = 0
    volume = 0

    live_ids: list[int] = list(book._by_id)
    last_mid = book.mid_price() or 100.0

    for _ in range(n_events):
        roll = rng.random()
        mid = book.mid_price() or last_mid

        if roll < P_LIMIT:
            side = Side.BUY if rng.random() < 0.5 else Side.SELL
            offset = abs(rng.normal(0, OFFSET_TICKS)) * TICK
            passive = rng.random() < P_PASSIVE

            # Passive orders sit behind the mid; aggressive ones reach across it.
            # The aggressive minority is what makes limit orders trade at all,
            # and it is why the spread does not simply widen forever.
            if side is Side.BUY:
                price = mid - offset if passive else mid + offset
            else:
                price = mid + offset if passive else mid - offset

            qty = int(rng.integers(*LIMIT_QTY))
            oid, trades = book.add_limit_order(side, to_tick(price), qty)
            if oid in book._by_id:
                live_ids.append(oid)

        elif roll < P_LIMIT + P_MARKET:
            side = Side.BUY if rng.random() < 0.5 else Side.SELL
            qty = int(rng.integers(*MARKET_QTY))
            before = book.mid_price()
            trades = book.market_order(side, qty)
            after = book.mid_price()
            if before is not None and after is not None:
                impacts.append((qty, abs(after - before)))

        else:
            trades = []
            if live_ids:
                idx = int(rng.integers(0, len(live_ids)))
                # Stale ids are left in the list on purpose: cancel() returning
                # False for an already-filled order is exactly what a real
                # gateway sees when a cancel races a fill.
                book.cancel(live_ids[idx])
                live_ids[idx] = live_ids[-1]
                live_ids.pop()

        n_trades += len(trades)
        volume += sum(t.quantity for t in trades)

        current_mid, current_spread = book.mid_price(), book.spread()
        if current_mid is not None:
            mids.append(current_mid)
            spreads.append(current_spread)
            last_mid = current_mid

    return {"mids": mids, "spreads": spreads, "n_trades": n_trades,
            "volume": volume, "impacts": impacts}
