"""B의 일정·시뮬레이션·독립 검증을 호출하는 포트. 실제 B 구현은 별도."""

from dataclasses import dataclass
from math import isfinite
from typing import Callable

from .candidates import validate_allocation
from .models import InputError, Inputs, Plan, integer, make_plan
from .search import ScoredPlan, require_development


@dataclass(frozen=True)
class Assessment:
    plan_id: str
    plan_revision: int
    input_bundle_id: str
    input_checksum: str
    schedule_feasible: bool
    independently_verified: bool
    served: int
    total: int
    p90_wait_s: float | None
    distance_m: float
    violations: tuple[str, ...] = ()


EvaluationPort = Callable[[Plan, Inputs], Assessment]


def checked_assessment(port: EvaluationPort, plan: Plan, inputs: Inputs) -> Assessment:
    validate_allocation(inputs, plan)
    result = port(plan, inputs)
    if not isinstance(result, Assessment):
        raise InputError("B는 Assessment를 반환해야 함")
    if (result.plan_id, result.plan_revision, result.input_bundle_id, result.input_checksum) != (
        plan.plan_id, plan.revision, inputs.input_bundle_id, inputs.checksum
    ):
        raise InputError("B 평가 결과의 운행안 또는 고정 입력 불일치", "DATA_VERSION_MISMATCH")
    integer(result.served, "served")
    integer(result.total, "total")
    if result.total != inputs.total_demand or result.served > result.total:
        raise InputError("B 결과의 평가 분모 또는 충족 인원 오류")
    for flag in (result.schedule_feasible, result.independently_verified):
        if type(flag) is not bool:
            raise InputError("검증 상태는 bool이어야 함")
    if type(result.distance_m) not in (int, float) or not isfinite(result.distance_m) or result.distance_m < 0:
        raise InputError("유효하지 않은 운행거리")
    if result.p90_wait_s is not None and (
        type(result.p90_wait_s) not in (int, float)
        or not isfinite(result.p90_wait_s) or result.p90_wait_s < 0
    ):
        raise InputError("유효하지 않은 대기시간")
    if result.served and result.p90_wait_s is None:
        raise InputError("충족 인원이 있으면 P90이 필요함")
    return result


@dataclass(frozen=True)
class Selection:
    recommended: Plan | None
    assessments: tuple[Assessment, ...]


def rerank(inputs: Inputs, top: tuple[ScoredPlan, ...], port: EvaluationPort) -> Selection:
    require_development(inputs)
    plans = []
    seen = set()
    for item in sorted(top, key=lambda p: p.rank):
        if item.plan.allocation not in seen:
            plans.append(item.plan)
            seen.add(item.plan.allocation)
        if len(plans) == 3:
            break
    results = tuple(checked_assessment(port, p, inputs) for p in plans)
    valid = [(p, r) for p, r in zip(plans, results)
             if r.schedule_feasible and r.independently_verified and not r.violations]
    if not valid:
        return Selection(None, results)
    chosen, _ = min(valid, key=lambda pair: (
        -pair[1].served, pair[1].p90_wait_s if pair[1].p90_wait_s is not None else float("inf"),
        pair[1].distance_m, pair[0].allocation))
    # 운영 상태 전환은 C가 하지 않는다. 추천안과 근거만 반환한다.
    return Selection(chosen, results)


def evaluate_holdout(development: Inputs, holdout: Inputs, plan: Plan, port: EvaluationPort) -> Assessment:
    require_development(development)
    if holdout.split != "evaluation" or holdout.input_bundle_id == development.input_bundle_id:
        raise InputError("별도 최종 평가 입력이 필요함")
    validate_allocation(development, plan)
    for field in ("stops", "segments", "allowed_turns", "turnaround_stops", "routes", "fleet", "policy"):
        if getattr(development, field) != getattr(holdout, field):
            raise InputError("고정 운행안의 교통망·자원·정책을 변경할 수 없음")
    frozen_plan = make_plan(holdout, dict(plan.allocation), "holdout-of-" + plan.plan_id)
    return checked_assessment(port, frozen_plan, holdout)
