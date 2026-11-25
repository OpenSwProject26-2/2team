"""소외 우선 삽입(`Params.fill`, 작업 2026-11-25). 배차 예산(n × H) 없이 다시 이은 것 — 규칙은 예전 `budget.BudgetTours.fill` 그대로.

- 골격 앵커 사이에 정류장을 하나씩 끼운다. 점수 = (늘어나는 값) ÷ (늘어나는 순환 시간 근사), 늘어나는 순환 ≤ 0이면 ∞.
  값 = 탑승 근사(`budget.RideApprox`) + β × 덮는 소외 수요(β = `fill_bonus`). 늘어나는 값 ≤ 1e-9인 삽입은 버린다.
  동률은 (점수, 늘어나는 값, −자리, 정류장 ID) 큰 것. 더 끼울 것이 없을 때까지.
- 바뀐 것(사용자 결정 2026-11-25): 탑승 근사의 대수 n = max(1, ⌈순환 / 배분기 대기 한도 wait_s(1200초)⌉) = 배분기 개설 단위.
  한도는 그 후보 종류의 기존 한도(`ok`로 받는다: 순회 = 2L, 편도 노선 생성 후 순회 = 방향별 L·우회율·정차·고리 닫기).
  배차 = C / n ≤ wait_s라서 예전과 같이 대기 항 min(1, W·n/C)은 1이다. 후보 대수는 고정하지 않는다(배분기가 정한다).
- 정류장 사이: 앵커마다 직전 정류장을 보고 U턴 없는 최단경로(`Graph.path_continue`), 양 끝 정류장은 중간에 지나지 않는다.
"""

from __future__ import annotations

import math
from typing import Callable

EPS = 1e-9


class Connector:
    """앵커를 잇는 정차열(U턴 없음). 같은 정류장이 두 번이면 None. 구간 결과는 기억한다."""

    def __init__(self, g):
        self.g = g
        self._legs: dict[tuple, tuple | None] = {}

    def __call__(self, anchors: list[str]) -> tuple[str, ...] | None:
        ends = frozenset({anchors[0], anchors[-1]})
        out = [anchors[0]]
        for a, b in zip(anchors, anchors[1:]):
            prev = out[-2] if len(out) > 1 else None
            key = (prev, a, b, ends)
            if key not in self._legs:
                self._legs[key] = self.g.path_continue(prev, a, b, ends - {a, b})
            leg = self._legs[key]
            if leg is None:
                return None
            out.extend(leg[1:])
        if len(set(out)) != len(out):
            return None
        return tuple(out)


class FillInserter:
    """connect(앵커) → 정차열, violations(정차열) → 위반 사유 목록(비면 통과), cycle(정차열) → 순환 근사(초)."""

    def __init__(self, connect: Callable, violations: Callable, cycle: Callable, approx, beta: float, wait_s: int,
                 count: Callable | None = None):
        self.connect, self.violations, self.cycle = connect, violations, cycle
        self.ap, self.beta, self.wait_s = approx, float(beta), int(wait_s)
        self.count = count or (lambda stage, reason: None)

    def n_of(self, cycle_s: int) -> int:
        return max(1, math.ceil(cycle_s / self.wait_s - EPS))

    def value(self, stops: tuple[str, ...]) -> tuple[int, float, float]:
        """(순환 근사, 탑승 근사, 삽입 점수의 값 = 탑승 근사 + β × 소외 수요)."""
        cyc = self.cycle(stops)
        r, f = self.ap.evaluate(stops, self.n_of(cyc), cyc)
        return cyc, r, r + self.beta * f

    def fill(self, anchors: list[str], pool: list[str], stage: str) -> tuple[str, ...] | None:
        stops = self.connect(anchors)
        if stops is None:
            self.count(stage, "no_path_or_repeat")
            return None
        bad = self.violations(stops)
        for r in bad or ["ok"]:
            self.count(stage, r)
        if bad:
            return None
        cur_t, _, cur_v = self.value(stops)
        anchors = list(anchors)
        while True:
            on = set(stops)
            best = None
            for s in pool:
                if s in on:
                    continue
                for pos in range(1, len(anchors)):
                    trial = anchors[:pos] + [s] + anchors[pos:]
                    new = self.connect(trial)
                    if new is None or self.violations(new):
                        continue
                    t, _, v = self.value(new)
                    dv = v - cur_v
                    if dv <= EPS:
                        continue
                    dt = t - cur_t
                    score = math.inf if dt <= 0 else dv / dt
                    key = (score, dv, -pos, s)
                    if best is None or key > best[0]:
                        best = (key, trial, new, t, v)
            if best is None:
                break
            _, anchors, stops, cur_t, cur_v = best
        return stops


def stop_far_weights(approx, station_stops) -> dict[str, float]:
    """동네 끝 정류장 → 소외 수요(시간대 가중) 합(예전 `_fill_tours`와 같은 식)."""
    from .equity import home_end
    out: dict[str, float] = {}
    dz = set(station_stops)
    for k, v in approx.far_w.items():
        h = home_end(k, dz)
        out[h] = out.get(h, 0.0) + v
    return out


def ranked_pool(values: dict[str, float], stop_far: dict[str, float], beta: float, size: int) -> list[str]:
    """삽입 후보 정류장: (값 + β × 소외 수요) 큰 순 size개, 동률 ID."""
    return sorted(values, key=lambda x: (-(values[x] + beta * stop_far.get(x, 0.0)), x))[:size]
