"""다른 모듈과 주고받는 인터페이스. M06은 시뮬레이터를 import하지 않고 일정만 받는다."""
from typing import Protocol
from .events import Event
from .models import Trip

class EventSink(Protocol):
    """사건 로그 출력처."""
    def emit(self, event: Event) -> None: ...

class ScheduleConsumer(Protocol):
    """M06(경로 안내)이 운행안의 운행편 일정을 받는 포트."""
    def consume(self, plan_id: str, revision: int, trips: tuple[Trip, ...]) -> None: ...
