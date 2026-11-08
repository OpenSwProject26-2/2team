"""M06 can consume compiled trip DTOs without a simulator-to-routing import."""
from typing import Protocol
from .events import Event
from .models import Trip

class EventSink(Protocol):
    def emit(self, event: Event) -> None: ...

class ScheduleConsumer(Protocol):
    def consume(self, plan_id: str, revision: int, trips: tuple[Trip, ...]) -> None: ...
