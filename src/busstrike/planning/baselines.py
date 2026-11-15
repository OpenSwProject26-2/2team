"""기존 노선 균등·수요 비례 배분."""

from .candidates import route_error
from .models import InputError, Inputs, Plan, make_plan


def baseline(inputs: Inputs, proportional: bool = False) -> Plan:
    routes = sorted((r for r in inputs.routes if r.original), key=lambda r: r.route_id)
    if not routes:
        raise InputError("기존 노선이 필요함")
    for route in routes:
        if error := route_error(inputs, route):
            raise InputError(f"기준안 노선 {route.route_id}: {error}", "PLAN_INFEASIBLE")
    weights = {r.route_id: 0 for r in routes}
    if proportional:
        for demand in inputs.demands:
            weights[demand.original_route_id] += demand.passenger_count
    if not sum(weights.values()):
        weights = dict.fromkeys(weights, 1)
    total, fleet = sum(weights.values()), len(inputs.fleet)
    allocation = {r: fleet * w // total for r, w in weights.items()}
    order = sorted(weights, key=lambda r: (-(fleet * weights[r] % total), r))
    for route_id in order[:fleet - sum(allocation.values())]:
        allocation[route_id] += 1
    return make_plan(inputs, allocation, "proportional" if proportional else "equal")
