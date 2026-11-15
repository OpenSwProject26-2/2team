"""명세 §4 brute-force 재계산 oracle. 엔진과 다른 구조로 구현한다.

엔진은 정류장·목적지별 FIFO 대기열의 머리를 k-way 병합하고 자격을 잃은 항목을 지연
제거한다. 여기서는 **대기열을 쓰지 않는다**. 사건 키를 전부 만들어 한 번 정렬하고,
순서대로 돌면서 상태를 승객 배열·차량 배열로만 유지한다. 승차할 때마다 자격 승객을
선형 탐색으로 고르고 정렬한다. 증분 유지·지연 삭제가 없다.

scan='full'  (기본) 명세 문장 그대로 매 승차마다 전체 승객을 훑는다. complex 1천 명
             (drain 안 승차 방문 약 43,000건 × 승객 1,000명)에서 0.64초로 충분히 빠르다.
scan='index' 출발 정류장으로 후보를 먼저 가른다. 상태·목적지 판정과 정렬은 그대로
             선형 탐색이며 대기열이 아니다. 큰 규모에서만 쓰고, 두 방식의 결과가 같은지
             테스트로 고정한다.
"""
from dataclasses import dataclass, field

PHASE_ALIGHT, PHASE_GENERATE, PHASE_BOARD, PHASE_DEPART, PHASE_EXPIRE = 0, 1, 2, 3, 4

WAITING, ONBOARD, ARRIVED = 'waiting', 'onboard', 'arrived'
SERVED, UNSERVED, CENSORED = 'served', 'unserved', 'censored'

ALIGHT_ORDER_NOTE = (
    '한 방문에서 여러 명이 내릴 때의 순서를 명세가 정하지 않았다. 여기서는 승차 순서로 '
    '내린다. 지표에는 영향이 없으나 로그 바이트 비교에는 영향이 있어 명세 보완이 필요하다.')


@dataclass
class PassengerState:
    """승객 1명의 물리 상태와 결과 상태. 둘을 따로 둔다(명세 V8).

    여정 기한이 지난 뒤 실제로 하차하면 physical은 arrived가 되고 arrival_s가 남지만
    outcome은 unserved로 이미 확정돼 있다.
    """
    passenger_id: str
    origin: str
    destination: str
    time_s: int
    outcome: str = None
    reason: str = None          # 결과를 확정한 기한 종류(wait·journey). M2 사유가 아니다.
    physical: str = WAITING
    boarding_s: int = None
    arrival_s: int = None
    vehicle_id: str = None
    trip_id: str = None
    refused: int = 0
    board_seq: int = None       # 승차 순서(같은 차량 안 하차 순서용)

    @property
    def resolved(self):
        return self.outcome is not None

    @property
    def wait_s(self):
        return None if self.boarding_s is None else self.boarding_s - self.time_s


@dataclass
class Oracle:
    """재계산 결과."""
    states: dict = field(default_factory=dict)
    boardings: dict = field(default_factory=dict)   # 방문 stable_id → [승차 승객 ID] (순서 유지)
    alightings: dict = field(default_factory=dict)  # 방문 stable_id → [하차 승객 ID]
    expiries: dict = field(default_factory=dict)    # 승객 ID → (time_s, kind, vehicle_id)
    departures: dict = field(default_factory=dict)  # 방문 stable_id → 출발 시점 탑승 인원(V14 공차)
    refusal_events: int = 0
    full_visits: list = field(default_factory=list)  # 만석으로 끝난 방문 stable_id
    note: str = ALIGHT_ORDER_NOTE

    def tally(self):
        counts = {SERVED: 0, UNSERVED: 0, CENSORED: 0}
        for state in self.states.values():
            counts[state.outcome] += 1
        return counts


def build_keys(scenario, passengers, max_wait_s, max_journey_s, drain_s):
    """명세 §4-1: 승객·방문 사건 키를 전부 만들어 drain 이하만 남기고 한 번 정렬한다."""
    keys = []
    for state in passengers:
        pid = state.passenger_id
        keys.append((state.time_s, PHASE_GENERATE, f'generate:{pid}', 'generate', pid))
        keys.append((state.time_s + max_wait_s, PHASE_EXPIRE, f'wait:{pid}', 'wait', pid))
        keys.append((state.time_s + max_journey_s, PHASE_EXPIRE, f'journey:{pid}', 'journey', pid))
    for visit in scenario.visits.values():
        keys.append((visit.arrival_s, PHASE_ALIGHT, visit.stable_id, 'alight', visit))
        keys.append((visit.arrival_s, PHASE_BOARD, visit.stable_id, 'board', visit))
        keys.append((visit.departure_s, PHASE_DEPART, visit.stable_id, 'depart', visit))
    keys = [k for k in keys if k[0] <= drain_s]
    keys.sort(key=lambda k: (k[0], k[1], k[2]))
    return keys


def _later_stops(scenario, trip_id, index):
    """운행편 trip_id의 index 이후 정류장 집합."""
    return {v.stop for v in scenario.visits_by_trip[trip_id] if v.index > index}


def recompute(scenario, passengers, max_wait_s, max_journey_s, drain_s, scan='full'):
    """사건을 순서대로 처리해 승객·차량 상태를 다시 만든다.

    passengers: [(passenger_id, origin, destination, time_s), ...] (발생 입력)
    """
    if scan not in ('index', 'full'):
        raise ValueError(f'scan은 index 또는 full: {scan!r}')
    states = {pid: PassengerState(pid, origin, destination, time_s)
              for pid, origin, destination, time_s in passengers}
    oracle = Oracle(states=states)
    order = list(states.values())           # 승객 배열. 대기열이 아니다.
    onboard_count = {}                      # 차량 배열: vehicle_id → 현재 인원
    onboard_of = {}                         # vehicle_id → [승객 ID] (승차 순서)
    appeared = set()                        # 발생한 승객
    board_seq = 0

    by_origin = None
    if scan == 'index':
        by_origin = {}
        for state in order:
            by_origin.setdefault(state.origin, []).append(state)

    def candidates(stop):
        return by_origin.get(stop, ()) if by_origin is not None else order

    for time_s, phase, stable_id, action, payload in build_keys(
            scenario, order, max_wait_s, max_journey_s, drain_s):

        if action == 'generate':
            appeared.add(payload)

        elif action == 'alight':
            visit = payload
            trip = scenario.trips[visit.trip_id]
            riders = onboard_of.get(trip.vehicle_id, [])
            leaving = [pid for pid in riders if states[pid].destination == visit.stop]
            for pid in leaving:                      # 승차 순서대로 내린다(명세 미정, note 참고)
                state = states[pid]
                state.physical = ARRIVED
                state.arrival_s = time_s
                state.vehicle_id = None
                if not state.resolved:               # 기한 안에 도착 → served
                    state.outcome = SERVED
                riders.remove(pid)
                onboard_count[trip.vehicle_id] = onboard_count.get(trip.vehicle_id, 0) - 1
            if leaving:
                oracle.alightings[visit.stable_id] = leaving

        elif action == 'board':
            visit = payload
            trip = scenario.trips[visit.trip_id]
            later = _later_stops(scenario, visit.trip_id, visit.index)
            # 자격 승객: 이 정류장에서 대기 중(발생 후·미승차·미확정)이고 목적지가 이후 정류장
            eligible = [s for s in candidates(visit.stop)
                        if s.origin == visit.stop and s.passenger_id in appeared
                        and s.physical == WAITING and not s.resolved
                        and s.destination in later]
            eligible.sort(key=lambda s: (s.time_s, s.passenger_id))
            free = trip.capacity - onboard_count.get(trip.vehicle_id, 0)
            taking = eligible[:max(free, 0)]
            for state in taking:
                state.physical = ONBOARD
                state.boarding_s = time_s
                state.vehicle_id = trip.vehicle_id
                state.trip_id = trip.trip_id
                state.board_seq = board_seq
                board_seq += 1
                onboard_of.setdefault(trip.vehicle_id, []).append(state.passenger_id)
                onboard_count[trip.vehicle_id] = onboard_count.get(trip.vehicle_id, 0) + 1
            if taking:
                oracle.boardings[visit.stable_id] = [s.passenger_id for s in taking]
            # 만차 거절(명세 V11): 승차 처리 후 빈 좌석 0이면 남은 자격 승객 1명당 1건
            if trip.capacity - onboard_count.get(trip.vehicle_id, 0) <= 0:
                remaining = eligible[len(taking):]
                if remaining:
                    oracle.refusal_events += len(remaining)
                    for state in remaining:
                        state.refused += 1
                oracle.full_visits.append(visit.stable_id)

        elif action == 'depart':
            # 출발은 승객 상태를 바꾸지 않는다. 출발 시점 탑승 인원만 적어 둔다(V14 공차시간).
            visit = payload
            trip = scenario.trips[visit.trip_id]
            oracle.departures[visit.stable_id] = onboard_count.get(trip.vehicle_id, 0)

        elif action in ('wait', 'journey'):
            state = states[payload]
            if state.resolved:
                continue
            if action == 'wait' and state.physical != WAITING:
                continue                              # 승차자에게는 wait 만료 없음
            state.outcome = UNSERVED
            state.reason = action
            oracle.expiries[payload] = (time_s, action,
                                        state.vehicle_id if state.physical == ONBOARD else None)

    for state in states.values():
        if not state.resolved:
            state.outcome = CENSORED                  # drain까지 미확정
    return oracle
