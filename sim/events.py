"""사건 로그 형식과 출력처(sink)."""
from dataclasses import dataclass, asdict
import json

@dataclass(frozen=True)
class Event:
    """사건 1건.

    (time_s, phase, stable_id)가 처리 순서 키다. phase: 0 도착·하차, 1 발생, 2 승차,
    3 출발, 4 기한 만료. occupancy는 사건 직후 차량 탑승 인원(차량 사건만).
    """
    time_s: int
    phase: int
    stable_id: str
    kind: str
    passenger_id: str | None = None
    vehicle_id: str | None = None
    occupancy: int | None = None

class NullSink:
    """로그를 저장하지 않는다(기본값, 성능 측정용)."""
    def emit(self, event): pass

class JsonlSink:
    """사건마다 JSON 한 줄을 스트림에 쓴다. 스트림은 호출자가 열고 닫으며 쓰기 오류는 그대로 전파한다."""
    def __init__(self, stream): self.stream = stream
    def emit(self, event):
        self.stream.write(json.dumps(asdict(event), sort_keys=True) + '\n')

class MemorySink:
    """테스트용. 최대 limit건까지 메모리에 모으고 넘치면 BufferError."""
    def __init__(self, limit=10000): self.events, self.limit = [], limit
    def emit(self, event):
        if len(self.events) >= self.limit: raise BufferError('event sink full')
        self.events.append(event)
