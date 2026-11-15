"""simulator.md §9 손계산 시나리오의 합성 입력(실데이터 없음)."""

from __future__ import annotations

import json
from pathlib import Path

from busstrike.sim import (
    Leg, PlanSpec, Policy, RouteSpec, TravelTimeTable, Vehicle, Window,
    build_run_inputs, write_arrivals,
)
from busstrike.sim import io

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "sim"

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

F1_POLICY = {
    "allowed_transfers": 0,
    "arrival_method": "systematic",
    "dwell_base_s": 10,
    "dwell_per_boarding_s": 2,
    "dwell_per_alighting_s": 1,
    "turnaround_s": 60,
    "cycle_rest_s": 0,
    "unknown_link": "error",
}

R1_LEGS = [{"in_service": True, "stop_ids": ["A", "B", "S"]},
           {"in_service": False, "stop_ids": ["S", "B", "A"]}]


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
                  **policy_over) -> dict:
    policy = dict(F1_POLICY, max_wait_s=max_wait_s, max_journey_s=max_journey_s)
    policy.update(policy_over)
    return {
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


def plan_dict(routes: list[dict], plan_id: str = "plan-1", source: str = "municipal", **extra) -> dict:
    d = {"plan_id": plan_id, "scenario_id": "sc-synthetic", "revision": 1, "status": "draft",
         "source": source, "routes": routes, "trips": [], "vehicle_duties": []}
    d.update(extra)
    return d


def pax(pid: str, origin, dest, arrival_s: int, max_wait_s: int, kind: str = "access") -> dict:
    origin = [origin] if isinstance(origin, str) else origin
    return {"passenger_id": pid, "kind": kind, "origin_stop_ids": sorted(origin), "dest_stop_ids": sorted(dest),
            "arrival_s": arrival_s, "max_wait_s": max_wait_s}


class Case:
    """한 시나리오의 입력 문서 묶음. 파일로도, 메모리 객체로도 꺼낼 수 있다."""

    def __init__(self, scenario: dict, travel_times: dict, plan: dict, passengers: list[dict]):
        self.scenario = scenario
        self.travel_times = travel_times
        self.plan = plan
        self.passengers = passengers

    # 메모리 객체
    def objects(self):
        sc = io.scenario_from_dict(self.scenario)
        tt = TravelTimeTable.from_dict(self.travel_times)
        plan = io.plan_from_dict(self.plan)
        pas = [io.passenger_from_dict(p) for p in self.passengers]
        return sc, tt, plan, pas

    # 파일
    def write(self, d: Path) -> dict[str, Path]:
        d.mkdir(parents=True, exist_ok=True)
        paths = {name: d / name for name in
                 ("scenario.json", "travel_times.json", "arrivals.jsonl", "plan.json", "run_inputs.json")}
        io.write_bytes(paths["scenario.json"], io.json_bytes(self.scenario))
        io.write_bytes(paths["travel_times.json"], io.json_bytes(self.travel_times))
        io.write_bytes(paths["plan.json"], (json.dumps(self.plan, ensure_ascii=False, indent=2) + "\n").encode())
        write_arrivals([io.passenger_from_dict(p) for p in self.passengers], paths["arrivals.jsonl"])
        build_run_inputs("bundle-1", paths["scenario.json"], paths["arrivals.jsonl"], paths["travel_times.json"],
                         seed=42, bin_length_s=900, out_path=paths["run_inputs.json"])
        return paths


def r1(vehicle_count: int = 1, **extra) -> dict:
    return dict({"route_id": "R1", "legs": R1_LEGS, "vehicle_count": vehicle_count}, **extra)


def t1() -> Case:
    w = 600
    return Case(
        scenario_dict((0, 1500), [vehicle_dict("V1", 3, 1500)], max_wait_s=w, max_journey_s=1200),
        travel_times_dict(F1_SEGMENTS), plan_dict([r1()]),
        [pax("p1", "A", ["S"], 0, w), pax("p2", "A", ["B"], 0, w), pax("p3", "A", ["S"], 5, w),
         pax("p4", "B", ["S"], 100, w), pax("p5", "B", ["S"], 700, w)],
    )


def t2(end_s: int = 1500) -> Case:
    w = 900
    return Case(
        scenario_dict((0, end_s), [vehicle_dict("V1", 2, end_s)], max_wait_s=w, max_journey_s=1000),
        travel_times_dict(F1_SEGMENTS), plan_dict([r1()]),
        [pax("q1", "A", ["S"], 0, w), pax("q2", "A", ["S"], 0, w), pax("q3", "A", ["S"], 0, w),
         pax("q4", "B", ["S"], 100, w), pax("q5", "B", ["S"], 100, w)],
    )


def t3(w1_dest=("S1", "S2"), w2_dest=("S1", "S2")) -> Case:
    w = 600
    routes = [
        {"route_id": "R1", "vehicle_count": 1, "legs": [{"in_service": True, "stop_ids": ["B", "S1"]},
                                                        {"in_service": False, "stop_ids": ["S1", "B"]}]},
        {"route_id": "R2", "vehicle_count": 1, "legs": [{"in_service": True, "stop_ids": ["B", "S2"]},
                                                        {"in_service": False, "stop_ids": ["S2", "B"]}]},
    ]
    return Case(
        scenario_dict((0, 450), [vehicle_dict("V1", 1, 450), vehicle_dict("V2", 1, 450)],
                      max_wait_s=w, max_journey_s=1200),
        travel_times_dict(T3_SEGMENTS), plan_dict(routes),
        [pax("w1", "B", list(w1_dest), 0, w), pax("w2", "B", list(w2_dest), 0, w), pax("w3", "B", ["A"], 0, w),
         pax("w4", "X", ["S1"], 0, w), pax("w5", "S1", ["B"], 0, w)],
    )


def t4() -> Case:
    w = 600
    segs = dict(F1_SEGMENTS)
    segs[("A", "B")] = (1000, [[0, 120], [600, 240]])
    return Case(
        scenario_dict((0, 1700), [vehicle_dict("V1", 3, 1700)], max_wait_s=w, max_journey_s=1200),
        travel_times_dict(segs), plan_dict([r1()]),
        [pax("x1", "A", ["S"], 790, w), pax("x2", "A", ["S"], 801, w), pax("x3", "B", ["S"], 1500, w)],
    )


def t1_expected_log() -> bytes:
    text = (FIXTURES / "t1_events.jsonl").read_text(encoding="utf-8").replace("\r\n", "\n")
    return text.encode("utf-8")


def f1_policy() -> Policy:
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
              window: tuple[int, int] = (21600, 37800), max_wait_s: int = 1200) -> Case:
    """결정론·속도 검사용 무작위 합성 입력. 난수는 테스트 코드 안에서만 쓴다(엔진과 무관)."""
    import random

    rng = random.Random(seed)
    w_s, w_e = window
    stations = {f"ST{k}": [f"ST{k}a", f"ST{k}b"] for k in range(max(3, n_routes // 4))}
    station_ids = sorted(stations)
    hubs = [f"H{k}" for k in range(max(4, n_routes // 3))]
    segs: dict = {}

    def seg(a, b):
        if (a, b) not in segs:
            base = rng.randint(60, 240)
            profile = [[0, base]] + [[b0, base + rng.randint(-30, 90)] for b0 in range(w_s + 3600, w_e, 3600)]
            segs[(a, b)] = (rng.randint(200, 2000), profile)

    routes, plain_stops, shapes = [], [], []
    for i in range(n_routes):
        st_id = station_ids[i % len(station_ids)]
        st = stations[st_id]
        own = [f"R{i:02d}s{j}" for j in range(rng.randint(2, 6))]
        plain_stops += own
        out = own + rng.sample(hubs, 1) + [st[i % 2]]
        back = list(reversed(out))
        shapes.append((st_id, out, back if i % 2 == 1 else None))
        legs = [{"in_service": True, "stop_ids": out}, {"in_service": i % 2 == 1, "stop_ids": back}]
        for leg in legs:
            for a, b in zip(leg["stop_ids"], leg["stop_ids"][1:]):
                seg(a, b)
        routes.append({"route_id": f"R{i:02d}", "legs": legs, "vehicle_count": vehicles_per_route})
    plain_stops += hubs
    vehicles = [vehicle_dict(f"V{k:03d}", rng.choice([25, 35, 45]), w_e, start_s=w_s,
                             standing_capacity=rng.choice([0, 0, 10]))
                for k in range(n_routes * vehicles_per_route + 2)]
    passengers = []
    for n in range(n_pax):
        arrival = rng.randint(w_s, w_e - 1800)
        st_id, out, back = shapes[rng.randrange(n_routes)]
        r = rng.random()
        if r < 0.1:      # 노선과 무관한 OD(대부분 no_service)
            st_id, stop = rng.choice(station_ids), rng.choice(plain_stops)
            egress = r < 0.03
        elif back is not None and r < 0.4:
            stop, egress = rng.choice(back[1:]), True
        else:
            stop, egress = rng.choice(out[:-1]), False
        station = stations[st_id]
        if egress:
            passengers.append(pax(f"x{n:06d}", station, [stop], arrival, max_wait_s, kind="egress"))
        else:
            passengers.append(pax(f"x{n:06d}", stop, station, arrival, max_wait_s))
    scenario = scenario_dict(window, vehicles, max_wait_s=max_wait_s, max_journey_s=3600,
                             dwell_per_boarding_s=3, dwell_per_alighting_s=2, turnaround_s=180)
    return Case(scenario, travel_times_dict(segs, valid_until_s=w_e), plan_dict(routes), passengers)
