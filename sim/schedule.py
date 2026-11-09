"""운행 일정 검증."""
from .models import SimulationError, integer

def validate_schedule(trips):
    """운행편 목록이 실행 가능한지 검사한다. 하나라도 어기면 SimulationError.

    - 운행편 ID 중복 없음, 정원 ≥ 1, 정류장 2개 이상이고 한 운행편 안에서 중복 없음
    - 정류장마다 출발 ≥ 도착, 다음 정류장 도착 > 이전 출발, 복귀 ≥ 마지막 출발
    - 같은 차량의 운행편은 정원·출발 거점이 같고, 앞 운행편 복귀 뒤에 다음이 시작
    """
    ids, duties = set(), {}
    for t in trips:
        integer(t.capacity, 1)
        integer(t.distance_m)
        integer(t.return_s)
        if not t.trip_id or not t.vehicle_id or t.trip_id in ids:
            raise SimulationError('duplicate trip or missing vehicle')
        ids.add(t.trip_id)
        if len(t.stops) < 2 or len(set(t.stops)) != len(t.stops) or len(t.arrivals) != len(t.stops) or len(t.departures) != len(t.stops):
            raise SimulationError('invalid stop visits')
        for a, d in zip(t.arrivals, t.departures):
            integer(a); integer(d)
            if d < a: raise SimulationError('departure before arrival')
        if any(a <= d for d, a in zip(t.departures, t.arrivals[1:])) or t.return_s < t.departures[-1]:
            raise SimulationError('time reversal')
        duties.setdefault(t.vehicle_id, []).append(t)
    for duty in duties.values():
        duty.sort(key=lambda t: t.arrivals[0])
        if len({t.capacity for t in duty}) != 1 or len({t.stops[0] for t in duty}) != 1:
            raise SimulationError('inconsistent vehicle duty')
        if any(a.return_s > b.arrivals[0] for a,b in zip(duty,duty[1:])):
            raise SimulationError('overlapping vehicle duty')
