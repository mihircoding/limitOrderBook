"""Latency: messages arrive when they arrive, not when they were sent.

Verify with:  pytest tests/test_latency.py

RESULTS.md's "what isn't modeled" list has always led with this one: the book
processes every order the instant it is created, so nothing in the project
touched the actual subject of low-latency trading. That is a big omission,
because in a real market the matching engine is not the interesting clock. The
interesting clock is the wire.

Everything here rests on one fact about a matching engine: it does not know
when you decided. It knows when your packet arrived, and it serves arrivals in
order. So two participants who react to the same public event at the same
instant do not reach the book at the same instant, and the one who arrives
second is not merely late - they are trading against someone who already knows
what they are about to do.

The model is deliberately thin:

    send(sent_at, participant, action)   ->   runs at sent_at + latency(participant)

`MessageBus` is a priority queue keyed by (arrival, sequence). The sequence
number is the tie-break, and it is not a detail: two messages arriving in the
same nanosecond have to be ordered *somehow*, and a real venue orders them by
the port they came in on. Ordering them by submission order instead would hand
the tie to whoever decided first, which is exactly the advantage latency is
supposed to take away.

What this is not: a network simulator. There is no queueing at the gateway, no
packet loss, no serialization delay that grows with message size, and no
matching-engine processing time. Those all matter in practice and none of them
change the effect being measured here.
"""

import heapq
from dataclasses import dataclass, field

import numpy as np

__all__ = ["LatencyModel", "MessageBus", "Message"]


@dataclass
class LatencyModel:
    """One-way latency for a participant, in microseconds.

    `base_us` is the floor - speed of light plus switches plus however long
    the participant's own code takes. `jitter_us` is the exponential tail on
    top of it. Exponential rather than normal because that is the shape real
    latency has: a hard floor you cannot beat, a typical value just above it,
    and an occasional very bad draw. A symmetric distribution around a mean
    would imply messages that sometimes arrive faster than physics allows.

    Colocated HFT round trips are single-digit microseconds; a retail order
    routed through a broker is measured in milliseconds. Both are expressible
    here, and the gap between them is four orders of magnitude - which is why
    the study in latency_study.py compares 10us against 250us rather than
    against 12us.
    """

    base_us: float
    jitter_us: float = 0.0
    seed: int | None = None

    def __post_init__(self):
        if self.base_us < 0 or self.jitter_us < 0:
            raise ValueError("latency cannot be negative")
        self._rng = np.random.default_rng(self.seed)

    def sample(self) -> float:
        """One draw, in microseconds."""
        if self.jitter_us == 0:
            return self.base_us
        return self.base_us + float(self._rng.exponential(self.jitter_us))


@dataclass(order=True)
class Message:
    """A pending action, ordered by (arrival, sequence).

    `action` is excluded from comparison because it is a callable and callables
    have no ordering; including it would make a tie between two equal arrivals
    raise a TypeError instead of falling through to the sequence number.
    """

    arrival_us: float
    sequence: int
    participant: str = field(compare=False, default="")
    action: object = field(compare=False, default=None)


class MessageBus:
    """Delivers actions to the book in arrival order.

    Usage is deliberately blunt: `send()` schedules, `deliver_until()` runs
    everything that has arrived by a given time, and the caller advances the
    clock. Nothing here knows what a book or an order is, which is what keeps
    the latency model separable from the matching engine - the same property
    that lets a real firm test its strategy against a book it did not write.
    """

    def __init__(self, latencies: dict[str, LatencyModel] | None = None):
        self.latencies = latencies or {}
        self._queue: list[Message] = []
        self._sequence = 0
        self.delivered = 0
        self.now_us = 0.0

    def register(self, participant: str, latency: LatencyModel) -> None:
        self.latencies[participant] = latency

    def send(self, sent_at_us: float, participant: str, action) -> float:
        """Schedule `action` for delivery. Returns its arrival time.

        A participant with no registered latency model is treated as
        instantaneous. That is a real case - it is how the exchange's own
        housekeeping messages behave - and it is also the default that makes
        every existing caller of the book unaffected by this module.
        """
        latency = self.latencies.get(participant)
        delay = latency.sample() if latency is not None else 0.0
        arrival = sent_at_us + delay
        heapq.heappush(self._queue, Message(arrival, self._sequence,
                                            participant, action))
        self._sequence += 1
        return arrival

    def deliver_until(self, time_us: float) -> int:
        """Run every message that has arrived by `time_us`, oldest first.

        Actions may themselves call send(), which is how a strategy reacting to
        its own fill is expressed. A message scheduled for a time that has
        already passed is delivered in this same call, which is correct: it
        arrived.
        """
        count = 0
        while self._queue and self._queue[0].arrival_us <= time_us:
            message = heapq.heappop(self._queue)
            self.now_us = message.arrival_us
            if message.action is not None:
                message.action()
            count += 1
            self.delivered += 1
        self.now_us = max(self.now_us, time_us)
        return count

    def drain(self) -> int:
        """Deliver everything still pending. For end-of-run cleanup."""
        return self.deliver_until(float("inf"))

    def pending(self) -> int:
        return len(self._queue)
