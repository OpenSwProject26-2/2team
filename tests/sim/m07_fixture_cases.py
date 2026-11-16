"""giho999 M07 fixture F1·F2의 scenario 입력을 새 엔진 입력으로 옮긴 것(교차 검증용, 합성만).

M07 `network.json`은 운행편의 방문 시각·복귀 시각·거리만 주고 구간별 시간·거리를 주지 않는다. 새 엔진은
구간 자료(`travel_times.json`)와 노선 leg로 일정을 만들므로, fixture 일정이 **그대로** 다시 나오도록
구간 값을 손으로 정했다(아래 주석의 계산). 운행편은 운행안이 직접 준 시간표(`provided`)로 넣어 trip_id까지
fixture와 같게 한다. 수요는 fixture의 `demand_bins.csv`와 seed(`measurement.arrival_seed`)를 그대로 쓴다.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.sim.cases import Case, travel_times_dict, vehicle_dict
from busstrike.sim import io

FIXTURES = Path(__file__).resolve().parents[1] / "evaluation" / "fixtures"
F1_NAMES = ("f1", "f1-w9", "f1-j14", "f1-drain12", "f1-wj9")
F2_NAMES = ("f2", "f2-headway599")


def _fixture(name: str) -> tuple[dict, dict]:
    run = FIXTURES / name / "run"
    return json.loads((run / "measurement.json").read_text()), json.loads((FIXTURES / name / "scenario" / "network.json").read_text())


def _trip(trip_id, route_id, vehicle_id, cycle, leg, in_service, stops_times):
    return {"trip_id": trip_id, "route_id": route_id, "vehicle_id": vehicle_id, "cycle_index": cycle,
            "leg_index": leg, "in_service": in_service,
            "stop_times": [{"stop_seq": i, "stop_id": s, "sched_s": t} for i, (s, t) in enumerate(stops_times)]}


def _scenario(measurement: dict, window, vehicles, stop_area, dwell_base_s) -> dict:
    cfg, pol = measurement["config"], measurement["policy"]
    return {
        "scenario_id": "sc-m07-fixture", "service_date": "2026-11-09", "timezone": "Asia/Seoul",
        "network_snapshot_id": "synthetic-net-1", "demand_profile_id": "m07-fixture-demand",
        "strike_route_ids": [], "operating_window": {"start_s": window[0], "end_s": window[1]},
        "fleet": {"authorized_count": len(vehicles), "vehicles": vehicles},
        "stop_area": stop_area,
        "policy": {"max_wait_s": cfg["max_wait_s"], "max_journey_s": cfg["max_journey_s"], "drain_s": cfg["drain_s"],
                   "max_headway_s": pol["max_headway_s"], "wait_threshold_s": pol["wait_threshold_s"],
                   "allowed_transfers": 0, "arrival_method": "systematic", "dwell_base_s": dwell_base_s,
                   "dwell_per_boarding_s": 0, "dwell_per_alighting_s": 0, "turnaround_s": 0, "cycle_rest_s": 0,
                   "unknown_link": "error"},
    }


def _plan(routes, trips) -> dict:
    duties = {}
    for t in trips:
        duties.setdefault(t["vehicle_id"], []).append(t)
    vehicle_duties = [{"vehicle_id": v, "route_id": ts[0]["route_id"], "trip_ids": [t["trip_id"] for t in ts],
                       "start_s": ts[0]["stop_times"][0]["sched_s"], "end_s": ts[-1]["stop_times"][-1]["sched_s"]}
                      for v, ts in sorted(duties.items())]
    return {"plan_id": "plan-m07-fixture", "scenario_id": "sc-m07-fixture", "revision": 1, "status": "draft",
            "source": "fixture", "routes": routes, "trips": trips, "vehicle_duties": vehicle_duties}


def f1_case(name: str) -> Case:
    """F1: A→B 정원 2, 도착 0·10, 주행 5초, 복귀 5초(return 10·20), 거리 0, 정차 0(도착 = 출발)."""
    measurement, _ = _fixture(name)
    segs = {("A", "B"): (0, [[0, 5]]), ("B", "A"): (0, [[0, 5]])}
    legs = [{"in_service": True, "stop_ids": ["A", "B"]}, {"in_service": False, "stop_ids": ["B", "A"]}]
    trips = [_trip("f1:v1:0000", "r1", "v1", 0, 0, True, [("A", 0), ("B", 5)]),
             _trip("f1:v1:0000:return", "r1", "v1", 0, 1, False, [("B", 5), ("A", 10)]),
             _trip("f1:v1:0001", "r1", "v1", 1, 0, True, [("A", 10), ("B", 15)]),
             _trip("f1:v1:0001:return", "r1", "v1", 1, 1, False, [("B", 15), ("A", 20)])]
    scenario = _scenario(measurement, (0, 20), [vehicle_dict("v1", 2, 20)], {"A": "dong-1", "B": "dong-1"}, 0)
    rows = io.read_demand_bins(FIXTURES / name / "scenario" / "demand_bins.csv")
    return Case(scenario, travel_times_dict(segs, valid_until_s=20),
                _plan([{"route_id": "r1", "legs": legs, "vehicle_count": 1}], trips), list(rows),
                seed=measurement["arrival_seed"])


def f2_case(name: str) -> Case:
    """F2: t1·t2(v1, 정원 2, r1)·t3(v2, 정원 5, r2). 정차 10초(A·B 출발 = 도착 + 10), 회차 0.

    - A→B 90초(모든 trip), B→C는 출발 시각 시간대별 90/490/90초(t1 210→300, t3 510→1000, t2 810→900).
    - r1 복귀 C→A 100초(300→400, 900→1000). r2 복귀는 C→Y→A 25+25초(1000→1050). Y는 정류장 방문이 없는
      공차 경유지다(M07 일정에 나타나지 않는다).
    - 거리: A→B 100, B→C 100, C→A 800 → r1 = 1000 m. C→Y 150, Y→A 150 → r2 = 500 m(fixture distance_m).
    """
    measurement, _ = _fixture(name)
    segs = {("A", "B"): (100, [[0, 90]]), ("B", "C"): (100, [[0, 90], [500, 490], [600, 90]]),
            ("C", "A"): (800, [[0, 100]]), ("C", "Y"): (150, [[0, 25]]), ("Y", "A"): (150, [[0, 25]])}
    routes = [{"route_id": "r1", "vehicle_count": 1, "vehicle_ids": ["v1"],
               "legs": [{"in_service": True, "stop_ids": ["A", "B", "C"]},
                        {"in_service": False, "stop_ids": ["C", "A"]}]},
              {"route_id": "r2", "vehicle_count": 1, "vehicle_ids": ["v2"],
               "legs": [{"in_service": True, "stop_ids": ["A", "B", "C"]},
                        {"in_service": False, "stop_ids": ["C", "Y", "A"]}]}]
    trips = [_trip("t1", "r1", "v1", 0, 0, True, [("A", 100), ("B", 200), ("C", 300)]),
             _trip("t1:return", "r1", "v1", 0, 1, False, [("C", 300), ("A", 400)]),
             _trip("t2", "r1", "v1", 1, 0, True, [("A", 700), ("B", 800), ("C", 900)]),
             _trip("t2:return", "r1", "v1", 1, 1, False, [("C", 900), ("A", 1000)]),
             _trip("t3", "r2", "v2", 0, 0, True, [("A", 400), ("B", 500), ("C", 1000)]),
             _trip("t3:return", "r2", "v2", 0, 1, False, [("C", 1000), ("Y", 1025), ("A", 1050)])]
    scenario = _scenario(measurement, (0, 1100), [vehicle_dict("v1", 2, 1100), vehicle_dict("v2", 5, 1100)],
                         {"A": "dong-1", "B": "dong-1", "C": "dong-2", "D": "dong-2"}, 10)
    rows = io.read_demand_bins(FIXTURES / name / "scenario" / "demand_bins.csv")
    return Case(scenario, travel_times_dict(segs, valid_until_s=1100), _plan(routes, trips), list(rows),
                seed=measurement["arrival_seed"])


def case_of(name: str) -> Case:
    return f1_case(name) if name.startswith("f1") else f2_case(name)
