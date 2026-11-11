"""사건 실행 순서. 순서는 키로 직접 정하고, SimPy는 시간 진행만 맡는다."""
import heapq
import simpy
from .models import SimulationError, integer

class Dispatcher:
    """(time_s, phase, stable_id) 키가 작은 사건부터 실행한다.

    add: 사건 1건을 넣는다. add_stream: 이미 정렬된 (키, 콜백) 목록을 넣되 다음 1건만 힙에
    올려 두고 하나 실행할 때마다 다음 것을 꺼낸다(메모리 절약, 실행 순서는 전부 넣은 것과 같음).
    keys에는 아직 실행 안 된 키만 둔다. 이미 실행된 키는 `key <= last` 검사로 거부된다.
    """
    def __init__(self):
        self.env = simpy.Environment()
        self.queue = []
        self.keys = set()
        self.last = None
    def _check(self, key):
        # 중복 키, phase 범위 밖, 이미 지나간 시점의 키를 거부한다.
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
        # 스트림의 다음 1건만 힙에 올린다. 정렬이 깨져 있으면 실패한다.
        for key, callback in stream:
            self._check(key)
            if previous is not None and key <= previous:
                raise SimulationError('stream not sorted')
            self.keys.add(key)
            heapq.heappush(self.queue, (key, callback, stream))
            return
    def run(self, drain_s):
        """drain_s 이하 시각의 사건을 모두 실행한다. 그 뒤 사건은 실행하지 않는다."""
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
