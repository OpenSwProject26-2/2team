"""The only M05 module allowed to depend on C's prototype DTOs."""
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
    return RunInputs(inputs.input_bundle_id, inputs.scenario_id, inputs.checksum,
        inputs.service_date,
        expand((d.demand_id, d.origin, d.destination, d.arrival_s, d.passenger_count)
               for d in inputs.demands),
        tuple(sorted((s.segment_id, s.travel_s) for s in inputs.segments)), seed)


def c_demands(realization, route_of_row):
    """R5 realization -> C Demand rows with exact arrival_s (C ADR-001, PR #16).

    C defines Demand.arrival_s as an already generated arrival time that is never
    re-drawn from a seed, so R5 runs *before* C: one Demand per (aggregate_id, time_s),
    demand_id '<aggregate_id>@<time_s>'. route_of_row(BinRow) must return the row's
    original_route_id explicitly (OD x bin rows carry no route; no default is guessed).
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
    """Reject C inputs whose demands are not exactly the conversion of `realization`."""
    try:
        expected = c_demands(realization, route_of_row)
    except SimulationError as exc:
        raise InputError(f'realization rejected: {exc}', 'DATA_VERSION_MISMATCH') from exc
    if tuple(sorted(inputs.demands, key=lambda d: d.demand_id)) != expected:
        raise InputError('C demands differ from the declared R5 realization', 'DATA_VERSION_MISMATCH')


def check_inputs(inputs, run):
    expected = prepare_inputs(inputs, seed=run.seed)
    if run != expected:
        raise InputError('RunInputs realization or source mismatch', 'DATA_VERSION_MISMATCH')


def compile_schedule(plan, inputs, run):
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
    """Route identity for R12 checks; compile_schedule names trips '<route>:<vehicle>:<seq>'."""
    return {t.trip_id: t.trip_id.split(':', 1)[0] for t in trips}


def assessment(plan, inputs, result):
    validate_allocation(inputs, plan)
    if (result.plan_id, result.plan_revision, result.input_bundle_id, result.source_checksum, result.total) != (
            plan.plan_id, plan.revision, inputs.input_bundle_id, inputs.checksum, inputs.total_demand):
        raise InputError('simulation result identity or denominator mismatch', 'DATA_VERSION_MISMATCH')
    return Assessment(plan.plan_id, plan.revision, inputs.input_bundle_id, inputs.checksum,
        True, False, result.served, result.total, result.p90_wait_s, result.distance_m)


def evaluation_port(run_inputs, config, sink_factory=None):
    """Bind one fixed realization and explicit policy; exceptions reach the caller.

    For R5 arrivals, build the C inputs from c_demands(realization, ...) first; every
    plan evaluated on those inputs then shares the same exact arrivals.
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
    trips = compile_schedule(plan, inputs, run)
    output = plan.to_service_plan(inputs)
    output['trips'] = [asdict(t) for t in trips]
    output['vehicle_duties'] = [
        {'vehicle_id': vid, 'trip_ids': [t.trip_id for t in trips if t.vehicle_id == vid]}
        for vid in sorted({t.vehicle_id for t in trips})]
    output['schedule_status'] = 'compiled'
    return output
