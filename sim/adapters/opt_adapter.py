"""The only M05 module allowed to depend on C's prototype DTOs."""
from dataclasses import asdict
from opt.candidates import validate_allocation
from opt.integration import Assessment
from opt.models import InputError
from sim.models import Trip, SimulationError
from sim.run_inputs import RunInputs, expand
from sim.arrivals import BinRow, validate_realization
from sim.schedule import validate_schedule
from sim.engine import simulate


def prepare_inputs(inputs, *, seed):
    return RunInputs(inputs.input_bundle_id, inputs.scenario_id, inputs.checksum,
        inputs.service_date,
        expand((d.demand_id, d.origin, d.destination, d.arrival_s, d.passenger_count)
               for d in inputs.demands),
        tuple(sorted((s.segment_id, s.travel_s) for s in inputs.segments)), seed)


def demand_bins(inputs, bin_width_s):
    """R5 mode only: read C Demand.arrival_s as the bin start of an explicit-width bin.

    TODO(ADR, D07/R5): C's Demand has no time_bin field. Whether arrival_s is an exact
    time or a bin start is not decided; the exact mode (prepare_inputs) stays the default.
    """
    return tuple(BinRow(d.demand_id, d.origin, d.destination, d.arrival_s, bin_width_s,
                        d.passenger_count) for d in inputs.demands)


def realized_inputs(inputs, realization):
    """Accept a pre-generated R5 realization after validating it against C demands."""
    widths = {r.bin_width_s for r in realization.rows}
    if len(widths) != 1:
        raise InputError('realization must use one explicit bin width', 'DATA_VERSION_MISMATCH')
    try:
        validate_realization(realization, demand_bins(inputs, widths.pop()))
    except SimulationError as exc:
        raise InputError(f'realization rejected: {exc}', 'DATA_VERSION_MISMATCH') from exc
    return RunInputs(inputs.input_bundle_id, inputs.scenario_id, inputs.checksum,
        inputs.service_date, realization.passengers,
        tuple(sorted((s.segment_id, s.travel_s) for s in inputs.segments)), realization.seed)


def check_inputs(inputs, run, realization=None):
    expected = (prepare_inputs(inputs, seed=run.seed) if realization is None
                else realized_inputs(inputs, realization))
    if run != expected:
        raise InputError('RunInputs realization or source mismatch', 'DATA_VERSION_MISMATCH')


def compile_schedule(plan, inputs, run, realization=None):
    validate_allocation(inputs, plan)
    check_inputs(inputs, run, realization)
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


def assessment(plan, inputs, result):
    validate_allocation(inputs, plan)
    if (result.plan_id, result.plan_revision, result.input_bundle_id, result.source_checksum, result.total) != (
            plan.plan_id, plan.revision, inputs.input_bundle_id, inputs.checksum, inputs.total_demand):
        raise InputError('simulation result identity or denominator mismatch', 'DATA_VERSION_MISMATCH')
    return Assessment(plan.plan_id, plan.revision, inputs.input_bundle_id, inputs.checksum,
        True, False, result.served, result.total, result.p90_wait_s, result.distance_m)


def evaluation_port(run_inputs, config, sink_factory=None, realization=None):
    """Bind one fixed realization and explicit policy; exceptions reach the caller.

    With `realization`, run_inputs must be realized_inputs(inputs, realization); every
    evaluated plan then shares the same pre-generated R5 arrivals.
    """
    def evaluate(plan, inputs):
        trips = compile_schedule(plan, inputs, run_inputs, realization)
        if trips and max(t.return_s for t in trips) > config.drain_s:
            raise SimulationError('drain must include full vehicle duties for Assessment distance')
        result = simulate(plan.plan_id, plan.revision, run_inputs, trips, config,
                          sink_factory() if sink_factory else None)
        return assessment(plan, inputs, result)
    return evaluate


def service_plan(plan, inputs, run, realization=None):
    trips = compile_schedule(plan, inputs, run, realization)
    output = plan.to_service_plan(inputs)
    output['trips'] = [asdict(t) for t in trips]
    output['vehicle_duties'] = [
        {'vehicle_id': vid, 'trip_ids': [t.trip_id for t in trips if t.vehicle_id == vid]}
        for vid in sorted({t.vehicle_id for t in trips})]
    output['schedule_status'] = 'compiled'
    return output
