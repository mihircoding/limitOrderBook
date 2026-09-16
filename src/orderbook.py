"""The limit order book and matching engine.

Verify with:  pytest tests/test_orderbook.py

Internal representation:

    self.bids : dict[float, deque[Order]]   # price -> FIFO queue of resting orders
    self.asks : dict[float, deque[Order]]
    self._by_id : dict[int, Order]          # O(1) cancel lookup
    self._next_ts : int                     # monotonic sequence number

A deque per price level gives time priority for free: append to the back, fill
from the front.

Best price used to be max(bids) / min(asks) over the dict keys, which is
O(number of price levels) and gets read on every quote, every pass of the
matching loop and every event the simulator generates. `benchmark.py` says
that cost is real: the lookup grows linearly with depth (446ns at 10 levels,
7,922ns at 1,000) and drags the whole simulation down with it, from 91k
events/sec at 10 levels to 12.5k at 1,000. So each side also keeps a heap of
its live price levels: O(1) to read the best price, O(log L) to insert a new
one. Same sweep with the heaps: 98k events/sec at 10 levels, 44k at 1,000.

Two things in here are deleted LAZILY, for the same reason in both cases: the
eager version has to walk a structure, and the lazy one lets whatever passes
by next do the work instead.

Price levels in the heaps. Emptying a level does not remove it from the heap —
the entry is dropped when it surfaces at the top and turns out to be gone.
Removing an arbitrary element from a binary heap is O(L); letting the stale
entry pay for itself later is O(log L) amortized and no bookkeeping at the
deletion site. `_in_heap` keeps at most one entry per distinct price, so a
level that is repeatedly created and destroyed can't grow the heap without
bound.

Cancelled orders in the queues. cancel() flags the order and leaves it where
it is; the matching loop discards these tombstones when they reach the front
of a queue. Splicing one out of the middle of a deque is O(orders at that
level), which benchmark.py caught as an 88x gap between the median cancel and
the 99th percentile — on the operation that is most of real order flow.
`_bid_live` / `_ask_live` count the live orders per level so an emptied level
still leaves the book in O(1), which is also what bounds the tombstones: the
price key is deleted, and the deque goes with it.
"""

import heapq
from collections import deque

from .order import Order, Side, StpPolicy, Trade


class LimitOrderBook:
    def __init__(self):
        self.bids: dict[float, deque] = {}
        self.asks: dict[float, deque] = {}
        self._by_id: dict[int, Order] = {}
        self._next_ts = 0
        self._next_id = 0

        # Price-level heaps. Python's heapq is a min-heap, so bids are stored
        # negated to make the highest price come out first.
        self._bid_heap: list[float] = []
        self._ask_heap: list[float] = []
        self._bid_in_heap: set[float] = set()
        self._ask_in_heap: set[float] = set()

        # Live (non-cancelled) order count per price level. cancel() is lazy —
        # it flags the order and leaves it in its deque — so the deque's length
        # stops being a reliable answer to "is this level still there". These
        # counters are: they're what tells cancel() when it has emptied a level
        # and the price key has to come out of the book, in O(1), without
        # walking anything.
        self._bid_live: dict[float, int] = {}
        self._ask_live: dict[float, int] = {}

    # ---------- level bookkeeping ----------

    def _side_state(self, side: Side) -> tuple[dict, list, set, dict, float]:
        """(book, heap, in_heap, live_counts, heap_sign) for one side.

        One place that knows which of the parallel structures belongs to which
        side. They have to stay in step — a price in `book` with no live count,
        or a live count with no price, is a corrupt book — and four call sites
        each re-deriving the mapping is how they drift apart.
        """
        if side is Side.BUY:
            return self.bids, self._bid_heap, self._bid_in_heap, self._bid_live, -1.0
        return self.asks, self._ask_heap, self._ask_in_heap, self._ask_live, 1.0

    def _drop_live(self, side: Side, price: float) -> None:
        """One live order left this price level: filled, cancelled or pulled by
        self-trade prevention. When the last one goes, the level goes with it —
        the price key is deleted even though the deque may still hold
        tombstones, which is what keeps best_bid(), depth() and the heaps
        honest about a level nobody is quoting any more. Deleting the key drops
        the whole deque, so the tombstones are collected at the same moment.
        """
        book, _, _, live, _ = self._side_state(side)
        remaining = live.get(price, 0) - 1
        if remaining > 0:
            live[price] = remaining
            return
        live.pop(price, None)
        book.pop(price, None)

    # ---------- quotes ----------

    def _best_price(self, side: Side) -> float | None:
        """Best resting price on one side, or None if that side is empty.

        Amortized O(1): the answer is at the top of the heap. The loop is not
        a scan — it discards levels that have since emptied, and each stale
        entry is discarded exactly once over the life of the book.
        """
        book, heap, in_heap, _, sign = self._side_state(side)

        while heap:
            price = sign * heap[0]
            if price in book:
                return price
            heapq.heappop(heap)
            in_heap.discard(price)

        return None

    def best_bid(self) -> float | None:
        """Highest bid price with resting size, or None if no bids."""
        return self._best_price(Side.BUY)

    def best_ask(self) -> float | None:
        """Lowest ask price with resting size, or None if no asks."""
        return self._best_price(Side.SELL)

    def depth(self, side: Side, levels: int = 5) -> list[tuple[float, int]]:
        """Top `levels` price levels on one side as [(price, total_qty), ...].

        Best first: bids descend from the highest, asks ascend from the lowest.
        Quantities at a price are aggregated — the outside world sees size at a
        level, not the individual orders queued behind it.
        """
        book = self.bids if side is Side.BUY else self.asks
        prices = sorted(book, reverse=side is Side.BUY)[:levels]
        return [(price, sum(o.quantity for o in book[price] if o.active))
                for price in prices]

    # ---------- limit orders ----------

    def add_limit_order(self, side: Side, price: float, quantity: int,
                        participant_id: str | None = None,
                        stp_policy: StpPolicy = StpPolicy.CANCEL_RESTING
                        ) -> tuple[int, list[Trade]]:
        """Submit a limit order. Returns (order_id, trades).

        Marketable size is matched first, and whatever survives rests in the book.
        A limit order is therefore not a distinct object from a market order —
        it is a market order with a price floor, and this method is that floor
        plus _match().

        participant_id / stp_policy: opt-in self-trade prevention. Leave
        participant_id as None (the default) and this behaves exactly as
        before — the book will happily cross two orders from the same trader,
        which is realistic for a book with no concept of "trader" at all.
        Pass a participant_id and _match() will refuse to print a trade
        against that same id; see StpPolicy for the two ways it can refuse.
        Under CANCEL_NEWEST the whole order can be killed before ever
        resting, so the "rests" step below still checks order.quantity > 0.
        """
        order = Order(order_id=self._next_id, side=side, price=price,
                      quantity=quantity, timestamp=self._next_ts,
                      participant_id=participant_id)
        self._next_id += 1
        self._next_ts += 1

        trades = self._match(order, limit_price=order.price, stp_policy=stp_policy)

        if order.quantity > 0:
            self._rest(order)

        return order.order_id, trades

    # ---------- IOC / FOK ----------

    def add_ioc_order(self, side: Side, price: float, quantity: int,
                      participant_id: str | None = None,
                      stp_policy: StpPolicy = StpPolicy.CANCEL_RESTING) -> list[Trade]:
        """Immediate-Or-Cancel: match whatever crosses right now, discard the
        rest. Same matching rules as a limit order (price improvement, maker
        priority, FIFO at a level) minus the one thing that makes a limit
        order a limit order - it never rests. A marketable IOC behaves exactly
        like a limit order that happened to fully fill; the only observable
        difference is what happens to the leftover when it doesn't.

        Used for "take what's there, don't leave a resting order that could
        get picked off a moment later" - the practical reason a trader
        reaches for IOC instead of a plain limit order.

        participant_id / stp_policy: see add_limit_order.
        """
        order = Order(order_id=self._next_id, side=side, price=price,
                      quantity=quantity, timestamp=self._next_ts,
                      participant_id=participant_id)
        self._next_id += 1
        self._next_ts += 1
        return self._match(order, limit_price=order.price, stp_policy=stp_policy)

    def add_fok_order(self, side: Side, price: float, quantity: int,
                      participant_id: str | None = None,
                      stp_policy: StpPolicy = StpPolicy.CANCEL_RESTING) -> list[Trade]:
        """Fill-Or-Kill: the whole order fills immediately at this price or
        better, or none of it does - no partial fills, nothing rests.

        The one order type here where checking-before-acting matters: _match
        mutates the book as it goes (decrements resting quantity, pops filled
        orders), so it cannot be run speculatively and then undone if the
        total turns out short. _fillable_quantity walks the same price levels
        read-only first; only if it clears the requested size does _match
        actually run.

        participant_id / stp_policy: see add_limit_order. _fillable_quantity
        has to know about both, because self-trade prevention changes how
        much of the book is honestly reachable - liquidity behind your own
        resting order either doesn't count at all (CANCEL_RESTING skips your
        own orders and keeps walking past them) or stops counting the moment
        it's reached (CANCEL_NEWEST would kill the whole order right there).
        Getting this wrong would mean a FOK either fires and then partially
        self-cancels mid-match (breaking the whole point of FOK) or rejects
        an order that could actually have filled clean.
        """
        book = self.asks if side is Side.BUY else self.bids
        available = self._fillable_quantity(book, side, price, participant_id, stp_policy)
        if available < quantity:
            return []

        order = Order(order_id=self._next_id, side=side, price=price,
                      quantity=quantity, timestamp=self._next_ts,
                      participant_id=participant_id)
        self._next_id += 1
        self._next_ts += 1
        trades = self._match(order, limit_price=order.price, stp_policy=stp_policy)
        assert order.quantity == 0, "fillable_quantity said this would fully fill"
        return trades

    def _fillable_quantity(self, book: dict, side: Side, limit_price: float,
                           participant_id: str | None = None,
                           stp_policy: StpPolicy = StpPolicy.CANCEL_RESTING) -> int:
        """How much of `book` is reachable at `limit_price` or better, without
        touching anything. Read-only twin of the crossing check inside
        _match - same price condition, no mutation, no side effects - and,
        when participant_id is given, the same self-trade rule too: walked in
        the exact price/time priority _match would use, since which orders
        count depends on what's reached BEFORE the requested size is used up,
        not just what's out there in total.
        """
        crossing_prices = sorted(
            (p for p in book if (p <= limit_price if side is Side.BUY else p >= limit_price)),
            reverse=(side is Side.SELL),
        )
        total = 0
        for price in crossing_prices:
            for order in book[price]:  # deque is already FIFO / time-priority order
                if not order.active:
                    continue  # a cancelled order is not liquidity, tombstone or not
                if participant_id is not None and order.participant_id == participant_id:
                    if stp_policy is StpPolicy.CANCEL_NEWEST:
                        return total  # would be killed on contact; nothing past here counts
                    continue  # CANCEL_RESTING: this order gets pulled, not counted, keep walking
                total += order.quantity
        return total

    # ---------- market orders and cancels ----------

    def market_order(self, side: Side, quantity: int, participant_id: str | None = None,
                     stp_policy: StpPolicy = StpPolicy.CANCEL_RESTING) -> list[Trade]:
        """Fill against the opposite side until done or the book is empty.

        Any unfilled remainder is discarded: a market order has no price at which
        to rest. Real venues vary here (some convert the remainder to a limit at
        the last traded price), and it is worth knowing that is a venue rule, not
        a law of nature.

        participant_id / stp_policy: see add_limit_order.
        """
        order = Order(order_id=self._next_id, side=side, price=0.0,
                      quantity=quantity, timestamp=self._next_ts,
                      participant_id=participant_id)
        self._next_id += 1
        self._next_ts += 1
        return self._match(order, limit_price=None, stp_policy=stp_policy)

    def cancel(self, order_id: int) -> bool:
        """Remove a resting order. False if already filled, cancelled, or unknown.

        LAZY deletion, which is what real engines do. This used to splice the
        order out of the middle of its deque with `deque.remove()`, which is
        O(orders at that level): cancelling at the front of a deep queue was
        instant and cancelling at the back walked the whole thing. benchmark.py
        found exactly that shape — a 1.9us median against a 170us p99, an 88x
        gap, on the operation that dominates real order flow.

        So: flag the order dead, drop its id, decrement the level's live count,
        and leave the object where it is. The matching loop throws tombstones
        away when they surface at the front of a queue, which is the one moment
        the work is unavoidable anyway. Nothing walks anything here, so the
        cost no longer depends on where in the queue the order was sitting.

        What it costs is memory: a cancelled order occupies its slot until the
        matching loop reaches it, and a level that never trades never reaches
        it. The bound is the whole point of _drop_live() deleting an emptied
        level outright — that drops its deque, tombstones and all. benchmark.py
        measures the leftovers rather than assuming they stay small.
        """
        order = self._by_id.pop(order_id, None)
        if order is None or not order.active:
            return False

        order.active = False
        self._drop_live(order.side, order.price)
        return True

    # ---------- matching internals ----------

    def _match(self, incoming: Order, limit_price: float | None,
              stp_policy: StpPolicy = StpPolicy.CANCEL_RESTING) -> list[Trade]:
        """Consume the opposite side while the incoming order still crosses.

        The ONLY place matching logic lives. A market order is this loop with
        limit_price=None, i.e. with the price condition switched off — which is
        why market and limit orders cannot drift out of agreement here.

        Priority is price first, then time: the best opposite price is picked each
        pass, and the deque's left end is the oldest order resting at it.

        Self-trade prevention lives here too, as a check BEFORE a trade is
        built, not a cleanup after: once a Trade is appended it's a fact that
        happened, so the only place to refuse a self-match is right before
        the fill math runs. incoming.participant_id is None (the default)
        skips this entirely - two orders from the same untracked "no
        participant" identity are still allowed to cross, same as always.
        """
        book = self.asks if incoming.side is Side.BUY else self.bids
        trades: list[Trade] = []

        opposite = Side.SELL if incoming.side is Side.BUY else Side.BUY

        while incoming.quantity > 0 and book:
            best = self._best_price(opposite)
            if best is None:
                break

            if limit_price is not None:
                crosses = (best <= limit_price if incoming.side is Side.BUY
                           else best >= limit_price)
                if not crosses:
                    break

            queue = book[best]

            # Tombstones from cancel() sit in the queue until something
            # reaches them. This is that something: discarding one is a
            # popleft, and each cancelled order is discarded exactly once
            # over the life of the book, so the loop is amortized O(1) and
            # not a scan.
            while queue and not queue[0].active:
                queue.popleft()
            if not queue:
                # Only reachable if a level's live count disagreed with its
                # queue, which would be a bug in the bookkeeping rather than
                # a state the book can legitimately be in. Clean up and keep
                # going rather than raise inside a matching loop.
                book.pop(best, None)
                self._side_state(opposite)[3].pop(best, None)
                continue

            resting = queue[0]

            if (incoming.participant_id is not None
                    and resting.participant_id == incoming.participant_id):
                if stp_policy is StpPolicy.CANCEL_NEWEST:
                    # Kill the whole incoming order right here - it never
                    # rests either (add_limit_order checks quantity > 0).
                    incoming.quantity = 0
                    break
                # CANCEL_RESTING: pull the resting order, no trade, keep
                # trying to match the incoming order against everyone else.
                queue.popleft()
                resting.active = False
                self._by_id.pop(resting.order_id, None)
                self._drop_live(opposite, best)
                continue

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
                resting.active = False
                self._by_id.pop(resting.order_id, None)
                self._drop_live(opposite, best)

        return trades

    def _rest(self, order: Order) -> None:
        """Append an order to the back of the FIFO queue at its price level.

        A brand-new price level also has to enter that side's heap. `_in_heap`
        is what makes the push conditional: without it, a level that empties
        and refills — which is most of them, in a book with a live spread —
        would push a duplicate every time and the heap would grow forever
        even though the number of distinct prices never changes.
        """
        book, heap, in_heap, live, sign = self._side_state(order.side)

        book.setdefault(order.price, deque()).append(order)
        self._by_id[order.order_id] = order
        live[order.price] = live.get(order.price, 0) + 1

        if order.price not in in_heap:
            in_heap.add(order.price)
            heapq.heappush(heap, sign * order.price)

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
