"""V2–V5·V8 불변조건. 입력은 파일뿐이고 엔진을 호출하지 않는다(명세 §3).

사건 로그는 커질 수 있으므로 검사마다 파일을 한 번씩 스트리밍한다. 전체를 메모리에
올리지 않는다.

명세와 다르게 구현한 곳은 CheckResult.note에 적는다. 명세에 맞춰 엔진을 통과시키거나
엔진에 맞춰 명세를 고치지 않는다(둘 다 보고 대상이다).
"""
from dataclasses import dataclass, field

from .checks import CheckResult, FAIL, NOT_VERIFIED, PASS

# 사건 종류별 phase (명세 §2 사건 의미)
KIND_PHASE = {'alighted': 0, 'vehicle_arrived': 0, 'generated': 1, 'boarded': 2,
              'departed': 3, 'expired': 4}
VISIT_PHASES = (0, 2, 3)
# 같은 키에 사건이 여럿일 수 있는 phase. phase 0은 하차 여럿 + 도착 1회,
# phase 2는 승차 여럿(한 방문에서 여러 명이 탄다).
MULTI_KEYS = {0: ('alighted', 'vehicle_arrived'), 2: ('boarded',)}
EXPIRY_PREFIXES = ('wait:', 'journey:')

V2_NOTE = (
    '명세 V2는 키 (time_s, phase, stable_id)가 "엄격 증가"라고 적었으나 실제 엔진 출력은 '
    '같은 키에 사건이 여러 개 있다(한 방문의 하차 여러 건 + 도착 1건, 한 방문의 승차 여러 건). '
    '명세 §2 자신이 "같은 키에서 alighted가 먼저"라고 적어 엄격 증가와 모순한다. '
    '여기서는 약한 증가 + phase 0/2에만 중복 허용 + phase 0 내부 순서(alighted 먼저, '
    'vehicle_arrived 최대 1회)로 검사한다. phase 1·3·4의 키는 유일해야 한다.')


# ---------- stable_id 해석 ----------

def split_visit_id(stable_id):
    """`<trip_id>:<index 8자리>` → (trip_id, index). 형식이 아니면 None."""
    trip_id, _, tail = stable_id.rpartition(':')
    if not trip_id or len(tail) != 8 or not tail.isdigit():
        return None
    return trip_id, int(tail)


def expiry_kind(stable_id):
    """`wait:<pid>`·`journey:<pid>` → 'wait'·'journey'. 아니면 None."""
    for prefix in EXPIRY_PREFIXES:
        if stable_id.startswith(prefix):
            return prefix[:-1]
    return None


# ---------- V2 사건 순서 ----------

def check_event_order(scenario, run):
    result = CheckResult('V2', '사건 순서', unit='사건', note=V2_NOTE)
    previous_key = None
    group = []  # 같은 키의 사건 목록

    def close_group():
        if len(group) <= 1:
            return
        phase = group[0].phase
        allowed = MULTI_KEYS.get(phase)
        if allowed is None:
            result.add(f'phase {phase}에서 같은 키에 사건 {len(group)}건 (유일해야 한다)',
                       key=list(group[0].key), lines=[e.line_no for e in group],
                       kinds=[e.kind for e in group])
            return
        bad = [e.kind for e in group if e.kind not in allowed]
        if bad:
            result.add(f'phase {phase}의 같은 키에 허용되지 않는 종류 {sorted(set(bad))}',
                       key=list(group[0].key), lines=[e.line_no for e in group],
                       kinds=[e.kind for e in group])
        if phase == 0:
            kinds = [e.kind for e in group]
            if kinds.count('vehicle_arrived') > 1:
                result.add('같은 방문에 vehicle_arrived가 2회 이상',
                           key=list(group[0].key), lines=[e.line_no for e in group])
            first_arrived = next((i for i, k in enumerate(kinds) if k == 'vehicle_arrived'), None)
            if first_arrived is not None and 'alighted' in kinds[first_arrived:]:
                result.add('같은 키에서 alighted가 vehicle_arrived보다 뒤에 있다 (명세 §2)',
                           key=list(group[0].key), lines=[e.line_no for e in group], kinds=kinds)

    for event in run.iter_events():
        result.examined += 1
        if event.phase not in range(5):
            result.add(f'phase가 0..4 밖이다: {event.phase}',
                       line=event.line_no, stable_id=event.stable_id)
        if event.time_s > run.drain_s:
            result.add(f'time_s {event.time_s}가 drain_s {run.drain_s}를 넘는다',
                       line=event.line_no, stable_id=event.stable_id, kind=event.kind)
        expected_phase = KIND_PHASE.get(event.kind)
        if expected_phase is None:
            result.add(f'알 수 없는 사건 종류: {event.kind!r}', line=event.line_no)
        elif expected_phase != event.phase:
            result.add(f'{event.kind}의 phase는 {expected_phase}여야 한다 (실제 {event.phase})',
                       line=event.line_no, stable_id=event.stable_id)

        # stable_id 형식
        if event.phase in VISIT_PHASES:
            if split_visit_id(event.stable_id) is None:
                result.add('방문 사건의 stable_id가 `<trip_id>:<index 8자리>` 형식이 아니다',
                           line=event.line_no, stable_id=event.stable_id)
        elif event.phase == 1:
            if event.stable_id != f'generate:{event.passenger_id}':
                result.add(f'발생 사건의 stable_id는 generate:{event.passenger_id} 여야 한다',
                           line=event.line_no, stable_id=event.stable_id)
        elif event.phase == 4:
            kind = expiry_kind(event.stable_id)
            if kind is None or event.stable_id != f'{kind}:{event.passenger_id}':
                result.add('기한 사건의 stable_id는 wait:<pid> 또는 journey:<pid> 여야 한다',
                           line=event.line_no, stable_id=event.stable_id)

        if previous_key is not None and event.key < previous_key:
            result.add(f'키가 역행한다: {list(previous_key)} 뒤에 {list(event.key)}',
                       line=event.line_no, kind=event.kind)
        if previous_key is not None and event.key != previous_key:
            close_group()
            group.clear()
        group.append(event)
        previous_key = event.key
    close_group()
    return result.resolve()


# ---------- V3 일정 일치 ----------

def check_schedule_match(scenario, run):
    result = CheckResult('V3', '일정 일치', unit='정류장 방문')
    seen = {}  # (trip_id, index) → {'vehicle_arrived': [시각], 'departed': [시각]}
    for event in run.iter_events():
        if event.kind not in ('vehicle_arrived', 'departed'):
            continue
        parsed = split_visit_id(event.stable_id)
        if parsed is None:
            continue  # 형식 위반은 V2가 보고한다
        if parsed not in scenario.visits:
            result.add('일정에 없는 방문 사건',
                       line=event.line_no, stable_id=event.stable_id, kind=event.kind)
            continue
        seen.setdefault(parsed, {}).setdefault(event.kind, []).append(event.time_s)

    for key, visit in scenario.visits.items():
        result.examined += 1
        found = seen.get(key, {})
        for kind, scheduled in (('vehicle_arrived', visit.arrival_s),
                                ('departed', visit.departure_s)):
            times = found.get(kind, [])
            expected = 1 if scheduled <= run.drain_s else 0
            if len(times) != expected:
                result.add(f'{kind} 사건이 {len(times)}건 (일정상 {expected}건이어야 한다)',
                           stable_id=visit.stable_id, stop=visit.stop,
                           scheduled_s=scheduled, drain_s=run.drain_s, observed_s=times)
            elif times and times[0] != scheduled:
                result.add(f'{kind} 시각이 일정과 다르다: 일정 {scheduled}, 로그 {times[0]}',
                           stable_id=visit.stable_id, stop=visit.stop)
    return result.resolve()


# ---------- 승객별 사건 모으기 (V4·V8 공용) ----------

@dataclass
class PassengerEvents:
    generated: list = field(default_factory=list)
    boarded: list = field(default_factory=list)
    alighted: list = field(default_factory=list)
    expired: list = field(default_factory=list)
    order: list = field(default_factory=list)  # (key, kind) 발생 순서


BUCKET = {'generated': 'generated', 'boarded': 'boarded', 'alighted': 'alighted',
          'expired': 'expired'}


def collect_passenger_events(run):
    """passenger_id → PassengerEvents. 사건 로그 1회 통과."""
    records = {}
    for event in run.iter_events():
        bucket = BUCKET.get(event.kind)
        if bucket is None or event.passenger_id is None:
            continue
        record = records.setdefault(event.passenger_id, PassengerEvents())
        getattr(record, bucket).append(event)
        record.order.append((event.key, event.kind))
    return records


# ---------- V4 승객 생애 ----------

def check_passenger_lifecycle(scenario, run, records=None, realization=None):
    """generated 1회, boarded ≤ 1회·출발지·순방향 목적지, 하차 1회, 순서 위반 없음."""
    result = CheckResult('V4', '승객 생애', unit='승객')
    records = collect_passenger_events(run) if records is None else records
    declared = run.outcomes
    expected_times = dict(realization or {})

    for pid in sorted(set(records) | set(declared)):
        result.examined += 1
        record = records.get(pid, PassengerEvents())
        outcome = declared.get(pid)
        if outcome is None:
            result.add('사건 로그에는 있으나 passenger_outcomes.csv에 없다', passenger_id=pid)
            continue

        # 발생: 정확히 1회, 선언된 발생 시각과 같아야 한다
        if len(record.generated) != 1:
            result.add(f'generated 사건이 {len(record.generated)}건 (1건이어야 한다)',
                       passenger_id=pid,
                       lines=[e.line_no for e in record.generated][:5])
        else:
            generated_s = record.generated[0].time_s
            if generated_s != outcome.time_s:
                result.add(f'generated 시각 {generated_s}가 CSV time_s {outcome.time_s}와 다르다',
                           passenger_id=pid, line=record.generated[0].line_no)
            if pid in expected_times:
                origin, destination, time_s = expected_times[pid]
                if generated_s != time_s:
                    result.add(f'generated 시각 {generated_s}가 realization {time_s}와 다르다',
                               passenger_id=pid, line=record.generated[0].line_no)
                if (outcome.origin, outcome.destination) != (origin, destination):
                    result.add('CSV의 OD가 realization과 다르다', passenger_id=pid,
                               csv=[outcome.origin, outcome.destination],
                               realization=[origin, destination])

        # 승차: 최대 1회
        if len(record.boarded) > 1:
            result.add(f'boarded 사건이 {len(record.boarded)}건 (최대 1건)', passenger_id=pid,
                       lines=[e.line_no for e in record.boarded][:5],
                       vehicles=sorted({e.vehicle_id for e in record.boarded}))
        board_visit = None
        if record.boarded:
            board = record.boarded[0]
            parsed = split_visit_id(board.stable_id)
            board_visit = scenario.visits.get(parsed) if parsed else None
            if board_visit is None:
                result.add('승차 사건의 방문이 일정에 없다', passenger_id=pid,
                           line=board.line_no, stable_id=board.stable_id)
            else:
                if board_visit.stop != outcome.origin:
                    result.add(f'출발지가 아닌 정류장 {board_visit.stop}에서 승차 '
                               f'(출발지 {outcome.origin})', passenger_id=pid, line=board.line_no)
                later = [v for v in scenario.visits_by_trip[board_visit.trip_id]
                         if v.index > board_visit.index and v.stop == outcome.destination]
                if not later:
                    result.add(f'목적지 {outcome.destination}가 이 운행편의 이후 정류장이 아니다',
                               passenger_id=pid, line=board.line_no,
                               trip_id=board_visit.trip_id, board_index=board_visit.index)

        # 하차: 승차 없이 하차 금지, 승차 차량이 목적지를 방문하면 정확히 1회
        if record.alighted and not record.boarded:
            result.add('승차 없이 alighted 사건이 있다', passenger_id=pid,
                       lines=[e.line_no for e in record.alighted][:5])
        if len(record.alighted) > 1:
            result.add(f'alighted 사건이 {len(record.alighted)}건 (최대 1건)', passenger_id=pid,
                       lines=[e.line_no for e in record.alighted][:5])
        if record.boarded and record.alighted:
            board, alight = record.boarded[0], record.alighted[0]
            if alight.key < board.key:
                result.add('승차보다 하차가 먼저 일어났다', passenger_id=pid,
                           board_line=board.line_no, alight_line=alight.line_no)
            if board.vehicle_id != alight.vehicle_id:
                result.add(f'승차 차량 {board.vehicle_id}와 하차 차량 {alight.vehicle_id}가 다르다',
                           passenger_id=pid, board_line=board.line_no, alight_line=alight.line_no)
            parsed = split_visit_id(alight.stable_id)
            alight_visit = scenario.visits.get(parsed) if parsed else None
            if alight_visit is None:
                result.add('하차 사건의 방문이 일정에 없다', passenger_id=pid, line=alight.line_no)
            elif alight_visit.stop != outcome.destination:
                result.add(f'목적지가 아닌 정류장 {alight_visit.stop}에서 하차 '
                           f'(목적지 {outcome.destination})', passenger_id=pid, line=alight.line_no)
        elif board_visit is not None and not record.alighted:
            # 승차 차량이 drain 안에 목적지를 방문했는데 하차가 없으면 위반
            arrivals = [v for v in scenario.visits_by_trip[board_visit.trip_id]
                        if v.index > board_visit.index and v.stop == outcome.destination
                        and v.arrival_s <= run.drain_s]
            if arrivals:
                result.add(f'목적지 방문(시각 {arrivals[0].arrival_s} ≤ drain)이 있는데 '
                           'alighted 사건이 없다', passenger_id=pid,
                           trip_id=board_visit.trip_id, stable_id=arrivals[0].stable_id)
    return result.resolve()


# ---------- V5 정원 ----------

def check_capacity(scenario, run):
    """사건으로 재구성한 차량별 탑승 인원 = 각 사건의 occupancy, 0 ≤ occupancy ≤ capacity."""
    result = CheckResult('V5', '정원', unit='점유 기록이 있는 사건')
    onboard = {}  # vehicle_id → 현재 인원
    for event in run.iter_events():
        if event.occupancy is None:
            if event.kind in ('boarded', 'alighted'):
                result.add(f'{event.kind} 사건에 occupancy가 없다', line=event.line_no,
                           passenger_id=event.passenger_id)
            continue
        result.examined += 1
        vehicle_id, capacity = event.vehicle_id, None
        parsed = split_visit_id(event.stable_id)
        trip = scenario.trips.get(parsed[0]) if parsed else None
        if trip is not None:
            capacity = trip.capacity
            if vehicle_id is None:
                vehicle_id = trip.vehicle_id
            elif vehicle_id != trip.vehicle_id:
                result.add(f'사건의 vehicle_id {vehicle_id}가 일정의 {trip.vehicle_id}와 다르다',
                           line=event.line_no, stable_id=event.stable_id)
        if vehicle_id is None:
            result.add('occupancy가 있으나 차량을 특정할 수 없다',
                       line=event.line_no, stable_id=event.stable_id, kind=event.kind)
            continue

        if event.kind == 'boarded':
            onboard[vehicle_id] = onboard.get(vehicle_id, 0) + 1
        elif event.kind == 'alighted':
            onboard[vehicle_id] = onboard.get(vehicle_id, 0) - 1
        reconstructed = onboard.get(vehicle_id, 0)

        if event.occupancy != reconstructed:
            result.add(f'occupancy {event.occupancy}가 사건으로 재구성한 인원 '
                       f'{reconstructed}와 다르다', line=event.line_no, kind=event.kind,
                       vehicle_id=vehicle_id, stable_id=event.stable_id)
        if event.occupancy < 0:
            result.add(f'occupancy가 음수다: {event.occupancy}',
                       line=event.line_no, vehicle_id=vehicle_id)
        if capacity is not None and event.occupancy > capacity:
            result.add(f'occupancy {event.occupancy}가 정원 {capacity}를 넘는다',
                       line=event.line_no, vehicle_id=vehicle_id, stable_id=event.stable_id)

    leftover = {v: n for v, n in onboard.items() if n != 0}
    if leftover:
        result.note = ('drain 시점에 차량에 남아 있는 인원(censored onboard)은 정상이다. '
                       f'남은 차량 {len(leftover)}대.')
    return result.resolve()


# ---------- V8 결과 ----------

def classify(record, drain_s):
    """사건만으로 결과를 정한다. (outcome, physical, arrival_s, 확정 사유) 반환.

    served ⇔ 만료 전 목적지 하차. unserved ⇔ drain 이내 만료 사건. 둘 다 아니면 censored.
    같은 time_s에서는 하차(phase 0)가 만료(phase 4)보다 먼저이므로 기한 당일 하차는 served다.
    """
    alight = record.alighted[0] if record.alighted else None
    expiry = min(record.expired, key=lambda e: e.key) if record.expired else None
    arrival_s = alight.time_s if alight else None

    if alight is not None and (expiry is None or alight.key < expiry.key):
        return 'served', 'arrived', arrival_s, None
    reason = expiry_kind(expiry.stable_id) if expiry is not None else None
    if expiry is not None:
        physical = 'arrived' if alight is not None else ('onboard' if record.boarded else 'waiting')
        return 'unserved', physical, arrival_s, reason
    physical = 'arrived' if alight is not None else ('onboard' if record.boarded else 'waiting')
    return 'censored', physical, arrival_s, None


def check_outcomes(scenario, run, records=None, realization=None):
    """승객별 결과를 사건에서 다시 정하고 CSV·합계와 비교한다."""
    result = CheckResult('V8', '결과와 합계', unit='승객')
    records = collect_passenger_events(run) if records is None else records
    declared = run.outcomes
    tally = {'served': 0, 'unserved': 0, 'censored': 0}

    for pid in sorted(set(records) | set(declared)):
        result.examined += 1
        outcome = declared.get(pid)
        record = records.get(pid, PassengerEvents())
        if outcome is None:
            result.add('사건 로그에는 있으나 CSV에 없다', passenger_id=pid)
            continue
        if not record.order:
            result.add('CSV에는 있으나 사건 로그에 사건이 없다', passenger_id=pid)
            continue

        expected, physical, arrival_s, reason = classify(record, run.drain_s)
        if expected not in tally:
            result.add(f'알 수 없는 결과 분류: {expected}', passenger_id=pid)
            continue
        tally[expected] += 1

        if outcome.outcome != expected:
            result.add(f'CSV outcome {outcome.outcome!r}이 사건으로 정한 {expected!r}와 다르다',
                       passenger_id=pid, reason=reason)
        if outcome.physical != physical:
            result.add(f'CSV physical {outcome.physical!r}이 사건으로 정한 {physical!r}와 다르다',
                       passenger_id=pid)
        if outcome.arrival_s != arrival_s:
            result.add(f'CSV arrival_s {outcome.arrival_s}가 하차 사건 시각 {arrival_s}와 다르다',
                       passenger_id=pid)
        boarding_s = record.boarded[0].time_s if record.boarded else None
        if outcome.boarding_s != boarding_s:
            result.add(f'CSV boarding_s {outcome.boarding_s}가 승차 사건 시각 {boarding_s}와 다르다',
                       passenger_id=pid)
        if expected == 'served' and outcome.reason:
            result.add(f'served인데 사유가 있다: {outcome.reason!r}', passenger_id=pid)
        if expected == 'censored' and outcome.reason:
            result.add(f'censored인데 사유가 있다: {outcome.reason!r}', passenger_id=pid)

    total = sum(tally.values())
    if total != len(declared):
        result.add(f'결과 분류 합 {total}이 CSV 승객 수 {len(declared)}와 다르다',
                   tally=dict(tally))
    if realization is not None and len(declared) != len(realization):
        result.add(f'CSV 승객 수 {len(declared)}가 realization 승객 수 {len(realization)}와 다르다')

    m1 = run.metrics.get('M1') or {}
    for name in ('served', 'unserved', 'censored', 'total'):
        if name not in m1:
            result.add(f'metrics.json M1에 {name}이 없다')
            continue
        mine = total if name == 'total' else tally[name]
        if m1[name] != mine:
            result.add(f'metrics.json M1.{name} {m1[name]}이 재계산값 {mine}와 다르다')
    return result.resolve()


# ---------- 묶음 실행 ----------

def run_invariants(scenario, run, realization=None):
    """V2·V3·V4·V5·V8을 돌려 CheckResult 목록을 돌려준다."""
    records = collect_passenger_events(run)
    realization_map = None
    if realization is not None:
        realization_map = {pid: (origin, destination, time_s)
                           for pid, origin, destination, time_s in realization}
    return [check_event_order(scenario, run),
            check_schedule_match(scenario, run),
            check_passenger_lifecycle(scenario, run, records, realization_map),
            check_capacity(scenario, run),
            check_outcomes(scenario, run, records,
                           realization if realization is not None else None)]
