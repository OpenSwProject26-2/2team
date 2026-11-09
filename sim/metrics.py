"""P4 지표(M1–M6)와 R12 최대 배차간격 검사. 결과를 인용할 때 이 정의를 함께 밝힌다.

M1  served / total. total = served + unserved + censored. total=0이면 NA(None).
    censored가 있으면 rate 대신 하한 served/total, 상한 (served+censored)/total을 낸다.

M2  unserved 사유는 하나만, 아래 순서로 먼저 맞는 것(REASON_PRIORITY):
    1. no_connection: 일정 어디에도 출발지를 거쳐 목적지로 가는 운행편이 없음
    2. service_end:   연결은 있지만 [발생 시각, drain_s]에 출발지에 오는 그런 운행편이 없음
    3. journey / wait: 결과를 확정한 기한. 같은 시각이면 journey가 먼저 처리된다.
    만차 거절은 별도 집계: 만석으로 끝난 방문마다 탈 수 있었던 남은 승객 1명당 1건(중복 허용).
    거절을 겪은 승객 수도 결과별로 세지만, 그것을 실패 원인으로 보지는 않는다.

M3  대기 = boarding_s − time_s(초). 모집단을 나눠 낸다: served(포기자 제외라 치우침),
    boarded(탄 사람 전부, 이후 여정 초과·censored 포함). 못 탄 unserved는 사유별 인원만.
    평균, nearest-rank P90, 임계 초과(>) 비율. max_wait_s ≤ 임계면 served 초과율은 당연히 0.

M5  출발 정류장의 지역별(호출자가 stop→지역 지정, 없으면 'unknown') 인원과 충족률, 최저 지역.

M6  차량별 집계(sim.reporting.VehicleStats). 빈 차 거리는 구간 거리가 없어 NA(TODO).

R12 같은 노선의 연속 출발 간격(정류장마다) ≤ max_headway_s. 운영 시작·종료와 첫·마지막
    출발 사이 간격은 검사하지 않는다(TODO 정책). 노선 구분은 호출자가 준다.
"""
from collections import Counter
from dataclasses import asdict
from math import ceil
from .models import SimulationError, integer

PROVISIONAL = '독립 검증 전 잠정 (provisional, not independently verified)'
REASON_PRIORITY = ('no_connection', 'service_end', 'journey', 'wait')


def _rate(n, d): return None if d == 0 else n / d


class WaitStats:
    """대기시간 통계. 승객별 목록 대신 초 단위 히스토그램으로 정확한 값을 낸다."""
    def __init__(self, threshold_s):
        self.threshold_s, self.hist, self.n, self.sum = threshold_s, Counter(), 0, 0

    def add(self, wait_s):
        if wait_s < 0: raise SimulationError('negative wait')
        self.hist[wait_s] += 1; self.n += 1; self.sum += wait_s

    def p90(self):
        if not self.n: return None
        rank, seen = ceil(self.n * .9), 0
        for value in sorted(self.hist):
            seen += self.hist[value]
            if seen >= rank: return value

    def summary(self, population):
        over = sum(c for v, c in self.hist.items() if v > self.threshold_s)
        return {'population': population, 'n': self.n,
                'mean_s': None if not self.n else self.sum / self.n, 'p90_s': self.p90(),
                'threshold_s': self.threshold_s, 'over_threshold_n': over,
                'over_threshold_rate': _rate(over, self.n)}


class MetricsAccumulator:
    """승객의 확정 상태를 한 명씩 한 번만 받아 지표를 누적한다(배치·스트리밍 공용)."""
    def __init__(self, *, wait_threshold_s, stop_area=None):
        integer(wait_threshold_s)
        self.stop_area = stop_area
        self.outcomes, self.reasons, self.censored_physical = Counter(), Counter(), Counter()
        self.refused_by_outcome, self.not_boarded_unserved = Counter(), Counter()
        self.served_wait, self.boarded_wait = WaitStats(wait_threshold_s), WaitStats(wait_threshold_s)
        self.areas = {}

    def add(self, s):
        outcome = s.outcome
        if outcome not in ('served', 'unserved', 'censored'): raise SimulationError('unfinished passenger')
        if (outcome == 'unserved') != (s.reason is not None): raise SimulationError('reason/outcome mismatch')
        self.outcomes[outcome] += 1
        if outcome == 'unserved': self.reasons[s.reason] += 1
        if outcome == 'censored': self.censored_physical[s.physical] += 1
        if s.refused: self.refused_by_outcome[outcome] += 1
        if s.boarding_s is not None:
            wait = s.boarding_s - s.passenger.time_s
            self.boarded_wait.add(wait)
            if outcome == 'served': self.served_wait.add(wait)
        elif outcome == 'unserved':
            self.not_boarded_unserved[s.reason] += 1
        if self.stop_area is not None:
            area = self.stop_area.get(s.passenger.origin, 'unknown')
            self.areas.setdefault(area, Counter())[outcome] += 1


def check_max_headway(trips, route_of, max_headway_s):
    """R12 검사. route_of는 trip_id → 노선. 위반 목록과 노선별 최대 간격을 돌려준다."""
    integer(max_headway_s, 1)
    departures = {}
    for t in trips:
        if t.trip_id not in route_of: raise SimulationError('trip without route identity')
        for stop, dep in zip(t.stops, t.departures):
            departures.setdefault((route_of[t.trip_id], stop), []).append(dep)
    violations, worst = [], {}
    for (route, stop), deps in sorted(departures.items()):
        deps.sort()
        for a, b in zip(deps, deps[1:]):
            gap = b - a
            worst[route] = max(worst.get(route, 0), gap)
            if gap > max_headway_s:
                violations.append({'route': route, 'stop': stop, 'from_s': a, 'to_s': b, 'gap_s': gap})
    return {'rule': 'R12', 'max_headway_s': max_headway_s, 'pass': not violations,
            'violation_count': len(violations), 'violations': violations[:100],
            'max_gap_by_route_s': dict(sorted(worst.items())),
            'not_checked': 'window-edge gaps (service start/end to first/last departure); TODO(policy)'}


def summarize(result, *, wait_threshold_s, stop_area=None, accumulator=None, headway=None):
    """M1–M6 보고서(dict). 결과를 스트리밍했다면 그때 쓴 accumulator를 넘긴다."""
    if accumulator is None:
        accumulator = MetricsAccumulator(wait_threshold_s=wait_threshold_s, stop_area=stop_area)
        for s in result.passengers: accumulator.add(s)
    a = accumulator
    served, unserved, censored = (a.outcomes[k] for k in ('served', 'unserved', 'censored'))
    total = served + unserved + censored
    if (served, unserved, censored, total) != (result.served, result.unserved, result.censored, result.total):
        raise SimulationError('metrics do not match result counts')
    if served and a.served_wait.p90() != result.p90_wait_s:
        raise SimulationError('P90 mismatch with result')
    areas = None
    if a.stop_area is not None:
        areas = {k: {'total': sum(c.values()), 'served': c['served'], 'unserved': c['unserved'],
                     'censored': c['censored'], 'served_rate': _rate(c['served'], sum(c.values()))}
                 for k, c in sorted(a.areas.items())}
        rated = [(v['served_rate'], k) for k, v in areas.items() if v['total']]
        low = min(rated) if rated else None
    return {
        'label': PROVISIONAL,
        'M1': {'served': served, 'unserved': unserved, 'censored': censored, 'total': total,
               'rate': _rate(served, total) if not censored else None,
               'lower': _rate(served, total), 'upper': _rate(served + censored, total),
               'note': 'rate is NA when total=0; with censored>0 report lower/upper only'},
        'M2': {'unserved_by_reason': {k: a.reasons[k] for k in REASON_PRIORITY},
               'reason_priority': list(REASON_PRIORITY),
               'refusal_events': result.refusal_count,
               'passengers_refused_by_outcome': {k: a.refused_by_outcome[k] for k in ('served', 'unserved', 'censored')},
               'censored_by_physical': dict(sorted(a.censored_physical.items()))},
        'M3': {'served': a.served_wait.summary('served (boarded and arrived by deadline)'),
               'boarded': a.boarded_wait.summary('boarded (includes later journey-unserved and censored onboard)'),
               'not_boarded_unserved_by_reason': {k: a.not_boarded_unserved[k] for k in REASON_PRIORITY}},
        'M5': None if areas is None else {'by_origin_area': areas,
               'min_served_rate': None if low is None else {'area': low[1], 'rate': low[0]}},
        'M6': {'vehicles': [asdict(v) for v in result.vehicle_stats],
               'empty_distance_m': None, 'empty_distance_note': 'NA: Trip has no per-segment distance (TODO)'},
        'G': {'R12': headway, 'engine_invariants': 'capacity/conservation/time order enforced by engine '
              '(fail-fast); independent check pending M07'},
    }
