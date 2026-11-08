"""One SimPy process owns explicit phase/stable-ID ordering."""
import heapq
import simpy
from .models import SimulationError, integer

class Dispatcher:
    def __init__(self):
        self.env = simpy.Environment()
        self.queue = []
        self.keys = set()
        self.last = None
    def add(self, time_s, phase, stable_id, callback):
        integer(time_s); integer(phase)
        key = (time_s, phase, stable_id)
        if phase > 4 or key in self.keys or (self.last is not None and key <= self.last):
            raise SimulationError('duplicate or retrograde event')
        self.keys.add(key)
        heapq.heappush(self.queue, (key, callback))
    def run(self, drain_s):
        def loop():
            while self.queue and self.queue[0][0][0] <= drain_s:
                key, callback = heapq.heappop(self.queue)
                yield self.env.timeout(key[0] - self.env.now)
                self.last = key
                callback(*key)
        self.env.process(loop())
        self.env.run()
