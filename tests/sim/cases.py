"""simulator.md §9 손계산 시나리오의 합성 입력(실데이터 없음).

승객은 M07 `demand_bins.csv` 행으로 준다. 손계산 사례는 폭 1초 행(인원 1)을 써서 발생 시각을 고정한다
(생성기 오프셋이 반드시 0이다). 그때 passenger_id는 `["<행 ID>",0]`이다(`pid()`).
"""

from __future__ import annotations

import json
from pathlib import Path

from busstrike.sim import (
    BinRow, Leg, PlanSpec, RouteSpec, TravelTimeTable, Vehicle, Window, build_run_inputs, generate,
    write_demand_bins,
)
from busstrike.sim import io

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "sim"
SEED = "s42"

# F1 구간: (from, to) → (distance_m, profile)
F1_SEGMENTS = {
    ("A", "B"): (1000, [[0, 120]]),
    ("B", "S"): (1500, [[0, 180]]),
    ("S", "B"): (1500, [[0, 180]]),
    ("B", "A"): (1000, [[0, 120]]),
}
T3_SEGMENTS = {
    ("B", "S1"): (1500, [[0, 180]]),
    ("S1", "B"): (1500, [[0, 180]]),
    ("B", "S2"): (800, [[0, 100]]),
    ("S2", "B"): (800, [[0, 100]]),
}

# M07 고정 일정: 1인당 정차는 0(simulator.md §3.3, 계약 §7 D3)
F1_POLICY = {
    "allowed_transfers": 0,
    "arrival_method": "systematic",
    "dwell_base_s": 10,
    "dwell_per_boarding_s": 0,
    "dwell_per_alighting_s": 0,
    "turnaround_s": 60,
    "cycle_rest_s": 0,
    "unknown_link": "error",
}

R1_LEGS = [{"in_service": True, "stop_ids": ["A", "B", "S"]},
           {"in_service": False, "stop_ids": ["S", "B", "A"]}]


def pid(aggregate_id: str, index: int = 0) -> str:
    return json.dumps([aggregate_id, index], separators=(",", ":"))


def travel_times_dict(segments: dict, valid_until_s: int = 10_000) -> dict:
    return {
        "schema_version": 1,
        "network_snapshot_id": "synthetic-net-1",
        "valid_from_s": 0,
        "valid_until_s": valid_until_s,
        "segments": [{"from_stop_id": a, "to_stop_id": b, "distance_m": d, "profile": p}
                     for (a, b), (d, p) in sorted(segments.items())],
    }


def vehicle_dict(vid: str, seats: int, end_s: int, start_s: int = 0, **extra) -> dict:
    d = {"vehicle_id": vid, "vehicle_class": "charter", "seat_capacity": seats, "standing_capacity": 0,
         "available_start_s": start_s, "available_end_s": end_s, "initial_stop_id": None, "end_stop_id": None}
    d.update(extra)
    return d


def scenario_dict(window: tuple[int, int], vehicles: list[dict], max_wait_s: int, max_journey_s: int,
                  stop_area: dict | None = None, **policy_over) -> dict:
    policy = dict(F1_POLICY, max_wait_s=max_wait_s, max_journey_s=max_journey_s)
    policy.update(policy_over)
    d = {
        "scenario_id": "sc-synthetic",
        "service_date": "2026-11-02",
        "timezone": "Asia/Seoul",
        "network_snapshot_id": "synthetic-net-1",
        "demand_profile_id": "synthetic-demand-1",
        "strike_route_ids": [],
        "operating_window": {"start_s": window[0], "end_s": window[1]},
        "fleet": {"authorized_count": len(vehicles), "vehicles": vehicles},
        "policy": policy,
    }
    if stop_area is not None:
        d["stop_area"] = stop_area
    return d


def plan_dict(routes: list[dict], plan_id: str = "plan-1", source: str = "municipal", **extra) -> dict:
    d = {"plan_id": plan_id, "scenario_id": "sc-synthetic", "revision": 1, "status": "draft",
         "source": source, "routes": routes, "trips": [], "vehicle_duties": []}
    d.update(extra)
    return d


def pax(aggregate_id: str, origin: str, dest: str, arrival_s: int, count: int = 1, width: int = 1) -> BinRow:
    """승객 행. 폭 1이면 발생 시각 = arrival_s, ID = `["aggregate_id",0..count-1]`."""
    return BinRow(aggregate_id, origin, dest, arrival_s, width, count)


class Case:
    """한 시나리오의 입력 문서 묶음. 파일로도, 메모리 객체로도 꺼낼 수 있다."""

    def __init__(self, scenario: dict, travel_times: dict, plan: dict, rows: list[BinRow], seed=SEED):
        self.scenario = scenario
        self.travel_times = travel_times
        self.plan = plan
        self.rows = rows
        self.seed = seed

    # 메모리 객체
    def objects(self):
        sc = io.scenario_from_dict(self.scenario)
        tt = TravelTimeTable.from_dict(self.travel_times)
        plan = io.plan_from_dict(self.plan)
        return sc, tt, plan, generate(self.rows, self.seed)

    # 파일
    def write(self, d: Path) -> dict[str, Path]:
        d.mkdir(parents=True, exist_ok=True)
        paths = {name: d / name for name in
                 ("scenario.json", "travel_times.json", "demand_bins.csv", "plan.json", "run_inputs.json")}
        io.write_bytes(paths["scenario.json"], io.json_bytes(self.scenario))
        io.write_bytes(paths["travel_times.json"], io.json_bytes(self.travel_times))
        io.write_bytes(paths["plan.json"], (json.dumps(self.plan, ensure_ascii=False, indent=2) + "\n").encode())
        write_demand_bins(self.rows, paths["demand_bins.csv"])
        build_run_inputs("bundle-1", paths["scenario.json"], paths["demand_bins.csv"], paths["travel_times.json"],
                         seed=self.seed, out_path=paths["run_inputs.json"])
        return paths


def r1(vehicle_count: int = 1, **extra) -> dict:
    return dict({"route_id": "R1", "legs": R1_LEGS, "vehicle_count": vehicle_count}, **extra)


def t1() -> Case:
    """§9.2 T1: 정류장 3·차량 1(좌석 3)·승객 5."""
    return Case(
        scenario_dict((0, 1500), [vehicle_dict("V1", 3, 1500)], max_wait_s=600, max_journey_s=1200,
                      wait_threshold_s=60, max_headway_s=900),
        travel_times_dict(F1_SEGMENTS), plan_dict([r1()]),
        [pax("p1", "A", "S", 0), pax("p2", "A", "B", 0), pax("p3", "A", "S", 5),
         pax("p4", "B", "S", 100), pax("p5", "B", "S", 700)],
    )


def t2(max_journey_s: int = 1000) -> Case:
    """§9.3 T2: 만차·FIFO·여정 기한 경계(좌석 2)."""
    return Case(
        scenario_dict((0, 1500), [vehicle_dict("V1", 2, 1500)], max_wait_s=900, max_journey_s=max_journey_s,
                      wait_threshold_s=600),
        travel_times_dict(F1_SEGMENTS), plan_dict([r1()]),
        [pax("q1", "A", "S", 0), pax("q2", "A", "S", 0), pax("q3", "A", "S", 0),
         pax("q4", "B", "S", 100), pax("q5", "B", "S", 100)],
    )


def t3(w1_dest: str = "S2", w2_dest: str = "S2", w3_dest: str = "S1") -> Case:
    """§9.4 T3: 정류장 공유·동시각(stable_id 순)·연결 없음."""
    routes = [
        {"route_id": "R1", "vehicle_count": 1, "legs": [{"in_service": True, "stop_ids": ["B", "S1"]},
                                                        {"in_service": False, "stop_ids": ["S1", "B"]}]},
        {"route_id": "R2", "vehicle_count": 1, "legs": [{"in_service": True, "stop_ids": ["B", "S2"]},
                                                        {"in_service": False, "stop_ids": ["S2", "B"]}]},
    ]
    return Case(
        scenario_dict((0, 450), [vehicle_dict("V1", 1, 450), vehicle_dict("V2", 1, 450)],
                      max_wait_s=600, max_journey_s=1200),
        travel_times_dict(T3_SEGMENTS), plan_dict(routes),
        [pax("w1", "B", w1_dest, 0), pax("w2", "B", w2_dest, 0), pax("w3", "B", w3_dest, 0),
         pax("w4", "B", "A", 0), pax("w5", "X", "S1", 0), pax("w6", "S1", "B", 0)],
    )


def t4() -> Case:
    """§9.5 T4: 시간대별 구간 시간·운행 종료."""
    segs = dict(F1_SEGMENTS)
    segs[("A", "B")] = (1000, [[0, 120], [600, 240]])
    return Case(
        scenario_dict((0, 1700), [vehicle_dict("V1", 3, 1700)], max_wait_s=600, max_journey_s=1200),
        travel_times_dict(segs), plan_dict([r1()]),
        [pax("x1", "A", "S", 790), pax("x2", "A", "S", 801), pax("x3", "B", "S", 1500)],
    )


def t1_expected_log() -> bytes:
    text = (FIXTURES / "t1_events.jsonl").read_text(encoding="utf-8").replace("\r\n", "\n")
    return text.encode("utf-8")


def f1_policy():
    return io.policy_from_dict(dict(F1_POLICY, max_wait_s=600, max_journey_s=1200))


def f1_tt(segments: dict | None = None) -> TravelTimeTable:
    return TravelTimeTable.from_dict(travel_times_dict(segments or F1_SEGMENTS))


def f1_route(vehicle_count: int = 1) -> RouteSpec:
    return RouteSpec("R1", (Leg(True, ("A", "B", "S")), Leg(False, ("S", "B", "A"))), vehicle_count)


def f1_vehicles(n: int, seats: int = 3, end_s: int = 1500) -> list[Vehicle]:
    return [Vehicle(f"V{i + 1}", "charter", seats, 0, 0, end_s) for i in range(n)]


def plan_spec(routes, plan_id: str = "plan-1") -> PlanSpec:
    return PlanSpec(plan_id, "sc-synthetic", 1, "draft", tuple(routes))


F1_WINDOW = Window(0, 1500)


def synthetic(seed: int, n_routes: int = 6, n_pax: int = 400, vehicles_per_route: int = 2,
              window: tuple[int, int] = (21600, 37800), max_wait_s: int = 1200,
              two_station: bool = False) -> Case:
    """결정론·속도 검사용 무작위 합성 입력. 난수는 테스트 코드 안에서만 쓴다(엔진과 무관).

    기본은 M07 출력이 받는 노선(영업 + 복귀 공차)만 만든다. `two_station=True`면 홀수 노선의 복귀 leg도
    영업으로 두어 두 역 연결형을 섞는다(시간표·GTFS 전용, M07 실행은 OUT_OF_SCOPE).
    """
    import random

    rng = random.Random(seed)
    w_s, w_e = window
    stations = [f"ST{k}" for k in range(max(3, n_routes // 4))]
    hubs = [f"H{k}" for k in range(max(4, n_routes // 3))]
    segs: dict = {}

    def seg(a, b):
        if (a, b) not in segs:
            base = rng.randint(60, 240)
            profile = [[0, base]] + [[b0, base + rng.randint(-30, 90)] for b0 in range(w_s + 3600, w_e, 3600)]
            segs[(a, b)] = (rng.randint(200, 2000), profile)

    routes, plain_stops, shapes = [], [], []
    for i in range(n_routes):
        station = stations[i % len(stations)]
        own = [f"R{i:02d}s{j}" for j in range(rng.randint(2, 6))]
        plain_stops += own
        out = own + rng.sample(hubs, 1) + [station]
        back = list(reversed(out))
        both = two_station and i % 2 == 1
        shapes.append((out, back if both else None))
        legs = [{"in_service": True, "stop_ids": out}, {"in_service": both, "stop_ids": back}]
        for leg in legs:
            for a, b in zip(leg["stop_ids"], leg["stop_ids"][1:]):
                seg(a, b)
        routes.append({"route_id": f"R{i:02d}", "legs": legs, "vehicle_count": vehicles_per_route})
    plain_stops += hubs
    capacity = rng.choice([25, 35, 45])
    vehicles = [vehicle_dict(f"V{k:03d}", capacity, w_e, start_s=w_s,
                             standing_capacity=rng.choice([0, 0, 10]))
                for k in range(n_routes * vehicles_per_route + 2)]
    rows: list[BinRow] = []
    made = 0
    k = 0
    while made < n_pax:
        width = rng.choice([1, 60, 300, 900])
        start = rng.randint(w_s, w_e - 1800 - width)
        count = min(rng.randint(1, 3), n_pax - made)
        out, back = shapes[rng.randrange(n_routes)]
        r = rng.random()
        if r < 0.1:      # 노선과 무관한 OD(대부분 no_connection)
            o, d = rng.choice(plain_stops), rng.choice(stations)
        elif back is not None and r < 0.4:
            o, d = back[0], rng.choice(back[1:])
        else:
            j = rng.randrange(len(out) - 1)
            o, d = out[j], (out[-1] if rng.random() < 0.8 else rng.choice(out[j + 1:]))
        if o != d:
            rows.append(BinRow(f"x{k:06d}", o, d, start, width, count))
            made += count
        k += 1
    stop_area = {s: f"area-{s[:3]}" for s in sorted(set(plain_stops))}
    scenario = scenario_dict(window, vehicles, max_wait_s=max_wait_s, max_journey_s=3600, stop_area=stop_area,
                             turnaround_s=180, wait_threshold_s=600, max_headway_s=1800)
    return Case(scenario, travel_times_dict(segs, valid_until_s=w_e), plan_dict(routes), rows, seed=f"syn-{seed}")
