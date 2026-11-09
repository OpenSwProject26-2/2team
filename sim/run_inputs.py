"""고정 실행 입력(RunInputs). 비교하는 운행안은 모두 같은 RunInputs를 쓴다."""
from dataclasses import dataclass, asdict
from datetime import date
import hashlib
import json
from .models import Passenger, SimulationError, integer

def digest(value):
    """정규 JSON(키 정렬, 공백 없음)의 SHA-256."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

@dataclass(frozen=True)
class RunInputs:
    """한 번의 실행에 쓰는 승객·주행시간·출처. 생성 시 중복 ID와 값을 검사한다.

    seed는 기록용이다. seed가 같다고 입력이 같다고 보지 않고 checksum으로 비교한다.
    """
    input_bundle_id: str
    scenario_id: str
    source_checksum: str
    service_date: str
    passengers: tuple[Passenger, ...]
    travel_times: tuple[tuple[str, int], ...]
    seed: str
    def __post_init__(self):
        date.fromisoformat(self.service_date)
        if any(not isinstance(x, str) or not x.strip() for x in
               (self.input_bundle_id, self.scenario_id, self.source_checksum, self.seed)):
            raise SimulationError('missing provenance')
        ids = set()
        for p in self.passengers:
            if not p.passenger_id or p.passenger_id in ids or not p.origin or not p.destination or p.origin == p.destination:
                raise SimulationError('invalid passenger or duplicate ID')
            ids.add(p.passenger_id)
            integer(p.time_s)
        if len(dict(self.travel_times)) != len(self.travel_times):
            raise SimulationError('duplicate segment realization')
        for sid, value in self.travel_times:
            if not sid: raise SimulationError('missing segment ID')
            integer(value, 1)
    @property
    def checksum(self):
        """digest(asdict(self))와 같은 값. 승객 전체를 한 번에 복사하지 않고 한 명씩 해시한다."""
        dump = json.dumps
        h = hashlib.sha256()
        h.update(('{"input_bundle_id":' + dump(self.input_bundle_id) + ',"passengers":[').encode())
        for k, p in enumerate(self.passengers):
            h.update(((',' if k else '') + '{"destination":' + dump(p.destination) + ',"origin":'
                      + dump(p.origin) + ',"passenger_id":' + dump(p.passenger_id) + ',"time_s":'
                      + str(p.time_s) + '}').encode())
        h.update(('],"scenario_id":' + dump(self.scenario_id) + ',"seed":' + dump(self.seed)
                  + ',"service_date":' + dump(self.service_date) + ',"source_checksum":'
                  + dump(self.source_checksum) + ',"travel_times":'
                  + json.dumps(self.travel_times, separators=(',', ':')) + '}').encode())
        return h.hexdigest()

def expand(rows):
    """정확 시각 집계 (집계 ID, 출발, 도착, arrival_s, 인원)를 1인 단위 승객으로 펼친다.

    승객 ID는 ["집계 ID", 순번] JSON이다.
    """
    result = []
    seen = set()
    for key, origin, destination, arrival, count in sorted(rows):
        integer(count)
        integer(arrival)
        if key in seen: raise SimulationError('duplicate aggregate ID')
        seen.add(key)
        for i in range(count):
            result.append(Passenger(json.dumps([key, i], separators=(',', ':')), origin, destination, arrival))
    return tuple(result)
