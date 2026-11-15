"""C(planning) 형식의 합성 입력. 실데이터 없음. 난수는 테스트 코드 안에서만 쓴다."""

from __future__ import annotations

import random
from dataclasses import replace
from pathlib import Path

from busstrike.planning.models import Demand, Inputs, Policy, Route, Segment, Vehicle, load_inputs

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "planning" / "synthetic.json"


def c_synthetic() -> Inputs:
    """C의 합성 예제(tests/fixtures/planning/synthetic.json). 파일은 C 소유라 읽기만 한다."""
    return load_inputs(FIXTURE)


def with_capacity(inputs: Inputs, capacity: int) -> Inputs:
    return replace(inputs, fleet=tuple(replace(v, capacity=capacity) for v in inputs.fleet))


def guro_scale(seed: int = 2026, n_routes: int = 30, n_vehicles: int = 60, n_demands: int = 12_000,
               n_stations: int = 8, max_count: int = 7) -> Inputs:
    """구로 규모로 키운 C 형식 합성 입력(06:00–10:30, 노선 30·차량 60·승객 약 4.8만 명).

    C 첫 버전 제약(동일 조건 차량, 모든 후보가 차고 정류장 DEP에서 출발·복귀)에 맞춘다.
    노선: DEP → 자기 정류장 3–6개 → 공용 허브 1개 → 역. 복귀는 같은 길 역방향 공차.
    수요: 노선 위 정류장 → 그 노선의 역 또는 뒤 정류장(대부분), DEP·허브 → 역(노선 공유), 무관한 OD 10%.
    """
    rng = random.Random(seed)
    w_s, w_e = 21_600, 37_800
    stations = [f"ST{k}" for k in range(n_stations)]
    hubs = [f"H{k}" for k in range(max(4, n_routes // 5))]
    segs: dict[tuple[str, str], Segment] = {}

    def seg(a: str, b: str) -> str:
        if (a, b) not in segs:
            back = segs.get((b, a))
            travel = back.travel_s if back else rng.randint(60, 240)
            dist = back.distance_m if back else rng.randint(200, 2000)
            segs[(a, b)] = Segment(f"{a}>{b}", a, b, travel, dist)
        return segs[(a, b)].segment_id

    routes, turns, outs = [], set(), []
    for i in range(n_routes):
        own = [f"R{i:02d}s{j}" for j in range(rng.randint(3, 6))]
        path = ["DEP"] + own + [rng.choice(hubs), stations[i % n_stations]]
        outbound = tuple(seg(a, b) for a, b in zip(path, path[1:]))
        back = list(reversed(path))
        ret = tuple(seg(a, b) for a, b in zip(back, back[1:]))
        ids = outbound + ret
        turns |= set(zip(ids, ids[1:] + ids[:1]))
        routes.append(Route(f"R{i:02d}", outbound, ret, original=i < (2 * n_routes) // 3))
        outs.append(path)
    stops = sorted({s for p in outs for s in p})
    originals = [r.route_id for r in routes if r.original]
    demands = []
    for k in range(n_demands):
        arrival = rng.randint(w_s, w_e - 1800)
        count = rng.randint(1, max_count)
        i = rng.randrange(n_routes)
        path = outs[i]
        r = rng.random()
        if r < 0.10:
            o, d = rng.sample(stops, 2)
        elif r < 0.25:
            o, d = rng.choice(["DEP", path[-2]]), path[-1]
        else:
            j = rng.randrange(0, len(path) - 1)
            o = path[j]
            d = path[-1] if rng.random() < 0.8 else path[rng.randrange(j + 1, len(path))]
        rid = routes[i].route_id if routes[i].original else rng.choice(originals)
        demands.append(Demand(f"g{k:06d}", o, d, arrival, count, rid))
    fleet = tuple(Vehicle(f"V{k:03d}", 45, "DEP", "DEP", w_s, w_e) for k in range(n_vehicles))
    return Inputs(
        input_bundle_id=f"synthetic-guro-scale-{seed}", scenario_id="synthetic-guro-scale",
        dataset_version="synthetic", network_version="synthetic-net", demand_version="synthetic-demand",
        conditions_version="synthetic-cond", service_date="2026-11-02", split="development",
        scope_mode="single_bus_leg", stops=tuple(stops), segments=tuple(sorted(segs.values(), key=lambda s: s.segment_id)),
        allowed_turns=tuple(sorted(turns)), turnaround_stops=tuple(["DEP"] + stations),
        routes=tuple(routes), demands=tuple(demands), fleet=fleet,
        policy=Policy(start_s=w_s, end_s=w_e, dwell_s=10, turnaround_s=180, max_wait_s=1200, max_travel_s=3600),
    )
