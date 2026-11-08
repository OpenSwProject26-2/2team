from dataclasses import dataclass, asdict
import json

@dataclass(frozen=True)
class Event:
    time_s: int
    phase: int
    stable_id: str
    kind: str
    passenger_id: str | None = None
    vehicle_id: str | None = None
    occupancy: int | None = None

class NullSink:
    def emit(self, event): pass

class JsonlSink:
    """Caller owns stream lifetime; failures propagate, never silently drop logs."""
    def __init__(self, stream): self.stream = stream
    def emit(self, event):
        self.stream.write(json.dumps(asdict(event), sort_keys=True) + '\n')

class MemorySink:
    """Bounded inspection sink, intended for tests only."""
    def __init__(self, limit=10000): self.events, self.limit = [], limit
    def emit(self, event):
        if len(self.events) >= self.limit: raise BufferError('event sink full')
        self.events.append(event)
