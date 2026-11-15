"""V9–V14 지표 재계산. 정답은 명세 §3이고 엔진 `sim/metrics.py`를 읽거나 쓰지 않는다.

입력은 재계산 oracle(`recompute`)의 승객·차량 상태뿐이다. metrics.json은 **비교 대상**이며
계산 근거가 아니다. metrics.json의 키 이름만 파일 형식으로 쓴다(명세가 스키마를 적지 않았다).
"""
from math import ceil

from .checks import CheckResult, NOT_VERIFIED
from .recompute import CENSORED, SERVED, UNSERVED

UNKNOWN_AREA = 'unknown'
REASONS = ('no_connection', 'service_end', 'journey', 'wait')


def _get(payload, path, default=None):
    """'M1.served'처럼 점으로 구분한 경로를 읽는다. 없으면 default."""
    node = payload
    for key in path.split('.'):
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def _compare(result, payload, path, mine, label=None):
    """metrics.json의 값과 재계산을 비교한다. 키가 없으면 그것도 위반이다."""
    sentinel = object()
    theirs = _get(payload, path, sentinel)
    if theirs is sentinel:
        result.add(f'metrics.json에 {path}가 없다')
        return
    if theirs != mine:
        result.add(f'{label or path}이 재계산과 다르다', path=path, recomputed=mine,
                   metrics=theirs)


# ---------- V9 M1 충족률 ----------

def fulfilment(oracle):
    """명세 V9 M1. censored>0이면 rate 대신 하한·상한만 의미가 있다."""
    tally = oracle.tally()
    total = sum(tally.values())
    served, censored = tally[SERVED], tally[CENSORED]
    if total == 0:
        rate = lower = upper = None
    elif censored == 0:
        rate = lower = upper = served / total
    else:
        rate = None
        lower, upper = served / total, (served + censored) / total
    return {'served': served, 'unserved': tally[UNSERVED], 'censored': censored,
            'total': total, 'rate': rate, 'lower': lower, 'upper': upper}


def check_fulfilment(scenario, run, oracle, summary=None):
    result = CheckResult('V9', 'M1 충족률', unit='승객')
    mine = (summary or summarize(scenario, run, oracle))['M1']
    result.examined = mine['total']
    m1 = run.metrics.get('M1')
    if not isinstance(m1, dict):
        result.add('metrics.json에 M1이 없다')
        return result.resolve()

    for name in ('served', 'unserved', 'censored', 'total', 'rate'):
        _compare(result, run.metrics, f'M1.{name}', mine[name])
    # 명세 V9는 censored>0인 경우만 lower·upper를 서술한다. 엔진은 censored=0일 때도
    # 내보내며 값은 rate와 같다. 있으면 재계산과 같아야 한다.
    for name in ('lower', 'upper'):
        if name in m1:
            _compare(result, run.metrics, f'M1.{name}', mine[name])
    if mine['censored'] == 0 and ('lower' in m1 or 'upper' in m1):
        result.note = ('명세 V9는 censored=0일 때 lower·upper를 정의하지 않는다. '
                       '엔진은 rate와 같은 값을 내보내며 그렇게 검사했다.')
    return result.resolve()


# ---------- V10 M2 미충족 사유 ----------

class Connectivity:
    """출발지→목적지 순방향 직행이 가능한 운행편과 그 출발지 도착 시각.

    엔진의 연결성 구조를 복사하지 않는다. 운행편을 매번 훑고 OD 단위로만 기억한다.
    """

    def __init__(self, scenario):
        self.scenario = scenario
        self._cache = {}
        self._stop_index = {
            trip_id: {}
            for trip_id in scenario.visits_by_trip}
        for trip_id, visits in scenario.visits_by_trip.items():
            for visit in visits:
                self._stop_index[trip_id].setdefault(visit.stop, []).append(visit.index)

    def origin_arrivals(self, origin, destination):
        """순방향 직행이 되는 운행편들의 출발지 도착 시각 목록. 비어 있으면 연결 없음."""
        key = (origin, destination)
        if key in self._cache:
            return self._cache[key]
        times = []
        for trip_id, visits in self.scenario.visits_by_trip.items():
            index = self._stop_index[trip_id]
            origins, destinations = index.get(origin), index.get(destination)
            if not origins or not destinations:
                continue
            last_destination = destinations[-1]
            times.extend(visits[i].arrival_s for i in origins if i < last_destination)
        times.sort()
        self._cache[key] = times
        return times


def reason_of(state, connectivity, drain_s):
    """명세 V10의 배타 우선순위로 미충족 사유를 정한다."""
    arrivals = connectivity.origin_arrivals(state.origin, state.destination)
    if not arrivals:
        return 'no_connection'
    if not any(state.time_s <= t <= drain_s for t in arrivals):
        return 'service_end'
    return state.reason  # 결과를 확정한 기한 종류(journey·wait)


def unserved_reasons(scenario, oracle, drain_s):
    """미충족 승객 ID → 사유."""
    connectivity = Connectivity(scenario)
    return {pid: reason_of(state, connectivity, drain_s)
            for pid, state in oracle.states.items() if state.outcome == UNSERVED}


def reason_counts(reasons):
    counts = {name: 0 for name in REASONS}
    for reason in reasons.values():
        if reason in counts:
            counts[reason] += 1
    return counts


def check_reasons(scenario, run, oracle, reasons=None):
    result = CheckResult('V10', 'M2 미충족 사유', unit='미충족 승객')
    reasons = unserved_reasons(scenario, oracle, run.drain_s) if reasons is None else reasons
    result.examined = len(reasons)

    declared = run.outcomes
    for pid, reason in sorted(reasons.items()):
        if reason not in REASONS:
            result.add(f'알 수 없는 사유: {reason!r}', passenger_id=pid)
            continue
        outcome = declared.get(pid)
        if outcome is not None and outcome.reason != reason:
            result.add(f'CSV 사유 {outcome.reason!r}가 재계산 {reason!r}와 다르다',
                       passenger_id=pid, origin=outcome.origin, destination=outcome.destination,
                       time_s=outcome.time_s)
    _compare(result, run.metrics, 'M2.unserved_by_reason', reason_counts(reasons),
             'M2.unserved_by_reason')
    return result.resolve()


# ---------- V11 M2 만차 거절 ----------

def refused_by_outcome(oracle):
    by_outcome = {SERVED: 0, UNSERVED: 0, CENSORED: 0}
    for state in oracle.states.values():
        if state.refused:
            by_outcome[state.outcome] += 1
    return by_outcome


def check_refusals(scenario, run, oracle):
    result = CheckResult('V11', 'M2 만차 거절', unit='승객')
    result.examined = len(oracle.states)
    _compare(result, run.metrics, 'M2.refusal_events', oracle.refusal_events)

    by_outcome = refused_by_outcome(oracle)
    declared = run.outcomes
    for pid, state in sorted(oracle.states.items()):
        outcome = declared.get(pid)
        if outcome is not None and outcome.refused != state.refused:
            result.add(f'CSV refused {outcome.refused}가 재계산 {state.refused}와 다르다',
                       passenger_id=pid)
    _compare(result, run.metrics, 'M2.passengers_refused_by_outcome', by_outcome,
             'M2.passengers_refused_by_outcome')
    return result.resolve()


# ---------- V12 M3 대기시간 ----------

def nearest_rank_p90(values):
    """명세 V12: 정렬 후 ⌈0.9n⌉번째(1부터). 0명이면 None."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(1, ceil(0.9 * len(ordered))) - 1]


def wait_summary(waits, threshold_s):
    over = [w for w in waits if w > threshold_s]
    return {'n': len(waits),
            'mean_s': (sum(waits) / len(waits)) if waits else None,
            'p90_s': nearest_rank_p90(waits),
            'over_threshold_n': len(over),
            'over_threshold_rate': (len(over) / len(waits)) if waits else None,
            'threshold_s': threshold_s}


def check_waits(scenario, run, oracle, reasons=None):
    result = CheckResult('V12', 'M3 대기시간', unit='탑승 승객')
    threshold = _get(run.measurement, 'policy.wait_threshold_s')
    if threshold is None:
        result.status = NOT_VERIFIED
        result.note = 'measurement.json policy에 wait_threshold_s가 없어 임계 초과를 재계산할 수 없다.'
        return result

    served_waits = [s.wait_s for s in oracle.states.values()
                    if s.outcome == SERVED and s.wait_s is not None]
    boarded_waits = [s.wait_s for s in oracle.states.values() if s.wait_s is not None]
    result.examined = len(boarded_waits)

    for population, waits in (('served', served_waits), ('boarded', boarded_waits)):
        mine = wait_summary(waits, threshold)
        for field, value in mine.items():
            _compare(result, run.metrics, f'M3.{population}.{field}', value)

    reasons = unserved_reasons(scenario, oracle, run.drain_s) if reasons is None else reasons
    not_boarded = {name: 0 for name in REASONS}
    for pid, reason in reasons.items():
        if oracle.states[pid].boarding_s is None and reason in not_boarded:
            not_boarded[reason] += 1
    _compare(result, run.metrics, 'M3.not_boarded_unserved_by_reason', not_boarded,
             'M3.not_boarded_unserved_by_reason')
    return result.resolve()


# ---------- V13 M5 지역별 ----------

def area_summary(scenario, oracle):
    """명세 V13 M5. 출발 정류장의 지역별 분모·충족률과 최저 지역."""
    areas = {}
    for state in oracle.states.values():
        area = scenario.stop_area.get(state.origin, UNKNOWN_AREA)
        bucket = areas.setdefault(area, {'total': 0, SERVED: 0, UNSERVED: 0, CENSORED: 0})
        bucket['total'] += 1
        bucket[state.outcome] += 1
    by_area = {area: {'total': b['total'], 'served': b[SERVED], 'unserved': b[UNSERVED],
                      'censored': b[CENSORED],
                      'served_rate': (b[SERVED] / b['total']) if b['total'] else None}
               for area, b in areas.items()}
    scored = [(values['served_rate'], area) for area, values in by_area.items()
              if values['total'] > 0]
    worst = min(scored) if scored else None
    return {'by_origin_area': by_area,
            'min_served_rate': None if worst is None else {'area': worst[1], 'rate': worst[0]}}


def check_areas(scenario, run, oracle, summary=None):
    result = CheckResult('V13', 'M5 지역별 충족률', unit='지역')
    mine = (summary or summarize(scenario, run, oracle))['M5']
    result.examined = len(mine['by_origin_area'])
    _compare(result, run.metrics, 'M5.by_origin_area', mine['by_origin_area'],
             'M5.by_origin_area')
    _compare(result, run.metrics, 'M5.min_served_rate', mine['min_served_rate'],
             'M5.min_served_rate')
    return result.resolve()


# ---------- V14 M6 차량별 ----------

def vehicle_summary(scenario, oracle, drain_s):
    """차량별 완료 운행 수·탑승·served 하차·운행시간·거리·공차시간."""
    rows = {}
    for trip in scenario.trips.values():
        rows.setdefault(trip.vehicle_id, {
            'vehicle_id': trip.vehicle_id, 'trips_completed': 0, 'boarded': 0,
            'served_alighted': 0, 'service_time_s': 0, 'distance_m': 0, 'empty_time_s': 0})

    for trip_id, visits in scenario.visits_by_trip.items():
        trip = scenario.trips[trip_id]
        row = rows[trip.vehicle_id]
        completed = trip.return_s <= drain_s
        if completed:
            row['trips_completed'] += 1
            row['service_time_s'] += trip.return_s - visits[0].arrival_s
            row['distance_m'] += trip.distance_m
            # 완료 운행의 회송: 마지막 출발부터 복귀까지
            row['empty_time_s'] += trip.return_s - visits[-1].departure_s
        # 탑승 0으로 출발한 구간 중 다음 도착이 drain 이내인 구간
        for i in range(len(visits) - 1):
            occupancy = oracle.departures.get(visits[i].stable_id)
            if occupancy == 0 and visits[i + 1].arrival_s <= drain_s:
                row['empty_time_s'] += visits[i + 1].arrival_s - visits[i].departure_s

    for state in oracle.states.values():
        if state.boarding_s is None:
            continue
        trip = scenario.trips.get(state.trip_id)
        if trip is None:
            continue
        rows[trip.vehicle_id]['boarded'] += 1
        if state.outcome == SERVED:
            rows[trip.vehicle_id]['served_alighted'] += 1
    return rows


def check_vehicles(scenario, run, oracle):
    result = CheckResult('V14', 'M6 차량별', unit='차량')
    mine = vehicle_summary(scenario, oracle, run.drain_s)
    result.examined = len(mine)

    recorded = _get(run.metrics, 'M6.vehicles')
    if not isinstance(recorded, list):
        result.add('metrics.json M6.vehicles가 목록이 아니다')
        return result.resolve()
    theirs = {}
    for row in recorded:
        vehicle_id = row.get('vehicle_id')
        if vehicle_id in theirs:
            result.add(f'M6.vehicles에 vehicle_id가 중복: {vehicle_id!r}')
        theirs[vehicle_id] = row

    for vehicle_id in sorted(set(mine) | set(theirs)):
        ours, recorded_row = mine.get(vehicle_id), theirs.get(vehicle_id)
        if ours is None:
            result.add('metrics.json에만 있는 차량', vehicle_id=vehicle_id)
            continue
        if recorded_row is None:
            result.add('일정에는 있으나 metrics.json M6에 없는 차량', vehicle_id=vehicle_id)
            continue
        differing = {field: {'recomputed': value, 'metrics': recorded_row.get(field)}
                     for field, value in ours.items()
                     if field != 'vehicle_id' and recorded_row.get(field) != value}
        if differing:
            result.add('차량별 집계가 재계산과 다르다', vehicle_id=vehicle_id, fields=differing)

    if 'empty_distance_m' in (_get(run.metrics, 'M6') or {}):
        if _get(run.metrics, 'M6.empty_distance_m') is not None:
            result.add('M6.empty_distance_m은 Trip에 구간 거리가 없어 NA여야 한다 (명세 §8 TODO)')
    return result.resolve()


# ---------- 재계산 지표 한곳에서 만들기 ----------

def summarize(scenario, run, oracle, reasons=None):
    """재계산한 M1·M2·M3·M5·M6·G를 metrics.json과 같은 모양으로 만든다.

    검사(V9–V14)와 손계산 대조 테스트가 **같은 이 함수**를 쓴다. 따로 계산하면 한쪽만
    맞는 상태를 못 잡는다. metrics.json은 비교 대상일 뿐 입력이 아니다.
    M6.vehicles는 metrics.json이 목록이지만 여기서는 vehicle_id → 집계 dict로 둔다.
    """
    reasons = unserved_reasons(scenario, oracle, run.drain_s) if reasons is None else reasons
    threshold = _get(run.measurement, 'policy.wait_threshold_s')
    served_waits = [s.wait_s for s in oracle.states.values()
                    if s.outcome == SERVED and s.wait_s is not None]
    boarded_waits = [s.wait_s for s in oracle.states.values() if s.wait_s is not None]
    not_boarded = {name: 0 for name in REASONS}
    for pid, reason in reasons.items():
        if oracle.states[pid].boarding_s is None and reason in not_boarded:
            not_boarded[reason] += 1

    summary = {
        'M1': fulfilment(oracle),
        'M2': {'unserved_by_reason': reason_counts(reasons),
               'refusal_events': oracle.refusal_events,
               'passengers_refused_by_outcome': refused_by_outcome(oracle)},
        'M3': {'not_boarded_unserved_by_reason': not_boarded},
        'M5': area_summary(scenario, oracle),
        'M6': {'vehicles': vehicle_summary(scenario, oracle, run.drain_s)},
    }
    if threshold is not None:
        summary['M3']['served'] = wait_summary(served_waits, threshold)
        summary['M3']['boarded'] = wait_summary(boarded_waits, threshold)
    return summary


def run_metrics(scenario, run, oracle):
    """V9–V14를 돌려 CheckResult 목록을 돌려준다."""
    reasons = unserved_reasons(scenario, oracle, run.drain_s)
    summary = summarize(scenario, run, oracle, reasons)
    return [check_fulfilment(scenario, run, oracle, summary),
            check_reasons(scenario, run, oracle, reasons),
            check_refusals(scenario, run, oracle),
            check_waits(scenario, run, oracle, reasons),
            check_areas(scenario, run, oracle, summary),
            check_vehicles(scenario, run, oracle)]
