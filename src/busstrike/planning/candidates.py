"""고정 후보의 방향·회전·회차 검사."""

from .models import InputError, Inputs, Plan, Route, identifier, integer


def route_error(inputs: Inputs, route: Route) -> str | None:
    segments = {s.segment_id: s for s in inputs.segments}
    ids = route.outbound + route.return_segments
    if not route.outbound or not route.return_segments:
        return "수송 구간과 복귀 구간이 모두 필요함"
    if any(s not in segments for s in ids):
        return "존재하지 않는 구간"
    edges = [segments[s] for s in ids]
    if any(a.destination != b.origin for a, b in zip(edges, edges[1:])):
        return "불연속 구간"
    if edges[-1].destination != edges[0].origin:
        return "출발 거점으로 복귀하지 않음"
    if any(pair not in inputs.allowed_turns for pair in zip(ids, ids[1:] + ids[:1])):
        return "허용되지 않은 구간 전이"
    terminal = segments[route.outbound[-1]].destination
    if terminal not in inputs.turnaround_stops or edges[0].origin not in inputs.turnaround_stops:
        return "회차 가능 정보 없음"
    stops = [segments[route.outbound[0]].origin] + [segments[s].destination for s in route.outbound]
    if len(set(stops)) != len(stops):
        return "첫 버전은 수송 구간의 중복 정류장 방문을 지원하지 않음"
    if inputs.fleet and edges[0].origin != inputs.fleet[0].initial_stop:
        return "차고지 공차 이동 자료가 필요한 후보"
    return None


def candidates(inputs: Inputs) -> tuple[tuple[Route, ...], dict[str, str]]:
    accepted, rejected = [], {}
    for route in sorted(inputs.routes, key=lambda r: r.route_id):
        error = route_error(inputs, route)
        if error:
            rejected[route.route_id] = error
        else:
            accepted.append(route)
    return tuple(accepted), rejected


def validate_allocation(inputs: Inputs, plan: Plan) -> None:
    identifier(plan.plan_id)
    integer(plan.revision, "revision", 1)
    if plan.scenario_id != inputs.scenario_id or plan.input_checksum != inputs.checksum:
        raise InputError("운행안과 실행 입력 버전 불일치", "DATA_VERSION_MISMATCH")
    routes = {r.route_id: r for r in inputs.routes}
    seen = set()
    total = 0
    for route_id, count in plan.allocation:
        integer(count, "vehicle_count")
        if route_id in seen or route_id not in routes:
            raise InputError("중복 또는 미등록 노선 배분")
        seen.add(route_id)
        total += count
        if count and (error := route_error(inputs, routes[route_id])):
            raise InputError(error, "PLAN_INFEASIBLE")
    if total > len(inputs.fleet):
        raise InputError("가용 차량 수 초과", "PLAN_INFEASIBLE")
