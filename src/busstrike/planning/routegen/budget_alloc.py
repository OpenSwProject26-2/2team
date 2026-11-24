"""배차 예산 후보의 배분(새 알고리즘 1·7단계, 작업 2026-11-25). 배분기(`allocate.py`)는 고치지 않고 그 위에서 쓴다.

- 대수 고정(1단계, `FixedNModel`): 배차 예산 후보는 노선 대수 n을 기록한다(순환 ≤ n × H). 그 노선을 열면 대수는 꼭 n이다.
  - 개설 대수 n0를 n으로 둔다(탐욕은 n대로 연다). 열린 노선에 n을 넘게 더하는 움직임은 `route_ok`가 막는다.
  - 대수가 n과 다른 열린 노선마다 위반량에 FIX_PENALTY(1e9)를 더한다. 그래서 사전식 비교(위반 먼저)가 그런 해를 받지 않고,
    남는 대수를 채우는 `fill`이 만든 위반 해는 국소 탐색(교체·닫고 다시 채우기)이 고친다. 고치지 못하면 `feasible=False`로 남는다.
- 상위 k개(7단계, `top_k`): 근사 1등 조합을 구한 뒤 그 노선 조합을 금지 목록(`Model(banned=…)`)에 넣고 다시 풀기를 k번.
  금지된 조합밖에 못 찾으면(더 다른 조합이 없으면) 멈춘다.
"""

from __future__ import annotations

from .allocate import Model, allocate

FIX_PENALTY = 1e9


class FixedNModel(Model):
    def __init__(self, routes, od_hourly, p, groups=None, group_weight=None, equity=None, must=None,
                 must_min_tph: float = 0.0, banned=(), fixed_n: dict[str, int] | None = None):
        super().__init__(routes, od_hourly, p, groups, group_weight, equity, must, must_min_tph, banned)
        self.fixed_n = {r: int(k) for r, k in sorted((fixed_n or {}).items()) if r in self.cycle and k > 0}
        for r, k in self.fixed_n.items():
            self.n0[r] = k

    def route_ok(self, n: dict[str, int], r: str) -> bool:
        if r in self.fixed_n and n.get(r, 0) > self.fixed_n[r]:
            return False
        return super().route_ok(n, r)

    def score(self, n: dict[str, int]) -> tuple[float, float]:
        viol, val = super().score(n)
        bad = sum(1 for r, k in n.items() if k > 0 and r in self.fixed_n and k != self.fixed_n[r])
        return viol + FIX_PENALTY * bad, val


def top_k(make_model, budget: int, k: int, starts: dict | None = None) -> list[dict]:
    """make_model(banned) → Model. 앞서 찾은 조합을 금지하며 k번 푼다. 반환: allocate 결과 목록(순서 = 찾은 순서)."""
    out: list[dict] = []
    banned: list[frozenset[str]] = []
    for _ in range(k):
        m = make_model(tuple(banned))
        a = allocate(m, budget, starts)
        sel = frozenset(r for r, v in a["n"].items() if v > 0)
        if sel in banned:
            break
        a["rank"] = len(out) + 1
        out.append(a)
        banned.append(sel)
    return out
