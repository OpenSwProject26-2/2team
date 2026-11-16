"""구 단독(per_gu) 차량 배분(설계 v1 5절).

충족 근사 v2 (시간 h = 6, 7, 8, 9시를 따로 계산해 더한다)
    served_k,h = κ · d_k,h · min(1, W · Σ_{r∋k} n_r / C_r)
    노선별 승객 = served_k,h × (n_r/C_r) / Σ_{r∋k} n_r/C_r   (배차 비율로 나눔)
    노선 수송 한도 n_r × 3600 / C_r × 좌석(시간당)을 넘는 몫은 잘라낸다.

개설: 새 노선은 n0 = ⌈C/W⌉대를 한 번에 넣는다. 비교 기준은 1대당 이득.
탐욕: 지연 평가(CELF) 우선순위 큐. 국소 탐색: 1대 옮기기, 가치 낮은 노선 닫고 재배분 — 나아지지 않을 때까지.
차량 합은 정확히 가용 대수. 남는 대수는 1대씩 이득이 가장 큰 열린 노선에 넣는다.
난수를 쓰지 않는다. 동률은 노선 ID 순.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass

EPS = 1e-9


@dataclass(frozen=True)
class AllocParams:
    wait_s: int = 1200
    seat: int = 45
    kappa: float = 1.0


class Model:
    """후보 노선(route_id, 주기, 덮는 OD 키)과 시간별 OD 수요로 근사 충족을 계산한다."""

    def __init__(self, routes: list[tuple[str, int, tuple[tuple[str, str], ...]]],
                 od_hourly: dict[tuple[str, str], list[float]], p: AllocParams = AllocParams()):
        self.p = p
        self.ids = sorted(r for r, _, _ in routes)
        self.cycle = {r: c for r, c, _ in routes}
        keys = sorted({k for _, _, ks in routes for k in ks})
        self.key_index = {k: i for i, k in enumerate(keys)}
        self.keys = keys
        self.d = [tuple(od_hourly[k]) for k in keys]
        self.cover = {r: tuple(self.key_index[k] for k in ks) for r, _, ks in routes}
        self.n0 = {r: max(1, math.ceil(c / p.wait_s)) for r, c, _ in routes}

    def detail(self, n: dict[str, int]) -> dict:
        """{'total', 'by_route': {r: [h6..h9 승객]}, 'demand_covered', 'cap_cut'}."""
        W, seat, kappa = self.p.wait_s, self.p.seat, self.p.kappa
        freq = {r: n[r] / self.cycle[r] for r in sorted(n) if n[r] > 0}
        f: dict[int, float] = {}
        for r, fr in freq.items():
            for ki in self.cover[r]:
                f[ki] = f.get(ki, 0.0) + fr
        by_route: dict[str, list[float]] = {}
        total = 0.0
        cut = 0.0
        for r, fr in freq.items():
            cap = fr * 3600 * seat
            loads = [0.0, 0.0, 0.0, 0.0]
            for ki in self.cover[r]:
                fk = f[ki]
                share = kappa * min(1.0, W * fk) * fr / fk
                dk = self.d[ki]
                for h in range(4):
                    loads[h] += dk[h] * share
            kept = [min(x, cap) for x in loads]
            cut += sum(loads) - sum(kept)
            by_route[r] = kept
            total += sum(kept)
        covered = sum(sum(self.d[ki]) for ki in f)
        return {"total": total, "by_route": by_route, "demand_covered": covered, "cap_cut": cut}

    def value(self, n: dict[str, int]) -> float:
        return self.detail(n)["total"]


def _with(n: dict[str, int], r: str, k: int) -> dict[str, int]:
    m = dict(n)
    m[r] = m.get(r, 0) + k
    if m[r] <= 0:
        del m[r]
    return m


def greedy(model: Model, budget: int, start: dict[str, int] | None = None) -> dict[str, int]:
    """CELF 지연 평가 탐욕. 움직임: 닫힌 노선 개설(n0대) 또는 열린 노선 +1대. 1대당 이득 순."""
    n = dict(start or {})
    used = sum(n.values())
    base = model.value(n)
    it = 0
    heap: list[tuple[float, str, str, int, int]] = []   # (-이득/대, route_id, 종류, 대수, stamp)

    def gain(r: str, k: int) -> float:
        return (model.value(_with(n, r, k)) - base) / k

    for r in model.ids:
        k = 1 if r in n else model.n0[r]
        if used + k <= budget:
            heapq.heappush(heap, (-gain(r, k), r, "add" if r in n else "open", k, it))
    while heap:
        neg, r, kind, k, stamp = heapq.heappop(heap)
        if used + k > budget:
            continue                                   # 예산은 줄기만 하므로 영영 못 쓴다
        if (kind == "open") == (r in n):
            continue                                   # 이미 열렸거나 닫힌 노선의 낡은 움직임
        if stamp != it:
            heapq.heappush(heap, (-gain(r, k), r, kind, k, it))
            continue
        if -neg <= EPS:
            break
        n = _with(n, r, k)
        used += k
        base = model.value(n)
        it += 1
        if used + 1 <= budget:
            heapq.heappush(heap, (-gain(r, 1), r, "add", 1, it))
    return n


def fill(model: Model, budget: int, n: dict[str, int]) -> dict[str, int]:
    """남는 대수를 1대씩 이득이 가장 큰 열린 노선에 넣는다(이득 0이어도 넣는다: 변환기 S5)."""
    n = dict(n)
    while sum(n.values()) < budget:
        left = budget - sum(n.values())
        if n:
            base = model.value(n)
            r = max(sorted(n), key=lambda x: (model.value(_with(n, x, 1)) - base, -model.ids.index(x)))
            n = _with(n, r, 1)
        else:                                          # 열린 노선이 없으면 남은 대수로 가장 좋은 노선 하나를 연다
            r = max(model.ids, key=lambda x: (model.value({x: min(left, model.n0[x])}), -model.ids.index(x)))
            n = {r: min(left, model.n0[r])}
    return n


def local_search(model: Model, budget: int, n: dict[str, int], max_rounds: int = 1000) -> tuple[dict[str, int], list[dict]]:
    """1대 옮기기 → (없으면) 가치 낮은 노선 닫고 재배분. 더 나아지지 않을 때까지. 기록을 함께 돌려준다."""
    cur = model.value(n)
    log: list[dict] = []
    for _ in range(max_rounds):
        best = None
        for a in sorted(n):
            for b in sorted(n):
                if a == b:
                    continue
                m = _with(_with(n, a, -1), b, 1)
                v = model.value(m)
                if v > cur + EPS and (best is None or v > best[0] + EPS):
                    best = (v, m, {"move": "shift1", "from": a, "to": b})
        if best is None:
            vals = sorted(n, key=lambda r: (cur - model.value({x: y for x, y in n.items() if x != r}), r))
            for r in vals:
                rest = {x: y for x, y in n.items() if x != r}
                m = fill(model, budget, greedy(model, budget, rest))
                v = model.value(m)
                if v > cur + EPS:
                    best = (v, m, {"move": "close_reopt", "closed": r})
                    break
        if best is None:
            break
        log.append(dict(best[2], gain=round(best[0] - cur, 6)))
        cur, n = best[0], best[1]
    return n, log


def allocate(model: Model, budget: int) -> dict:
    g = greedy(model, budget)
    v_greedy = model.value(g)
    f = fill(model, budget, g)
    n, log = local_search(model, budget, f)
    if sum(n.values()) != budget or any(v < 1 for v in n.values()):
        raise AssertionError(f"vehicle sum {sum(n.values())} != {budget}")
    return {"n": dict(sorted(n.items())), "value": model.value(n), "value_greedy": v_greedy,
            "value_filled": model.value(f), "greedy": dict(sorted(g.items())), "local_search": log}
