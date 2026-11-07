"""정적 예상 운행편과 구간 용량을 사용하는 근사 평가. B 시뮬레이터 아님."""

from dataclasses import dataclass

from .candidates import validate_allocation
from .models import Inputs, Plan, Route


@dataclass(frozen=True)
class Estimate:
    served: int
    total: int
    mean_wait_s: float | None
    distance_m: int
    estimated: bool = True

    @property
    def fulfillment_rate(self) -> float | None:
        return self.served / self.total if self.total else None

    def rank(self, plan: Plan) -> tuple:
        return (-self.served, self.mean_wait_s if self.mean_wait_s is not None else float("inf"),
                self.distance_m, plan.allocation)


@dataclass
class _Trip:
    route_id: str
    departure_s: int
    stops: tuple[str, ...]
    offsets: tuple[int, ...]
    remaining: list[int]


def route_timing(inputs: Inputs, route: Route) -> tuple[tuple[str, ...], tuple[int, ...], int, int]:
    edges = {s.segment_id: s for s in inputs.segments}
    stops = [edges[route.outbound[0]].origin]
    offsets = [0]
    for sid in route.outbound:
        edge = edges[sid]
        offsets.append(offsets[-1] + inputs.policy.dwell_s + edge.travel_s)
        stops.append(edge.destination)
    cycle = offsets[-1] + inputs.policy.turnaround_s + sum(edges[s].travel_s for s in route.return_segments)
    distance = sum(edges[s].distance_m for s in route.outbound + route.return_segments)
    return tuple(stops), tuple(offsets), cycle, distance


def evaluate(inputs: Inputs, plan: Plan) -> Estimate:
    validate_allocation(inputs, plan)
    if not inputs.fleet:
        return Estimate(0, inputs.total_demand, None, 0)
    vehicle = inputs.fleet[0]
    start = max(inputs.policy.start_s, vehicle.available_start_s)
    end = min(inputs.policy.end_s, vehicle.available_end_s)
    routes = {r.route_id: r for r in inputs.routes}
    trips, distance = [], 0
    for route_id, count in sorted(plan.allocation):
        if not count:
            continue
        stops, offsets, cycle, trip_distance = route_timing(inputs, routes[route_id])
        # shortcut: 주행시간은 고정 스냅샷, 시간대별 혼잡이 필요하면 B 평가로 대체한다.
        for phase in range(count):
            departure = start + phase * cycle // count
            while departure + cycle <= end:
                trips.append(_Trip(route_id, departure, stops, offsets,
                                   [vehicle.capacity] * (len(stops) - 1)))
                distance += trip_distance
                departure += cycle
    served, wait_total = 0, 0
    for demand in sorted(inputs.demands, key=lambda d: (d.arrival_s, d.demand_id)):
        choices = []
        for trip in trips:
            if demand.origin not in trip.stops or demand.destination not in trip.stops:
                continue
            origin, destination = trip.stops.index(demand.origin), trip.stops.index(demand.destination)
            if origin >= destination:
                continue
            boarding = trip.departure_s + trip.offsets[origin]
            arrival = trip.departure_s + trip.offsets[destination]
            wait = boarding - demand.arrival_s
            if 0 <= wait <= inputs.policy.max_wait_s and arrival - demand.arrival_s <= inputs.policy.max_travel_s:
                choices.append((arrival, trip.route_id, trip.departure_s, origin, destination, wait, trip))
        remaining = demand.passenger_count
        for _, _, _, origin, destination, wait, trip in sorted(choices, key=lambda c: c[:3]):
            taken = min(remaining, min(trip.remaining[origin:destination]))
            for edge_index in range(origin, destination):
                trip.remaining[edge_index] -= taken
            remaining -= taken
            served += taken
            wait_total += taken * wait
            if not remaining:
                break
    return Estimate(served, inputs.total_demand, wait_total / served if served else None, distance)
