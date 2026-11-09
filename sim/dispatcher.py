"""One SimPy process owns explicit phase/stable-ID ordering."""
import heapq
import simpy
from .models import SimulationError, integer

class Dispatcher:
    """Executes (time_s, phase, stable_id) keys in ascending order.

    `add` pushes one event. `add_stream` lazily merges an already-sorted iterator of
    (key, callback): only its next item sits in the heap, so long sorted sequences do
    not occupy memory up front. Order is identical to pushing every item at once.
    `keys` holds pending keys only; processed keys are rejected by the `key <= last` rule.
    """
    def __init__(self):
        self.env = simpy.Environment()
        self.queue = []
        self.keys = set()
        self.last = None
    def _check(self, key):
        time_s, phase, _ = key
        integer(time_s); integer(phase)
        if phase > 4 or key in self.keys or (self.last is not None and key <= self.last):
            raise SimulationError('duplicate or retrograde event')
    def add(self, time_s, phase, stable_id, callback):
        key = (time_s, phase, stable_id)
        self._check(key)
        self.keys.add(key)
        heapq.heappush(self.queue, (key, callback, None))
    def add_stream(self, items):
        self._pull(iter(items), None)
    def _pull(self, stream, previous):
        for key, callback in stream:
            self._check(key)
            if previous is not None and key <= previous:
                raise SimulationError('stream not sorted')
            self.keys.add(key)
            heapq.heappush(self.queue, (key, callback, stream))
            return
    def run(self, drain_s):
        def loop():
            while self.queue and self.queue[0][0][0] <= drain_s:
                key, callback, stream = heapq.heappop(self.queue)
                self.keys.discard(key)
                yield self.env.timeout(key[0] - self.env.now)
                self.last = key
                callback(*key)
                if stream is not None: self._pull(stream, key)
        self.env.process(loop())
        self.env.run()
