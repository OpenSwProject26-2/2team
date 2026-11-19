"""구 단독(per_gu) 차량 배분(설계 v1 5절).

충족 근사 v3 (시간 h = 6, 7, 8, 9시를 따로 계산해 더한다)
    served_k,h = κ · a_h · d_k,h · min(1, W · Σ_{r∋k} n_r / C_r)
    노선별 승객 = served_k,h × (n_r/C_r) / Σ_{r∋k} n_r/C_r   (배차 비율로 나눔)
    a_h = 운행 시작 시각(`service_start_s`) 뒤에 남는 그 시간의 몫(07:00 시작이면 6시 0, 나머지 1).
    수송 한도(`cap_mode`)
    - "segment"(기본, 3단계): 노선의 구간(정류장 i → i+1)마다 재차 인원 L_s,h = Σ_{i_k ≤ s < j_k} 승객_k,h를 구하고
      시간당 좌석 n_r × 3600 / C_r × 좌석 = cap_h와 비교한다. 승객 k는 지나는 구간 중 가장 붐비는 구간의 비율
      min(1, cap_h / max_{s∈[i_k, j_k)} L_s,h)만큼 남긴다. 중간에 내린 자리를 뒤 승객이 다시 쓰는 것(좌석 재사용)이 들어간다.
      (i_k, j_k) = 그 노선에서 승객이 탈·내릴 정차 순번(`spans`, 평가 규칙의 재배정 짝). spans가 없는 노선은 모든 승객이
      노선 처음부터 끝까지 탄다고 보아 아래 "route"와 같다.
    - "route"(예전, 1·2단계): 노선 승객 합이 cap_h를 넘는 몫을 잘라낸다(좌석을 한 번만 쓴다).

개설: 새 노선은 n0 = ⌈C/W⌉대를 한 번에 넣는다. 비교 기준은 1대당 이득.
탐욕: 지연 평가(CELF) 우선순위 큐.
국소 탐색(v1): 매 회차 아래 이동을 모두 평가해 가장 좋은 개선 하나를 받는다. 개선이 없으면
가치 낮은 노선 닫고 재배분(close_reopt)을 시도하고, 그것도 없으면 멈춘다.
- shift1·shift2·shift3: 열린 노선 a에서 b로 k대(a는 1대 이상 남김)
- swap: 열린 노선 a를 닫고 안 열린 후보 c를 n0[c]대로 연다. 대수가 모자라면 다른 노선에서
  1대씩(뺄 때 손실이 가장 작은 노선부터, 1대는 남김) 가져오고, 남으면 fill로 넣는다.
초기해 여러 개(탐욕 + fill, 수요 상위 노선부터 n0씩, 호출자가 준 해 — 예: 피더안 배분)에서 각각 국소 탐색을
돌리고 근사가 가장 큰 해를 고른다(동률이면 앞 초기해). 국소 탐색은 근사를 줄이지 않으므로 결과는 모든 초기해 이상이다.
차량 합은 정확히 가용 대수. 남는 대수는 1대씩 이득이 가장 큰 열린 노선에 넣는다.
난수를 쓰지 않는다. 동률은 노선 ID 순.

형평 하한(설계 2.2·6.2, 3단계, `Model(equity=EquityFloor)`)
- 탈 수 있는 수요 비율(전체, 역 800m 밖) = 열린 노선들이 덮는 OD 키(평가 규칙)의 구 동네 정류장 수요 ÷ 분모. 두 값이 모두
  하한(기본: 같은 규칙에서 잰 지자체안 값) 이상이어야 한다.
- 여러 조합 하한(선택, `EquityFloor.extra`): 다른 평가 조합에서 그 조합의 덮는 키로 잰 두 값도 각 조합의 하한 이상.
  부족 인원을 모두 위반량에 더한다. 기본은 끔(기본 조합 하나).
- 노선 대당 하한 `route_min_per_vehicle`: 열린 노선마다 근사 승객 ÷ 대수 ≥ 하한.
- 방식: 사전식 목적 (위반량 최소, 그다음 근사 최대). 위반량 = 형평 부족 인원(전체 + 역 800m 밖) + 노선별 대당 하한 부족 인원.
  모든 탐색(탐욕 뒤 국소 탐색, 초기해 비교)이 이 순서로 비교한다. 그래서 하한을 만족하는 해가 하나라도 찾아지면 그 뒤로는
  하한을 깨는 이동을 받지 않고(제약 위반 이동 거부), 만족 해들 사이에서 근사를 키운다. 하한을 못 맞추면 위반량이 가장 작은
  해를 돌려주고 `feasible=False`·부족분·경고를 남긴다.
- 초기해에 "형평 탐욕"(위반량을 1대당 가장 많이 줄이는 노선부터 n0대로 열고, 위반이 0이 되면 근사 탐욕으로 나머지)을 더한다.
  이 노선들은 고정하지 않는다(예전 형평 먼저와 다름). 국소 탐색이 바꿀 수 있다.

규칙(AllocParams)
- respect_n0(기본 True): 열린 노선은 n0 = ⌈C/W⌉대 아래로 줄이지 않는다(배차 간격이 대기 한도를 넘지 않게).
  이전 버전은 옮기기·교체에서 1대까지 줄일 수 있어, 배차가 대기 한도를 넘는 노선이 나왔다.
- cover_groups(기본 False): 그룹(역)마다 노선을 하나씩 먼저 연다. 그룹 무게(역 수요) 큰 순으로 예산이 되는 만큼.
  국소 탐색은 이렇게 덮은 그룹을 비우는 이동을 받지 않는다.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass

EPS = 1e-9
VIOL_EPS = 1e-6                                     # 위반 인원 비교 허용 오차


SERVICE_T0 = 6 * 3600                               # 근사 시간대 06~10시(data.HOURS)
CAP_MODES = ("segment", "route")


@dataclass(frozen=True)
class AllocParams:
    wait_s: int = 1200
    seat: int = 45
    kappa: float = 1.0
    respect_n0: bool = True
    cover_groups: bool = False
    cap_mode: str = "segment"
    service_start_s: int | None = None                 # None = 06:00(시간대 처음부터)
    route_min_per_vehicle: float = 0.0                 # 0 = 끔

    def __post_init__(self):
        if self.cap_mode not in CAP_MODES:
            raise ValueError(f"cap_mode는 {CAP_MODES} 중 하나: {self.cap_mode}")

    def hour_weights(self) -> tuple[float, float, float, float]:
        """시간 h = 6..9시마다 운행 시작 시각 뒤에 남는 몫(0~1)."""
        st = SERVICE_T0 if self.service_start_s is None else int(self.service_start_s)
        return tuple(min(1.0, max(0.0, (SERVICE_T0 + (h + 1) * 3600 - st) / 3600)) for h in range(4))


@dataclass(frozen=True)
class EquityFloor:
    """형평 하한: 키별 무게(그 구 동네 정류장 수요, 역 800m 밖 수요)와 분모·하한(비율).

    extra: 다른 평가 조합에서도 하한을 걸 때 (이름, {route_id: 그 조합에서 탈 수 있는 키들}, 하한 전체, 하한 역 밖)들.
    비면(기본) 배분 모델이 쓰는 조합(routes의 덮는 키) 하나에만 건다.
    """
    w_total: dict
    w_far: dict
    den_total: float
    den_far: float
    floor_total: float
    floor_far: float
    extra: tuple = ()

    @classmethod
    def from_index(cls, eq, floor_total: float, floor_far: float, extra=()) -> "EquityFloor":
        """equity.EquityIndex와 하한 두 값(과 다른 조합 하한 extra)으로 만든다."""
        return cls(dict(eq.w_total), dict(eq.w_far), eq.den_total, eq.den_far, float(floor_total), float(floor_far),
                   tuple(extra))


class Model:
    """후보 노선(route_id, 주기, 덮는 OD 키[, 승차·하차 순번])과 시간별 OD 수요로 근사 충족을 계산한다.

    routes의 원소: (route_id, 주기 s, 덮는 키들) 또는 (route_id, 주기 s, 덮는 키들, spans).
    spans = 키마다 (탈 순번 i, 내릴 순번 j), i < j. 구간별 정원(cap_mode="segment")에 쓴다.
    equity: 형평 하한(EquityFloor). 없으면 하한 없음(위반량은 노선 대당 하한만).
    """

    def __init__(self, routes: list[tuple], od_hourly: dict[tuple[str, str], list[float]], p: AllocParams = AllocParams(),
                 groups: dict[str, str] | None = None, group_weight: dict[str, float] | None = None,
                 equity: EquityFloor | None = None):
        self.p = p
        self.groups = dict(groups or {})            # 노선 → 그룹(역)
        self.group_weight = dict(group_weight or {})
        self.required: set[str] = set()             # 덮어야 하는 그룹(cover_groups일 때 allocate가 정한다)
        self.fixed: dict[str, int] = {}             # 반드시 열어 둘 노선 → 최소 대수(예전 형평 먼저 재현용)
        self.ids = sorted(r[0] for r in routes)
        self.cycle = {r[0]: r[1] for r in routes}
        keys = sorted({k for r in routes for k in r[2]})
        self.key_index = {k: i for i, k in enumerate(keys)}
        self.keys = keys
        hw = p.hour_weights()
        self.hour_w = hw
        self.d = [tuple(x * a for x, a in zip(od_hourly[k], hw)) for k in keys]
        self.cover = {r[0]: tuple(self.key_index[k] for k in r[2]) for r in routes}
        self.spans: dict[str, tuple[tuple[int, int], ...] | None] = {}
        for r in routes:
            sp = tuple(tuple(x) for x in r[3]) if len(r) > 3 and r[3] is not None else None
            if sp is not None and (len(sp) != len(r[2]) or any(not (0 <= a < b) for a, b in sp)):
                raise ValueError(f"{r[0]}: spans는 덮는 키마다 (i, j), 0 ≤ i < j")
            self.spans[r[0]] = sp
        self.n0 = {r[0]: max(1, math.ceil(r[1] / p.wait_s)) for r in routes}
        self.rank = {r: i for i, r in enumerate(self.ids)}
        self.demand_of = {r: sum(sum(self.d[ki]) for ki in self.cover[r]) for r in self.ids}
        by_key: dict[int, set[str]] = {}
        for r in self.ids:
            for ki in self.cover[r]:
                by_key.setdefault(ki, set()).add(r)
        self.overlap = {r: tuple(sorted({x for ki in self.cover[r] for x in by_key[ki]} - {r})) for r in self.ids}
        self.equity = equity
        if equity is not None:
            self.eq_t = [equity.w_total.get(k, 0.0) for k in keys]
            self.eq_f = [equity.w_far.get(k, 0.0) for k in keys]
            self.eq_need_t = equity.floor_total * equity.den_total
            self.eq_need_f = equity.floor_far * equity.den_far
            self.eq_extra = [(lab, {r: frozenset(ks) for r, ks in cov.items()}, ft * equity.den_total,
                              ff * equity.den_far) for lab, cov, ft, ff in equity.extra]
        self._cache: dict[tuple, tuple] = {}
        self._rcache: dict[tuple, tuple] = {}

    # -- 노선 하나(그 노선과 키를 나누는 열린 노선의 대수가 같으면 결과가 같다 → 캐시)
    def _route(self, r: str, n: dict[str, int]) -> tuple[list[float], float]:
        ck = (r, n[r], tuple((x, n[x]) for x in self.overlap[r] if n.get(x, 0) > 0))
        got = self._rcache.get(ck)
        if got is not None:
            return got
        W, seat, kappa = self.p.wait_s, self.p.seat, self.p.kappa
        fr = n[r] / self.cycle[r]
        cap = fr * 3600 * seat
        cov = self.cover[r]
        # 키별 배차 합 f_k = Σ_{x∋k, 열림} n_x / C_x
        loads = []                                   # 키마다 [h6..h9] 승객(절단 전)
        for ki in cov:
            fk = fr
            for x in self.overlap[r]:
                nx = n.get(x, 0)
                if nx > 0 and ki in self._cover_set(x):
                    fk += nx / self.cycle[x]
            share = kappa * min(1.0, W * fk) * fr / fk
            dk = self.d[ki]
            loads.append([dk[h] * share for h in range(4)])
        raw = [sum(x[h] for x in loads) for h in range(4)]
        sp = self.spans[r] if self.p.cap_mode == "segment" else None
        if sp is None or not sp:
            kept = [min(x, cap) for x in raw]
        else:
            S = max(b for _, b in sp)
            kept = [0.0, 0.0, 0.0, 0.0]
            for h in range(4):
                diff = [0.0] * (S + 1)
                for (a, b), x in zip(sp, loads):
                    diff[a] += x[h]
                    diff[b] -= x[h]
                g, acc = [], 0.0
                for sidx in range(S):
                    acc += diff[sidx]
                    g.append(1.0 if acc <= cap + EPS else cap / acc)
                kept[h] = sum(x[h] * min(g[a:b]) for (a, b), x in zip(sp, loads))
        got = (kept, sum(raw) - sum(kept))
        self._rcache[ck] = got
        return got

    def _cover_set(self, r: str) -> frozenset:
        cs = getattr(self, "_cs", None)
        if cs is None:
            cs = self._cs = {}
        got = cs.get(r)
        if got is None:
            got = cs[r] = frozenset(self.cover[r])
        return got

    def detail(self, n: dict[str, int]) -> dict:
        """{'total', 'by_route': {r: [h6..h9 승객]}, 'demand_covered', 'cap_cut'}."""
        m = {r: k for r, k in n.items() if k > 0}
        by_route: dict[str, list[float]] = {}
        total = cut = 0.0
        keys: set[int] = set()
        for r in sorted(m):
            kept, c = self._route(r, m)
            by_route[r] = kept
            total += sum(kept)
            cut += c
            keys |= self._cover_set(r)
        covered = sum(sum(self.d[ki]) for ki in keys)
        return {"total": total, "by_route": by_route, "demand_covered": covered, "cap_cut": cut}

    # -- 하한(형평·노선 대당)
    def equity_of(self, n: dict[str, int]) -> dict | None:
        """열린 노선이 덮는 키로 잰 형평 두 값과 부족 인원. equity가 없으면 None."""
        if self.equity is None:
            return None
        keys: set[int] = set()
        for r, k in n.items():
            if k > 0:
                keys |= self._cover_set(r)
        rt = sum(self.eq_t[ki] for ki in keys)
        rf = sum(self.eq_f[ki] for ki in keys)
        e = self.equity
        out = {"total": rt / e.den_total if e.den_total else 0.0, "far": rf / e.den_far if e.den_far else 0.0,
               "short_total": max(0.0, self.eq_need_t - rt), "short_far": max(0.0, self.eq_need_f - rf)}
        out["short"] = out["short_total"] + out["short_far"]
        if self.eq_extra:
            out["extra"] = {}
            for lab, cov, nt, nf in self.eq_extra:
                ks = set().union(*(cov.get(r, ()) for r, k in n.items() if k > 0))
                xt = sum(e.w_total.get(k, 0.0) for k in ks)
                xf = sum(e.w_far.get(k, 0.0) for k in ks)
                d = {"total": xt / e.den_total if e.den_total else 0.0, "far": xf / e.den_far if e.den_far else 0.0,
                     "short_total": max(0.0, nt - xt), "short_far": max(0.0, nf - xf)}
                out["extra"][lab] = d
                out["short"] += d["short_total"] + d["short_far"]
        return out

    def route_shortfall(self, n: dict[str, int], by_route: dict | None = None) -> dict[str, float]:
        """노선 대당 하한을 못 넘는 열린 노선 → 부족 인원(하한 × 대수 − 근사 승객)."""
        lo = self.p.route_min_per_vehicle
        if lo <= 0:
            return {}
        by_route = by_route if by_route is not None else self.detail(n)["by_route"]
        out = {}
        for r, k in n.items():
            if k > 0:
                gap = lo * k - sum(by_route[r])
                if gap > 1e-6:
                    out[r] = gap
        return out

    def route_ok(self, n: dict[str, int], r: str) -> bool:
        """열린 노선 r이 대당 하한을 넘는가(하한이 없으면 늘 True)."""
        lo = self.p.route_min_per_vehicle
        if lo <= 0 or n.get(r, 0) <= 0:
            return True
        m = {x: k for x, k in n.items() if k > 0}
        return sum(self._route(r, m)[0]) >= lo * m[r] - 1e-6

    def score(self, n: dict[str, int]) -> tuple[float, float]:
        """(위반 인원, 근사). 위반이 작을수록, 같으면 근사가 클수록 좋다."""
        key = tuple(sorted((r, k) for r, k in n.items() if k > 0))
        got = self._cache.get(key)
        if got is None:
            det = self.detail(dict(key))
            viol = 0.0
            eq = self.equity_of(dict(key))
            if eq is not None:
                viol += eq["short"]
            viol += sum(self.route_shortfall(dict(key), det["by_route"]).values())
            got = self._cache[key] = (viol, det["total"])
        return got

    def better(self, a: tuple[float, float], b: tuple[float, float] | None) -> bool:
        """a가 b보다 좋은가(사전식: 위반 먼저, 그다음 근사)."""
        if b is None:
            return True
        if a[0] < b[0] - VIOL_EPS:
            return True
        return abs(a[0] - b[0]) <= VIOL_EPS and a[1] > b[1] + EPS

    def valid(self, n: dict[str, int]) -> bool:
        if self.p.respect_n0 and any(k < self.n0[r] for r, k in n.items() if k > 0):
            return False
        if self.required and not self.required <= {self.groups.get(r) for r, k in n.items() if k > 0}:
            return False
        if any(n.get(r, 0) < k for r, k in self.fixed.items()):
            return False
        return True

    def value(self, n: dict[str, int]) -> float:
        return self.score(n)[1]


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

    def gain(r: str, k: int) -> float | None:
        m = _with(n, r, k)
        if not model.route_ok(m, r):
            return None                                # 노선 대당 하한을 못 넘는 움직임은 하지 않는다
        return (model.value(m) - base) / k

    for r in model.ids:
        k = 1 if r in n else model.n0[r]
        if used + k <= budget:
            g0 = gain(r, k)
            if g0 is not None:
                heapq.heappush(heap, (-g0, r, "add" if r in n else "open", k, it))
    while heap:
        neg, r, kind, k, stamp = heapq.heappop(heap)
        if used + k > budget:
            continue                                   # 예산은 줄기만 하므로 영영 못 쓴다
        if (kind == "open") == (r in n):
            continue                                   # 이미 열렸거나 닫힌 노선의 낡은 움직임
        if stamp != it:
            g1 = gain(r, k)
            if g1 is not None:
                heapq.heappush(heap, (-g1, r, kind, k, it))
            continue
        if -neg <= EPS:
            break
        n = _with(n, r, k)
        used += k
        base = model.value(n)
        it += 1
        if used + 1 <= budget:
            g2 = gain(r, 1)
            if g2 is not None:
                heapq.heappush(heap, (-g2, r, "add", 1, it))
    return n


def fill(model: Model, budget: int, n: dict[str, int]) -> dict[str, int]:
    """남는 대수를 1대씩 이득이 가장 큰 열린 노선에 넣는다(이득 0이어도 넣는다: 변환기 S5)."""
    n = dict(n)
    while sum(n.values()) < budget:
        left = budget - sum(n.values())
        if n:
            base = model.value(n)
            r = max(sorted(n), key=lambda x: (model.route_ok(_with(n, x, 1), x), model.value(_with(n, x, 1)) - base,
                                              -model.rank[x]))
            n = _with(n, r, 1)
        else:                                          # 열린 노선이 없으면 남은 대수로 가장 좋은 노선 하나를 연다
            r = max(model.ids, key=lambda x: (model.value({x: min(left, model.n0[x])}), -model.rank[x]))
            n = {r: min(left, model.n0[r])}
    return n


MOVES = ("shift1", "shift2", "shift3", "swap", "close_reopt")


def cover_seed(model: Model, budget: int) -> tuple[dict[str, int], set[str]]:
    """그룹(역)마다 1대당 이득이 가장 큰 노선 하나를 n0대로 연다. 그룹 무게 큰 순, 예산이 되는 만큼."""
    n: dict[str, int] = {}
    used = 0
    covered: set[str] = set()
    by_group: dict[str, list[str]] = {}
    for r in model.ids:
        g = model.groups.get(r)
        if g is not None:
            by_group.setdefault(g, []).append(r)
    for g in sorted(by_group, key=lambda x: (-model.group_weight.get(x, 0.0), x)):
        base = model.value(n)
        cands = [r for r in by_group[g] if used + model.n0[r] <= budget]
        if not cands:
            continue
        r = max(cands, key=lambda x: ((model.value(_with(n, x, model.n0[x])) - base) / model.n0[x], -model.rank[x]))
        if model.value(_with(n, r, model.n0[r])) - base <= EPS:
            continue                                   # 이 그룹 노선이 아무도 못 태우면 억지로 열지 않는다
        n = _with(n, r, model.n0[r])
        used += model.n0[r]
        covered.add(g)
    return n, covered


def top_demand_start(model: Model, budget: int, start: dict[str, int] | None = None) -> dict[str, int]:
    """덮는 수요가 큰 노선부터 n0대씩 연다(예산을 넘는 노선은 건너뜀). 남는 대수는 fill."""
    n: dict[str, int] = dict(start or {})
    used = sum(n.values())
    for r in sorted(model.ids, key=lambda x: (-model.demand_of[x], x)):
        if r in n:
            continue
        k = model.n0[r]
        if used + k <= budget:
            n[r] = k
            used += k
        if used == budget:
            break
    return fill(model, budget, n)


def equity_seed(model: Model, budget: int, start: dict[str, int] | None = None) -> dict[str, int]:
    """형평 탐욕: 형평 부족이 0이 될 때까지 (줄이는 부족 인원 + 근사 이득) ÷ n0가 가장 큰 노선을 n0대로 연다.

    부족을 줄이지 못하는 노선, 열었을 때 근사 대당 하한을 못 넘는 노선은 열지 않는다. 고정하지 않는 초기해다.
    """
    n = dict(start or {})
    if model.equity is None:
        return n
    used = sum(n.values())
    lo = model.p.route_min_per_vehicle
    while True:
        eq = model.equity_of(n)
        short = eq["short"]
        if short <= VIOL_EPS:
            break
        base = model.value(n)
        best = None
        for r in model.ids:
            k = model.n0[r]
            if r in n or used + k > budget:
                continue
            m = _with(n, r, k)
            e2 = model.equity_of(m)
            red = short - e2["short"]
            if red <= VIOL_EPS:
                continue
            if lo > 0 and sum(model.detail(m)["by_route"][r]) < lo * k - 1e-6:
                continue
            key = ((red + model.value(m) - base) / k, -model.rank[r])
            if best is None or key > best[0]:
                best = (key, m)
        if best is None:
            break
        n = best[1]
        used = sum(n.values())
    return n


def swap(model: Model, budget: int, n: dict[str, int], a: str, c: str) -> dict[str, int] | None:
    """노선 교체: 열린 a를 닫고 안 열린 c를 n0[c]대로 연다. 불가능하면 None."""
    if a not in n or c in n:
        return None
    m = {x: y for x, y in n.items() if x != a}
    m[c] = model.n0[c]
    while sum(m.values()) > budget:
        floor = (lambda y: model.n0[y]) if model.p.respect_n0 else (lambda y: 1)
        donors = [x for x in sorted(m) if x != c and m[x] > floor(x)]
        if not donors:
            return None
        base = model.value(m)
        x = max(donors, key=lambda y: (model.value(_with(m, y, -1)) - base, -model.rank[y]))
        m = _with(m, x, -1)
    return fill(model, budget, m)


def _new_stats() -> dict[str, dict[str, int]]:
    return {k: {"tries": 0, "accepts": 0} for k in MOVES}


def local_search(model: Model, budget: int, n: dict[str, int], max_rounds: int = 1000,
                 stats: dict[str, dict[str, int]] | None = None) -> tuple[dict[str, int], list[dict]]:
    """회차마다 shift1~3·swap 중 최선 개선 하나 → (없으면) close_reopt. 더 나아지지 않을 때까지.

    stats(이동 종류별 시도·수락 수)를 받으면 거기에 더한다. 기록(받아들인 이동 목록)을 함께 돌려준다.
    """
    st = stats if stats is not None else _new_stats()
    cur = model.score(n)
    log: list[dict] = []
    for _ in range(max_rounds):
        best = None

        def consider(m: dict[str, int] | None, info: dict) -> None:
            nonlocal best
            st[info["move"]]["tries"] += 1
            if m is None or not model.valid(m):
                return
            v = model.score(m)
            if model.better(v, cur) and (best is None or model.better(v, best[0])):
                best = (v, m, info)

        for k in (1, 2, 3):
            for a in sorted(n):
                if n[a] - k < (model.n0[a] if model.p.respect_n0 else 1):
                    continue
                for b in sorted(n):
                    if a != b:
                        consider(_with(_with(n, a, -k), b, k), {"move": f"shift{k}", "from": a, "to": b})
        closed = [c for c in model.ids if c not in n]
        for a in sorted(n):
            for c in closed:
                consider(swap(model, budget, n, a, c), {"move": "swap", "closed": a, "opened": c})
        if best is None:
            order = sorted(n, key=lambda r: (cur[1] - model.value({x: y for x, y in n.items() if x != r}), r))
            for r in order:
                rest = {x: y for x, y in n.items() if x != r}
                consider(fill(model, budget, greedy(model, budget, rest)), {"move": "close_reopt", "closed": r})
                if best is not None:
                    break
        if best is None:
            break
        st[best[2]["move"]]["accepts"] += 1
        log.append(dict(best[2], gain=round(best[0][1] - cur[1], 6), violation=round(best[0][0], 3)))
        cur, n = best[0], best[1]
    return n, log


def _check(n: dict[str, int], budget: int, model: Model, name: str) -> None:
    if sum(n.values()) != budget or any(v < 1 for v in n.values()) or any(r not in model.cover for r in n):
        raise AssertionError(f"{name}: vehicle sum {sum(n.values())} != {budget} or bad route")


def allocate(model: Model, budget: int, starts: dict[str, dict[str, int]] | None = None) -> dict:
    """초기해 여러 개 → 각각 국소 탐색 → 근사 최대(동률이면 앞 초기해).

    starts: 추가 초기해(이름 → 배분). 예산과 차량 합이 같고 이 모델의 노선만 써야 한다.
    """
    seed: dict[str, int] = dict(model.fixed)          # 고정 노선(형평 먼저)은 처음부터 열고 줄이지 않는다
    if sum(seed.values()) > budget:
        raise ValueError(f"고정 노선 대수 {sum(seed.values())}가 예산 {budget}보다 많다")
    model.required = set()
    if model.p.cover_groups and model.groups:
        cs, model.required = cover_seed(model, budget - sum(seed.values()))
        for r, k in cs.items():
            seed[r] = max(seed.get(r, 0), k)
    g = greedy(model, budget, seed)
    v_greedy = model.value(g)
    f = fill(model, budget, g)
    inits = [("greedy", f), ("top_demand", top_demand_start(model, budget, seed))]
    if model.equity is not None:
        es = equity_seed(model, budget, seed)
        inits.append(("equity_greedy", fill(model, budget, greedy(model, budget, es))))
    skipped = []
    for name, st0 in sorted((starts or {}).items()):
        if model.valid(st0):
            inits.append((name, st0))
        else:
            skipped.append(name)                       # 규칙(n0·역 덮기)을 어기는 초기해는 쓰지 않는다
    total_stats = _new_stats()
    runs: dict[str, dict] = {}
    chosen = None
    for name, n0 in inits:
        _check(n0, budget, model, f"start {name}")
        st = _new_stats()
        n, log = local_search(model, budget, dict(n0), stats=st)
        _check(n, budget, model, f"local search from {name}")
        sc = model.score(n)
        runs[name] = {"start": dict(sorted(n0.items())), "initial": model.value(n0), "final": sc[1],
                      "initial_violation": round(model.score(n0)[0], 3), "final_violation": round(sc[0], 3),
                      "n": dict(sorted(n.items())), "moves": log, "stats": st}
        for k in MOVES:
            for t in ("tries", "accepts"):
                total_stats[k][t] += st[k][t]
        if chosen is None or model.better(sc, model.score(runs[chosen]["n"])):
            chosen = name
    n = runs[chosen]["n"]
    viol, val = model.score(n)
    det = model.detail(n)
    eq = model.equity_of(n)
    rs = model.route_shortfall(n, det["by_route"])
    warnings = []
    if eq is not None and eq["short_total"] + eq["short_far"] > VIOL_EPS:
        e = model.equity
        warnings.append(f"형평 하한 미달: 전체 {eq['total']:.4f} (하한 {e.floor_total:.4f}, 부족 {eq['short_total']:.1f}명), "
                        f"역 800m 밖 {eq['far']:.4f} (하한 {e.floor_far:.4f}, 부족 {eq['short_far']:.1f}명)")
    for lab, d in (eq or {}).get("extra", {}).items():
        if d["short_total"] + d["short_far"] > VIOL_EPS:
            warnings.append(f"형평 하한 미달({lab}): 전체 {d['total']:.4f} 부족 {d['short_total']:.1f}명, "
                            f"역 800m 밖 {d['far']:.4f} 부족 {d['short_far']:.1f}명")
    if rs:
        warnings.append("노선 대당 하한 미달: " + ", ".join(
            f"{r}({n[r]}대, 근사 {sum(det['by_route'][r]):.1f}명, 부족 {g:.1f}명)" for r, g in sorted(rs.items())))
    for w in warnings:
        _warn(w)
    return {"n": n, "value": val, "chosen": chosen, "inits": runs, "move_stats": total_stats,
            "value_greedy": v_greedy, "value_filled": model.value(f), "greedy": dict(sorted(g.items())),
            "covered_groups": sorted(model.required), "skipped_starts": skipped,
            "local_search": runs[chosen]["moves"],
            "violation": round(viol, 3), "feasible": viol <= VIOL_EPS,
            "equity": None if eq is None else {
                "total": round(eq["total"], 4), "far": round(eq["far"], 4),
                "floor_total": round(model.equity.floor_total, 4), "floor_far": round(model.equity.floor_far, 4),
                "short_total": round(eq["short_total"], 1), "short_far": round(eq["short_far"], 1),
                **({"extra": {lab: {k: round(v, 4 if k in ("total", "far") else 1) for k, v in d.items()}
                              for lab, d in eq["extra"].items()}} if eq.get("extra") else {})},
            "route_min_per_vehicle": model.p.route_min_per_vehicle,
            "per_vehicle": {r: round(sum(det["by_route"][r]) / n[r], 1) for r in sorted(n)},
            "route_shortfall": {r: round(g, 1) for r, g in sorted(rs.items())},
            "cap_mode": model.p.cap_mode, "service_start_s": model.p.service_start_s,
            "hour_weights": list(model.hour_w), "warnings": warnings}


def _warn(msg: str) -> None:
    import warnings

    warnings.warn(msg, RuntimeWarning, stacklevel=3)
