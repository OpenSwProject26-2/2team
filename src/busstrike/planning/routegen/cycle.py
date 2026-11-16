"""후보 노선의 주기 C — 시뮬레이터·변환기 함수를 그대로 부른다.

C = Σ 주행시간 + 영업 정차 수 × dwell_base_s + turnaround_s + 공차 복귀 (+ cycle_rest_s).
여기서 이 식을 직접 계산하지 않는다. 단일 노선 비교 입력을 만들어
1. `comparison_plan.to_engine_plan`(S1–S6 검사 + 공차 복귀 경로 = 변환기 규칙)으로 M05 노선을 얻고,
2. `timetable.route_period`(시간표 생성기가 쓰는 고정 주기 = 운영 시간 안 시간대 시작마다의
   `planned_cycle` 최댓값)로 주기를 얻는다.
그래서 근사의 C는 시뮬레이터 시간표의 `period_s`와 같은 값이다(단위 테스트로 확인).
"""

from __future__ import annotations

from dataclasses import dataclass

from busstrike.sim import io as sio
from busstrike.sim.adapters.comparison_plan import to_engine_plan
from busstrike.sim.models import Scenario, SimError
from busstrike.sim.timetable import route_period
from busstrike.sim.traveltime import TravelTimeTable


@dataclass(frozen=True)
class Cycle:
    period_s: int
    return_stops: tuple[str, ...]


def single_route_plan(stops: tuple[str, ...], tt: TravelTimeTable, scenario: Scenario,
                      route_id: str = "cand") -> dict:
    """후보 1개짜리 비교 입력. S5(차량 합 = 가용 대수)를 맞추려고 vehicles = 가용 대수로 둔다(주기와 무관)."""
    return {"plan_id": "cand", "revision": 0, "network_snapshot_id": tt.network_snapshot_id,
            "routes": [{"route_id": route_id, "stops": list(stops), "vehicles": len(scenario.vehicles)}]}


def route_cycle(stops: tuple[str, ...], tt: TravelTimeTable, scenario: Scenario) -> Cycle | None:
    """변환에 실패하면(S 검사·공차 경로 없음) None."""
    raw = single_route_plan(stops, tt, scenario)
    try:
        engine_plan, _ = to_engine_plan(raw, tt, scenario)
    except SimError:
        return None
    route = sio.plan_from_dict(engine_plan).routes[0]
    _, period = route_period(route, scenario.vehicles, scenario.window, scenario.policy, tt)
    return Cycle(period, route.legs[1].stop_ids)
