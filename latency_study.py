"""What does being slow actually cost a market maker?

Usage:  python latency_study.py

RESULTS.md's list of things this project doesn't model has always started with
latency, with the note that its absence means nothing here touches the actual
subject of low-latency trading. This is that section.

The setup is the smallest one that contains the real effect. Two market makers
quote the same size at the same prices around the same estimate of fair value.
They have identical information, identical inventory limits, identical
everything - the only difference between them is how long their messages take
to reach the book. Then flow arrives, some of it informed and some of it not,
and the book serves whatever reaches it first.

What makes this worth measuring rather than asserting: the slow maker does not
simply trade less. It trades *differently*, and the difference is the whole
reason latency is worth money.
"""

import numpy as np

from src.latency import LatencyModel, MessageBus
from src.order import Side, to_tick
from src.orderbook import LimitOrderBook
from src.simulator import seed_book

TICK = 0.01

ROUND_US = 2_000.0        # wall-clock spacing between episodes
N_ROUNDS = 4_000
QUOTE_SIZE = 100
EDGE_TICKS = 1            # how far off fair value each maker quotes

P_JUMP = 0.25             # chance the fair value jumps this round
JUMP_TICKS = 3.0
DIFFUSION_TICKS = 0.3     # ordinary drift in fair value between rounds

TAKER_LATENCY_US = 50.0   # how fast informed flow reacts to the jump
N_UNINFORMED = 2          # uninformed market orders per round
UNINFORMED_QTY = (20, 120)

FAST_US = 10.0
SLOW_LATENCIES = (10.0, 25.0, 50.0, 100.0, 250.0, 1_000.0)


class Maker:
    """A two-sided quoter that cancels and re-posts when fair value moves.

    Simple on purpose: no inventory skew, no spread widening, no learning. The
    point of the study is that two makers running *identical* logic get
    different outcomes, so any cleverness here would confound it.
    """

    def __init__(self, name: str, book: LimitOrderBook, owner: dict, settle):
        self.name = name
        self.book = book
        self.settle = settle
        # order_id -> (maker name, side), shared across makers. The book
        # reports a maker_id, which is an order id: a matching engine has no
        # opinion about who is making money, so attribution has to be kept
        # out here. Registering the ids the instant they are created matters -
        # look them up a round later and every fill against a quote that has
        # since been replaced goes unattributed, which silently biases the
        # result toward whichever maker replaces its quotes least often.
        self.owner = owner
        self.bid_id: int | None = None
        self.ask_id: int | None = None
        self.fills: list[tuple[Side, float, int, bool]] = []  # side, px, qty, informed

    def requote(self, fair: float) -> None:
        """Cancel both quotes and post new ones around `fair`.

        Runs at message-arrival time, not at decision time - it is handed to
        bus.send() rather than called directly, which is the entire mechanism
        being studied. By the time this executes, whatever else arrived first
        has already traded.
        """
        for attr in ("bid_id", "ask_id"):
            oid = getattr(self, attr)
            if oid is not None:
                self.book.cancel(oid)
                setattr(self, attr, None)
        # A requote is not always passive. After a 3-tick jump down, the new
        # ask sits below where the competition's stale bid still is, so posting
        # it *takes* that bid. That is latency arbitrage, it is what actually
        # happens on a real venue, and pretending the trades don't exist would
        # hide most of the effect being measured - so both halves are settled.
        bid = to_tick(fair - EDGE_TICKS * TICK)
        ask = to_tick(fair + EDGE_TICKS * TICK)
        self.bid_id, trades = self.book.add_limit_order(
            Side.BUY, bid, QUOTE_SIZE, participant_id=self.name)
        self.settle(trades, informed=True, taker=(self.name, Side.BUY))
        self.ask_id, trades = self.book.add_limit_order(
            Side.SELL, ask, QUOTE_SIZE, participant_id=self.name)
        self.settle(trades, informed=True, taker=(self.name, Side.SELL))
        self.owner[self.bid_id] = (self.name, Side.BUY)
        self.owner[self.ask_id] = (self.name, Side.SELL)


def run(slow_us: float, seed: int = 0) -> dict:
    """One race: a 10us maker against a `slow_us` maker, same flow, same book."""
    rng = np.random.default_rng(seed)
    book = LimitOrderBook()
    bus = MessageBus({
        "fast": LatencyModel(FAST_US, jitter_us=FAST_US * 0.2, seed=seed + 1),
        "slow": LatencyModel(slow_us, jitter_us=slow_us * 0.2, seed=seed + 2),
        "taker": LatencyModel(TAKER_LATENCY_US, seed=seed + 3),
    })

    fair = 100.0
    seed_book(book, mid=fair, levels=5, qty=50)
    owner: dict[int, tuple[str, Side]] = {}
    makers: dict[str, Maker] = {}

    # Fair value as of the end of the round a fill happened in. Every fill is
    # marked against this rather than against the final price of the run: the
    # question is whether the spread captured beat the information traded
    # against, and a mark 4,000 rounds later measures the random walk instead.
    markout = {"fair": fair}

    def settle(trades, informed: bool, taker=None) -> None:
        """Credit every side of every trade to whoever owned it."""
        for trade in trades:
            entry = owner.get(trade.maker_id)
            if entry is not None:
                name, side = entry
                makers[name].fills.append((side, trade.price, trade.quantity,
                                           informed, markout["fair"], "maker"))
            if taker is not None and taker[0] in makers:
                makers[taker[0]].fills.append(
                    (taker[1], trade.price, trade.quantity, informed,
                     markout["fair"], "taker"))

    for name in ("fast", "slow"):
        makers[name] = Maker(name, book, owner, settle)
    for maker in makers.values():
        maker.requote(fair)

    informed_volume = 0
    uninformed_volume = 0

    for round_index in range(N_ROUNDS):
        t0 = round_index * ROUND_US

        jumped = rng.random() < P_JUMP
        step = rng.normal(0, DIFFUSION_TICKS) * TICK
        direction = 1 if rng.random() < 0.5 else -1
        new_fair = fair + step + (direction * JUMP_TICKS * TICK if jumped else 0.0)
        markout["fair"] = new_fair

        # Both makers see the same trigger at the same instant. Only the wire
        # differs - which is the point, and is why they are given identical
        # information rather than a fast one with better data.
        for maker in makers.values():
            bus.send(t0, maker.name, lambda m=maker, f=new_fair: m.requote(f))

        if jumped:
            # Informed flow: knows which way fair value went and lifts the
            # stale side. It is slower than the fast maker's cancel and faster
            # than the slow maker's, which is the whole race.
            side = Side.SELL if direction < 0 else Side.BUY
            qty = QUOTE_SIZE * 2

            def informed_order(side=side, qty=qty):
                nonlocal informed_volume
                trades = book.market_order(side, qty, participant_id="taker")
                informed_volume += sum(t.quantity for t in trades)
                settle(trades, informed=True)

            bus.send(t0, "taker", informed_order)

        for _ in range(N_UNINFORMED):
            when = t0 + float(rng.uniform(0, ROUND_US))
            side = Side.BUY if rng.random() < 0.5 else Side.SELL
            qty = int(rng.integers(*UNINFORMED_QTY))

            def uninformed_order(side=side, qty=qty):
                nonlocal uninformed_volume
                trades = book.market_order(side, qty)
                uninformed_volume += sum(t.quantity for t in trades)
                settle(trades, informed=False)

            bus.send(when, "noise", uninformed_order)

        bus.deliver_until(t0 + ROUND_US)
        fair = new_fair

    bus.drain()

    out = {"slow_us": slow_us, "informed_volume": informed_volume,
           "uninformed_volume": uninformed_volume}
    for name, maker in makers.items():
        # Buying at 99.99 when fair settles at 100.00 is a 1-tick gain whether
        # or not the position was ever closed. Marking each fill against fair
        # value right after it happened is the standard markout, and it is the
        # only way to separate spread captured from information traded against.
        def pnl_of(fills):
            return sum((mark - price) * qty if side is Side.BUY
                       else (price - mark) * qty
                       for side, price, qty, _, mark, _ in fills)

        passive = [f for f in maker.fills if f[5] == "maker"]
        active = [f for f in maker.fills if f[5] == "taker"]
        volume = sum(f[2] for f in maker.fills)
        passive_volume = sum(f[2] for f in passive)
        active_volume = sum(f[2] for f in active)
        toxic = sum(f[2] for f in passive if f[3])
        out[name] = {
            "fills": len(maker.fills),
            "volume": volume,
            "passive_volume": passive_volume,
            "active_volume": active_volume,
            "toxic_share": toxic / passive_volume if passive_volume else 0.0,
            "pnl": pnl_of(maker.fills),
            "passive_ticks": (pnl_of(passive) / passive_volume / TICK
                              if passive_volume else 0.0),
            "active_ticks": (pnl_of(active) / active_volume / TICK
                             if active_volume else 0.0),
            "ticks_per_share": (pnl_of(maker.fills) / volume / TICK
                                if volume else 0.0),
        }
    return out


def main() -> None:
    print(f"Two makers, identical logic and identical information, quoting "
          f"{QUOTE_SIZE} x {EDGE_TICKS} tick either side of fair value.")
    print(f"{N_ROUNDS:,} rounds. Fair value jumps {JUMP_TICKS:.0f} ticks with "
          f"probability {P_JUMP:.0%}; informed flow reacts in "
          f"{TAKER_LATENCY_US:.0f}us.")
    print(f"The fast maker is fixed at {FAST_US:.0f}us. Only the slow one moves.")
    RESULTS = {slow_us: run(slow_us) for slow_us in SLOW_LATENCIES}

    header = (f"{'slow maker':>11} | {'quoted vol':>10} {'toxic%':>7} "
              f"{'passive':>8} {'took vol':>9} {'taking':>7} {'net':>7} "
              f"{'total':>10}")
    for label in ("fast maker (10us)", "slow maker"):
        print(f"\n{label}")
        print(header)
        print("-" * len(header))
        for slow_us in SLOW_LATENCIES:
            r = RESULTS[slow_us]["fast" if label.startswith("fast") else "slow"]
            print(f"{slow_us:>9.0f}us | {r['passive_volume']:>10,} "
                  f"{r['toxic_share']:>7.1%} {r['passive_ticks']:>8.3f} "
                  f"{r['active_volume']:>9,} {r['active_ticks']:>7.3f} "
                  f"{r['ticks_per_share']:>7.3f} {r['pnl'] / TICK:>10,.0f}")

    print("\n'quoted vol' is volume filled on this maker's own resting quotes;")
    print("'toxic%' is the share of that which came from informed flow.")
    print("'took vol' is volume where this maker was the aggressor - almost all")
    print("of it a requote crossing a competitor's stale quote after a jump.")
    print("All three tick columns are P&L per share, marked against fair value")
    print("right after the fill. One tick is the whole quoted edge, so 1.000")
    print("means every share earned the full spread and lost nothing to")
    print("information. 'total' is the whole run's P&L in ticks.")


if __name__ == "__main__":
    main()
