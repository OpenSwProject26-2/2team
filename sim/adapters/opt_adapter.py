"""C(M04) 연결부. M05에서 C 코드를 import하는 유일한 파일이다(PYTHONPATH=C.Part 필요)."""
from dataclasses import asdict
import json
from opt.candidates import validate_allocation
from opt.integration import Assessment
from opt.models import Demand, InputError
from sim.models import Trip, SimulationError
from sim.run_inputs import RunInputs, expand
from sim.arrivals import validate_realization
from sim.schedule import validate_schedule
from sim.engine import simulate


def prepare_inputs(inputs, *, seed):
    """C 입력을 RunInputs로 바꾼다. C의 arrival_s는 정확 시각이라 그대로 승객 발생 시각이 된다."""
    return RunInputs(inputs.input_bundle_id, inputs.scenario_id, inputs.checksum,
        inputs.service_date,
        expand((d.demand_id, d.origin, d.destination, d.arrival_s, d.passenger_count)
               for d in inputs.demands),
        tuple(sorted((s.segment_id, s.travel_s) for s in inputs.segments)), seed)


def c_demands(realization, route_of_row):
    """R5 realization을 C Demand(정확 시각)로 바꾼다. C보다 먼저 실행한다(C ADR-001).

    (집계 ID, 발생 초)마다 1행, demand_id는 '<집계 ID>@<초>'. 집계 행에는 노선이 없으므로
    route_of_row(행)가 original_route_id를 직접 돌려줘야 한다(기본값을 추측하지 않음).
    """
    validate_realization(realization)
    rows = {r.aggregate_id: r for r in realization.rows}
    counts = {}
    for p in realization.passengers:
        key = (json.loads(p.passenger_id)[0], p.time_s)
        counts[key] = counts.get(key, 0) + 1
    demands = []
    for (aid, time_s), n in sorted(counts.items()):
        row = rows[aid]
        route = route_of_row(row)
        if not isinstance(route, str) or not route:
            raise SimulationError('missing original_route_id for aggregate row')
        demands.append(Demand(f'{aid}@{time_s}', row.origin, row.destination, time_s, n, route))
    return tuple(sorted(demands, key=lambda d: d.demand_id))


def check_c_demands(inputs, realization, route_of_row):
    """C 입력의 수요가 realization을 변환한 결과와 정확히 같은지 검사한다."""
    try:
        expected = c_demands(realization, route_of_row)
    except SimulationError as exc:
        raise InputError(f'realization rejected: {exc}', 'DATA_VERSION_MISMATCH') from exc
    if tuple(sorted(inputs.demands, key=lambda d: d.demand_id)) != expected:
        raise InputError('C demands differ from the declared R5 realization', 'DATA_VERSION_MISMATCH')


def check_inputs(inputs, run):
    """RunInputs가 이 C 입력에서 만든 것인지 확인한다(승객·주행시간·출처 모두 일치)."""
    expected = prepare_inputs(inputs, seed=run.seed)
    if run != expected:
        raise InputError('RunInputs realization or source mismatch', 'DATA_VERSION_MISMATCH')


def compile_schedule(plan, inputs, run):
    """C 배분안(노선별 차량 수)을 운행편 일정으로 바꾼다.

    차량은 ID 순으로 배정한다. 노선의 순환시간 = 갈 때·돌아올 때 주행 + 정차 × 갈 때 구간 수
    + 회차. 같은 노선 차량은 순환시간을 대수로 나눈 간격으로 출발하고, 복귀가 운영·가용 종료
    안에 끝나는 운행편만 만든다. 승객은 갈 때만 태우고 돌아올 때는 빈 차다.
    """
    validate_allocation(inputs, plan)
    check_inputs(inputs, run)
    segments = {s.segment_id: s for s in inputs.segments}
    travel = dict(run.travel_times)
    routes = {r.route_id: r for r in inputs.routes}
    fleet = iter(sorted(inputs.fleet, key=lambda v:v.vehicle_id))
    trips = []
    for rid, count in sorted(plan.allocation):
        route = routes[rid]
        if not count: continue
        cycle = sum(travel[s] for s in route.outbound+route.return_segments)
        cycle += inputs.policy.dwell_s*len(route.outbound)+inputs.policy.turnaround_s
        for phase in range(count):
            vehicle = next(fleet)
            start = max(inputs.policy.start_s, vehicle.available_start_s)
            end = min(inputs.policy.end_s, vehicle.available_end_s)
            time_s = start + phase*cycle//count
            sequence = 0
            while time_s+cycle <= end:
                stops = [segments[route.outbound[0]].origin]
                arrivals, departures = [time_s], []
                for sid in route.outbound:
                    departures.append(arrivals[-1]+inputs.policy.dwell_s)
                    arrivals.append(departures[-1]+travel[sid])
                    stops.append(segments[sid].destination)
                departures.append(arrivals[-1])
                trips.append(Trip(f'{rid}:{vehicle.vehicle_id}:{sequence:08d}', vehicle.vehicle_id,
                    vehicle.capacity, tuple(stops), tuple(arrivals), tuple(departures), time_s+cycle,
                    sum(segments[s].distance_m for s in route.outbound+route.return_segments)))
                time_s += cycle
                sequence += 1
    validate_schedule(trips)
    return tuple(trips)


def route_of_trips(trips):
    """R12 검사용 trip_id → 노선. compile_schedule의 trip_id 형식 '<노선>:<차량>:<순번>'에서 뽑는다."""
    return {t.trip_id: t.trip_id.split(':', 1)[0] for t in trips}


def assessment(plan, inputs, result):
    """시뮬레이션 결과를 C의 Assessment로 바꾼다. 식별자·분모가 다르면 거부, 독립 검증은 항상 False."""
    validate_allocation(inputs, plan)
    if (result.plan_id, result.plan_revision, result.input_bundle_id, result.source_checksum, result.total) != (
            plan.plan_id, plan.revision, inputs.input_bundle_id, inputs.checksum, inputs.total_demand):
        raise InputError('simulation result identity or denominator mismatch', 'DATA_VERSION_MISMATCH')
    return Assessment(plan.plan_id, plan.revision, inputs.input_bundle_id, inputs.checksum,
        True, False, result.served, result.total, result.p90_wait_s, result.distance_m)


def evaluation_port(run_inputs, config, sink_factory=None):
    """C가 후보 운행안을 평가할 때 부르는 함수를 만든다. 모든 후보가 같은 고정 입력을 쓴다.

    drain_s는 모든 차량 복귀를 포함해야 한다(거리 집계 때문). 오류는 호출자에게 그대로 전달한다.
    """
    def evaluate(plan, inputs):
        trips = compile_schedule(plan, inputs, run_inputs)
        if trips and max(t.return_s for t in trips) > config.drain_s:
            raise SimulationError('drain must include full vehicle duties for Assessment distance')
        result = simulate(plan.plan_id, plan.revision, run_inputs, trips, config,
                          sink_factory() if sink_factory else None)
        return assessment(plan, inputs, result)
    return evaluate


def service_plan(plan, inputs, run):
    """C의 ServicePlan 초안에 운행편 일정과 차량별 운행 목록을 채운다."""
    trips = compile_schedule(plan, inputs, run)
    output = plan.to_service_plan(inputs)
    output['trips'] = [asdict(t) for t in trips]
    output['vehicle_duties'] = [
        {'vehicle_id': vid, 'trip_ids': [t.trip_id for t in trips if t.vehicle_id == vid]}
        for vid in sorted({t.vehicle_id for t in trips})]
    output['schedule_status'] = 'compiled'
    return output
