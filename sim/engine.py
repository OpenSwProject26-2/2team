"""M05 시뮬레이터 본체: 고정 입력과 운행 일정으로 승객 한 명 한 명의 결과를 계산한다."""
from collections import deque
from dataclasses import asdict
from functools import partial
import heapq
from .dispatcher import Dispatcher
from .events import Event, NullSink
from .models import SimulationError, integer
from .passengers import PassengerState
from .vehicles import VehicleState
from .schedule import validate_schedule
from .reporting import Result, VehicleStats
from .metrics import WaitStats
from .run_inputs import digest


class _Connectivity:
    """unserved 사유 판정용 OD 연결 정보. OD별로 처음 필요할 때 계산해 둔다."""
    def __init__(self, trips, drain_s):
        self.visits, self.drain_s, self.cache = {}, drain_s, {}
        for t in trips:
            position = {stop: i for i, stop in enumerate(t.stops)}
            for i, stop in enumerate(t.stops):
                self.visits.setdefault(stop, []).append((t.arrivals[i], i, position))

    def reason(self, passenger, trigger):
        """우선순위: 연결 없음 → 운행 종료 → 결과를 확정한 기한(journey/wait)."""
        od = (passenger.origin, passenger.destination)
        if od not in self.cache:
            connected, last = False, None
            for arrival, i, position in self.visits.get(od[0], ()):
                if position.get(od[1], -1) > i:
                    connected = True
                    if arrival <= self.drain_s and (last is None or arrival > last): last = arrival
            self.cache[od] = (connected, last)
        connected, last = self.cache[od]
        if not connected: return 'no_connection'
        if last is None or passenger.time_s > last: return 'service_end'
        return trigger


def simulate(plan_id, revision, inputs, trips, config, sink=None, probe=None, outcome_sink=None):
    """운행안 1개를 고정 입력 1개로 시뮬레이션하고 Result를 돌려준다.

    sink: 사건 로그 출력처(기본 NullSink). probe(stage): 측정용 훅, 상태를 바꾸지 않는다.
    outcome_sink(state): 결과 스트리밍. 승객 결과가 더는 바뀌지 않는 순간 한 번씩 넘기고
    메모리에서 지운다. 이때 Result.passengers는 ()이다.
    입력·일정 오류는 SimulationError, 정원 초과·인원 불일치는 RuntimeError로 멈춘다.
    """
    integer(revision, 1)
    if not plan_id: raise SimulationError('missing plan ID')
    validate_schedule(trips)
    if any(p.time_s > config.drain_s for p in inputs.passengers):
        raise SimulationError('drain precedes passenger generation')
    sink = sink if sink is not None else NullSink()
    streaming = outcome_sink is not None
    # 승객을 (발생 시각, ID) 순으로 정렬해 두면 발생·대기 만료·여정 만료 사건도 같은 순서가
    # 된다. 그래서 승객 사건을 미리 다 넣지 않고 정렬된 스트림 3개로 꺼내 쓴다.
    order = sorted(inputs.passengers, key=lambda p: (p.time_s, p.passenger_id))
    states = {}
    vehicles = {t.vehicle_id: VehicleState(t.capacity) for t in trips}
    vstats = {vid: {'boarded': 0, 'served_alighted': 0, 'empty_time_s': 0} for vid in vehicles}
    connectivity = _Connectivity(trips, config.drain_s)
    dispatcher, count, refusals = Dispatcher(), [0], [0]
    tally = {'served': 0, 'unserved': 0, 'censored': 0}
    served_waits = WaitStats(0)
    # 대기열 waiting[출발 정류장][목적지]: 발생 순서((시각, ID) 순)로 쌓이는 선착순 줄.
    # 이미 탔거나 결과가 정해진 승객은 줄에서 바로 빼지 않고, 줄 맨 앞에 왔을 때 버린다.
    waiting = {}
    # 탈 자격: 정류장에서 기다리는 중이고 결과가 아직 미확정.
    def eligible(s): return s.physical == 'waiting' and s.outcome is None
    def head(q):
        while q and not eligible(q[0]): q.popleft()
        return q[0] if q else None
    def emit(key, kind, pid=None, vid=None):
        sink.emit(Event(*key, kind, pid, vid, len(vehicles[vid].onboard) if vid else None))
        count[0] += 1
    # 승객 결과 확정: 인원 집계, served 대기 기록, 스트리밍이면 내보내고 메모리에서 삭제.
    def final(s):
        tally[s.outcome] += 1
        if s.outcome == 'served': served_waits.add(s.boarding_s - s.passenger.time_s)
        if streaming:
            del states[s.passenger.passenger_id]
            outcome_sink(s)
    # phase 1 발생: 승객 상태를 만들고 출발 정류장 대기열에 넣는다.
    def generate(*key, p):
        s = states[p.passenger_id] = PassengerState(p)
        s.physical = 'waiting'
        waiting.setdefault(p.origin, {}).setdefault(p.destination, deque()).append(s)
        emit(key, 'generated', p.passenger_id)
    # phase 4 기한 만료: 대기 기한은 아직 대기 중일 때만, 여정 기한은 도착 전이면 적용한다.
    # 탑승 중 여정 기한을 넘기면 결과만 unserved가 되고 차에서는 내리지 않는다.
    def expire(*key, pid):
        s = states.get(pid)
        if s is not None and s.outcome is None and s.physical != 'arrived':
            trigger = 'wait' if key[2].startswith('wait:') else 'journey'
            if trigger == 'wait' and s.physical != 'waiting': return
            s.outcome = 'unserved'
            s.reason = connectivity.reason(s.passenger, trigger)
            emit(key, 'expired', s.passenger.passenger_id, s.vehicle_id)
            if s.physical == 'waiting': final(s)
    dispatcher.add_stream(((p.time_s, 1, 'generate:'+p.passenger_id), partial(generate, p=p)) for p in order)
    dispatcher.add_stream(((p.time_s+config.max_wait_s, 4, 'wait:'+p.passenger_id),
                           partial(expire, pid=p.passenger_id)) for p in order)
    dispatcher.add_stream(((p.time_s+config.max_journey_s, 4, 'journey:'+p.passenger_id),
                           partial(expire, pid=p.passenger_id)) for p in order)
    # phase 0 도착: 이 정류장이 목적지인 승객을 내린다(아직 미확정이면 served).
    def arrive(*key, t, i):
        stop, v = t.stops[i], vehicles[t.vehicle_id]
        for pid in sorted(tuple(v.onboard)):
            s = v.onboard[pid]
            if s.passenger.destination == stop:
                del v.onboard[pid]
                s.physical, s.arrival_s, s.vehicle_id = 'arrived', key[0], None
                if s.outcome is None:
                    s.outcome = 'served'
                    vstats[t.vehicle_id]['served_alighted'] += 1
                emit(key, 'alighted', pid, t.vehicle_id)
                final(s)
        v.check()
        emit(key, 'vehicle_arrived', vid=t.vehicle_id)
    # phase 2 승차: 이 운행편의 남은 정류장이 목적지인 대기 승객을 선착순으로 빈 좌석만큼 태운다.
    def board(*key, t, i):
        v = vehicles[t.vehicle_id]
        free, queues = v.capacity-len(v.onboard), waiting.get(t.stops[i])
        if not queues: return
        # 목적지별 줄의 맨 앞끼리 비교해 가장 먼저 온 승객부터 태운다(전체 선착순과 같은 결과).
        heads = []
        if free > 0:
            for dest in t.stops[i+1:]:
                q = queues.get(dest)
                s = head(q) if q else None
                if s is not None: heads.append(((s.passenger.time_s, s.passenger.passenger_id), dest))
            heapq.heapify(heads)
        while heads and free > 0:
            _, dest = heapq.heappop(heads)
            q = queues[dest]
            s = q.popleft()
            nxt = head(q)
            if nxt is not None:
                heapq.heappush(heads, ((nxt.passenger.time_s, nxt.passenger.passenger_id), dest))
            free -= 1
            if s.vehicle_id is not None: raise RuntimeError('multiple vehicles')
            s.physical, s.vehicle_id, s.boarding_s = 'onboard', t.vehicle_id, key[0]
            v.onboard[s.passenger.passenger_id] = s
            v.check()
            vstats[t.vehicle_id]['boarded'] += 1
            emit(key, 'boarded', s.passenger.passenger_id, t.vehicle_id)
        if free == 0:
            # 만석으로 끝난 방문: 이 차를 탈 수 있었는데 남은 승객마다 거절 1건(방문마다 중복 집계).
            for dest in t.stops[i+1:]:
                for s in queues.get(dest, ()):
                    if eligible(s):
                        s.refused += 1
                        refusals[0] += 1
    # phase 3 출발: 빈 차로 떠나는 구간(마지막 정류장이면 복귀)을 공차 시간으로 더한다.
    def depart(*key, t, i):
        if not vehicles[t.vehicle_id].onboard:
            if i < len(t.stops)-1:
                if t.arrivals[i+1] <= config.drain_s:
                    vstats[t.vehicle_id]['empty_time_s'] += t.arrivals[i+1]-t.departures[i]
            elif t.return_s <= config.drain_s:
                vstats[t.vehicle_id]['empty_time_s'] += t.return_s-t.departures[i]
        emit(key, 'departed', vid=t.vehicle_id)
    def trip_events(t):
        # 한 운행편의 사건은 이미 시간순이다(도착 ≤ 출발 < 다음 도착, 같은 시각이면 0<2<3).
        for i in range(len(t.stops)):
            sid = t.trip_id+':'+str(i).zfill(8)
            yield (t.arrivals[i], 0, sid), partial(arrive, t=t, i=i)
            yield (t.arrivals[i], 2, sid), partial(board, t=t, i=i)
            yield (t.departures[i], 3, sid), partial(depart, t=t, i=i)
    for trip in trips: dispatcher.add_stream(trip_events(trip))
    if probe: probe('engine_init')
    dispatcher.run(config.drain_s)
    if probe: probe('engine_run')
    # drain 시점에 남은 승객 확정: 결과 미정이면 censored, 여정 초과로 아직 탑승 중이면 unserved.
    for pid in sorted(states):
        s = states[pid]
        if s.outcome is None:
            s.outcome = 'censored'
            final(s)
        elif s.physical == 'onboard':
            final(s)
    served, unserved, censored = tally['served'], tally['unserved'], tally['censored']
    ordered = () if streaming else tuple(states[k] for k in sorted(states))
    if served+unserved+censored != len(inputs.passengers): raise RuntimeError('conservation')
    for v in vehicles.values(): v.check()
    return Result(plan_id, revision, inputs.input_bundle_id, inputs.source_checksum, inputs.checksum,
        digest(asdict(config)), ordered, served, unserved, censored, served_waits.p90(),
        sum(t.distance_m for t in trips if t.return_s <= config.drain_s), count[0], refusals[0],
        vehicle_stats(trips, vstats, config.drain_s), len(inputs.passengers) if streaming else None)


def vehicle_stats(trips, counters, drain_s):
    """차량별 집계(M6): 엔진 카운터 + drain_s 안에 복귀까지 끝난 운행편의 시간·거리."""
    completed = {vid: [0, 0, 0] for vid in counters}
    for t in trips:
        if t.return_s <= drain_s:
            c = completed[t.vehicle_id]
            c[0] += 1; c[1] += t.return_s-t.arrivals[0]; c[2] += t.distance_m
    return tuple(VehicleStats(vid, completed[vid][0], counters[vid]['boarded'], counters[vid]['served_alighted'],
                              completed[vid][1], completed[vid][2], counters[vid]['empty_time_s'])
                 for vid in sorted(counters))
