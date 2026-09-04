"""The limit order book and matching engine.

Verify with:  pytest tests/test_orderbook.py

Internal representation:

    self.bids : dict[float, deque[Order]]   # price -> FIFO queue of resting orders
    self.asks : dict[float, deque[Order]]
    self._by_id : dict[int, Order]          # O(1) cancel lookup
    self._next_ts : int                     # monotonic sequence number

A deque per price level gives time priority for free: append to the back, fill
from the front. Best price is then max(bids) / min(asks) over the dict keys —
O(number of price levels), which is fine at this scale. Production books keep a
sorted structure, or an array indexed by tick, so best-price lookup is O(1).
That is a profiling-driven optimization, not where you start.
"""

from collections import deque

from .order import Order, Side, Trade


class LimitOrderBook:
    def __init__(self):
        self.bids: dict[float, deque] = {}
        self.asks: dict[float, deque] = {}
        self._by_id: dict[int, Order] = {}
        self._next_ts = 0
        self._next_id = 0

    # ---------- quotes ----------

    def best_bid(self) -> float | None:
        """Highest bid price with resting size, or None if no bids."""
        return max(self.bids) if self.bids else None

    def best_ask(self) -> float | None:
        """Lowest ask price with resting size, or None if no asks."""
        return min(self.asks) if self.asks else None

    def depth(self, side: Side, levels: int = 5) -> list[tuple[float, int]]:
        """Top `levels` price levels on one side as [(price, total_qty), ...].

        Best first: bids descend from the highest, asks ascend from the lowest.
        Quantities at a price are aggregated — the outside world sees size at a
        level, not the individual orders queued behind it.
        """
        book = self.bids if side is Side.BUY else self.asks
        prices = sorted(book, reverse=side is Side.BUY)[:levels]
        return [(price, sum(o.quantity for o in book[price])) for price in prices]

    # ---------- limit orders ----------

    def add_limit_order(self, side: Side, price: float, quantity: int) -> tuple[int, list[Trade]]:
        """Submit a limit order. Returns (order_id, trades).

        Marketable size is matched first, and whatever survives rests in the book.
        A limit order is therefore not a distinct object from a market order —
        it is a market order with a price floor, and this method is that floor
        plus _match().
        """
        order = Order(order_id=self._next_id, side=side, price=price,
                      quantity=quantity, timestamp=self._next_ts)
        self._next_id += 1
        self._next_ts += 1

        trades = self._match(order, limit_price=order.price)

        if order.quantity > 0:
            self._rest(order)

        return order.order_id, trades

    # ---------- IOC / FOK ----------

    def add_ioc_order(self, side: Side, price: float, quantity: int) -> list[Trade]:
        """Immediate-Or-Cancel: match whatever crosses right now, discard the
        rest. Same matching rules as a limit order (price improvement, maker
        priority, FIFO at a level) minus the one thing that makes a limit
        order a limit order - it never rests. A marketable IOC behaves exactly
        like a limit order that happened to fully fill; the only observable
        difference is what happens to the leftover when it doesn't.

        Used for "take what's there, don't leave a resting order that could
        get picked off a moment later" - the practical reason a trader
        reaches for IOC instead of a plain limit order.
        """
        order = Order(order_id=self._next_id, side=side, price=price,
                      quantity=quantity, timestamp=self._next_ts)
        self._next_id += 1
        self._next_ts += 1
        return self._match(order, limit_price=order.price)

    def add_fok_order(self, side: Side, price: float, quantity: int) -> list[Trade]:
        """Fill-Or-Kill: the whole order fills immediately at this price or
        better, or none of it does - no partial fills, nothing rests.

        The one order type here where checking-before-acting matters: _match
        mutates the book as it goes (decrements resting quantity, pops filled
        orders), so it cannot be run speculatively and then undone if the
        total turns out short. _fillable_quantity walks the same price levels
        read-only first; only if it clears the requested size does _match
        actually run.
        """
        book = self.asks if side is Side.BUY else self.bids
        available = self._fillable_quantity(book, side, price)
        if available < quantity:
            return []

        order = Order(order_id=self._next_id, side=side, price=price,
                      quantity=quantity, timestamp=self._next_ts)
        self._next_id += 1
        self._next_ts += 1
        trades = self._match(order, limit_price=order.price)
        assert order.quantity == 0, "fillable_quantity said this would fully fill"
        return trades

    def _fillable_quantity(self, book: dict, side: Side, limit_price: float) -> int:
        """How much of `book` is reachable at `limit_price` or better, without
        touching anything. Read-only twin of the crossing check inside
        _match - same price condition, no mutation, no side effects.
        """
        crossing_prices = [p for p in book if
                           (p <= limit_price if side is Side.BUY else p >= limit_price)]
        return sum(o.quantity for p in crossing_prices for o in book[p])

    # ---------- market orders and cancels ----------

    def market_order(self, side: Side, quantity: int) -> list[Trade]:
        """Fill against the opposite side until done or the book is empty.

        Any unfilled remainder is discarded: a market order has no price at which
        to rest. Real venues vary here (some convert the remainder to a limit at
        the last traded price), and it is worth knowing that is a venue rule, not
        a law of nature.
        """
        order = Order(order_id=self._next_id, side=side, price=0.0,
                      quantity=quantity, timestamp=self._next_ts)
        self._next_id += 1
        self._next_ts += 1
        return self._match(order, limit_price=None)

    def cancel(self, order_id: int) -> bool:
        """Remove a resting order. False if already filled, cancelled, or unknown.

        _by_id finds the Order in O(1); removing it from the middle of its deque
        is O(level size). The production trick is LAZY deletion — flag the order
        dead here and let the matching loop skip dead orders when they surface at
        the front — which trades a little memory for an O(1) cancel. Cancels
        vastly outnumber fills in real markets, so that trade is usually worth it.
        """
        order = self._by_id.pop(order_id, None)
        if order is None:
            return False

        book = self.bids if order.side is Side.BUY else self.asks
        queue = book.get(order.price)
        if queue is None:
            return False

        try:
            queue.remove(order)
        except ValueError:
            return False

        if not queue:
            del book[order.price]
        return True

    # ---------- matching internals ----------

    def _match(self, incoming: Order, limit_price: float | None) -> list[Trade]:
        """Consume the opposite side while the incoming order still crosses.

        The ONLY place matching logic lives. A market order is this loop with
        limit_price=None, i.e. with the price condition switched off — which is
        why market and limit orders cannot drift out of agreement here.

        Priority is price first, then time: the best opposite price is picked each
        pass, and the deque's left end is the oldest order resting at it.
        """
        book = self.asks if incoming.side is Side.BUY else self.bids
        trades: list[Trade] = []

        while incoming.quantity > 0 and book:
            best = min(book) if incoming.side is Side.BUY else max(book)

            if limit_price is not None:
                crosses = (best <= limit_price if incoming.side is Side.BUY
                           else best >= limit_price)
                if not crosses:
                    break

            queue = book[best]
            resting = queue[0]
            fill = min(incoming.quantity, resting.quantity)

            # Printed at the MAKER's price. The taker crossed the spread and gets
            # price improvement if they were willing to pay more; the resting
            # order's price is what it agreed to.
            trades.append(Trade(price=resting.price, quantity=fill,
                                maker_id=resting.order_id,
                                taker_id=incoming.order_id))

            incoming.quantity -= fill
            resting.quantity -= fill

            if resting.quantity == 0:
                queue.popleft()
                self._by_id.pop(resting.order_id, None)
                if not queue:
                    del book[best]

        return trades

    def _rest(self, order: Order) -> None:
        """Append an order to the back of the FIFO queue at its price level."""
        book = self.bids if order.side is Side.BUY else self.asks
        book.setdefault(order.price, deque()).append(order)
        self._by_id[order.order_id] = order

    # ---------- convenience ----------

    def mid_price(self) -> float | None:
        bb, ba = self.best_bid(), self.best_ask()
        if bb is None or ba is None:
            return None
        return (bb + ba) / 2

    def spread(self) -> float | None:
        bb, ba = self.best_bid(), self.best_ask()
        if bb is None or ba is None:
            return None
        return ba - bb
