"""평가 횟수가 제한된 탐욕·차량 이동 탐색 및 소규모 전수 비교."""

from dataclasses import dataclass
from math import comb

from .candidates import candidates
from .evaluator import Estimate, evaluate
from .models import InputError, Inputs, Plan, integer, make_plan


@dataclass(frozen=True)
class ScoredPlan:
    plan: Plan
    estimate: Estimate

    @property
    def rank(self) -> tuple:
        return self.estimate.rank(self.plan)


@dataclass(frozen=True)
class SearchResult:
    best: ScoredPlan
    top: tuple[ScoredPlan, ...]
    evaluations: int
    rejected_candidates: dict[str, str]


def require_development(inputs: Inputs) -> None:
    if inputs.split != "development":
        raise InputError("최종 평가 입력으로 탐색·재선택할 수 없음")


def search(inputs: Inputs, max_evaluations: int = 1000) -> SearchResult:
    require_development(inputs)
    integer(max_evaluations, "max_evaluations", 1)
    accepted, rejected = candidates(inputs)
    ids = [r.route_id for r in accepted]
    cache: dict[tuple, ScoredPlan] = {}

    def score(allocation: dict[str, int]) -> ScoredPlan | None:
        key = tuple(sorted(allocation.items()))
        if key in cache:
            return cache[key]
        if len(cache) >= max_evaluations:
            return None
        plan = make_plan(inputs, allocation, "search")
        item = ScoredPlan(plan, evaluate(inputs, plan))
        cache[key] = item
        return item

    current = score(dict.fromkeys(ids, 0))
    assert current is not None
    for _ in inputs.fleet:
        neighbors = []
        for route_id in ids:
            allocation = dict(current.plan.allocation)
            allocation[route_id] += 1
            item = score(allocation)
            if item is not None:
                neighbors.append(item)
        if not neighbors:
            break
        current = min(neighbors, key=lambda p: p.rank)
        if len(cache) >= max_evaluations:
            break
    while len(cache) < max_evaluations:
        neighbors = []
        for donor, count in current.plan.allocation:
            if not count:
                continue
            for recipient in ids:
                if recipient == donor:
                    continue
                allocation = dict(current.plan.allocation)
                allocation[donor] -= 1
                allocation[recipient] += 1
                item = score(allocation)
                if item is not None:
                    neighbors.append(item)
        if not neighbors:
            break
        best_neighbor = min(neighbors, key=lambda p: p.rank)
        if best_neighbor.rank >= current.rank:
            break
        current = best_neighbor
    ranked = sorted(cache.values(), key=lambda p: p.rank)
    return SearchResult(ranked[0], tuple(ranked[:3]), len(cache), rejected)


def exhaustive(inputs: Inputs, max_combinations: int = 10000) -> SearchResult:
    require_development(inputs)
    integer(max_combinations, "max_combinations", 1)
    accepted, rejected = candidates(inputs)
    ids = [r.route_id for r in accepted]
    fleet = len(inputs.fleet)
    if comb(fleet + len(ids), len(ids)) > max_combinations:
        raise InputError("전수 비교 조합 상한 초과", "OUT_OF_SCOPE")
    ranked = []

    def visit(index: int, remaining: int, allocation: dict[str, int]) -> None:
        if index == len(ids):
            plan = make_plan(inputs, allocation, "exhaustive")
            ranked.append(ScoredPlan(plan, evaluate(inputs, plan)))
            return
        for count in range(remaining + 1):
            visit(index + 1, remaining - count, {**allocation, ids[index]: count})

    visit(0, fleet, {})
    ranked.sort(key=lambda p: p.rank)
    return SearchResult(ranked[0], tuple(ranked[:3]), len(ranked), rejected)
