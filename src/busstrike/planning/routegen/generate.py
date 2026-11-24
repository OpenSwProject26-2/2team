"""후보 노선 생성(설계 v1 4절). 구 하나·결정론.

1. 허브: F_k = Σ_s r(s↔k)(access+egress, 4시간 합, 5일 평균) ≥ F_min = 2 × 좌석 × (240 ÷ C₀)
2. 권역 K_k: 허브 반경 R 안에서 r(s↔k) > 0인 정류장(중복 소속 허용)
3. 겹침 병합: O_ij = Σ_{s∈K_i∩K_j}(r(s↔i)+r(s↔j)) / min(F_i, F_j) ≥ τ → union-find.
   군집이 역 3개를 넘으면 그 군집만 τ를 τ_step씩 올려 다시 나눈다. 구 경계를 넘으면 cross_gu.
4. 섹터 분할 + 최선 삽입: 패턴·m·L_max마다 섹터별 seed = argmax v_s × dist(s, 중심),
   삽입 점수 = 증가 덮개 수요 ÷ 증가 편도 시간. 제약(편도·정차 수·우회율·S2) 위반 삽입은 버린다. 끝에 2-opt 1회.
5. 추가 후보: 허브·방향마다 수요 상위 seed의 최단경로 피더(삽입 휴리스틱의 비교 기준).
5b. (multi=True일 때) 여러 역 경유 피더: 수요가 여러 역으로 흩어진 정류장(가장 많이 가는 역 몫 < multi_spread)에서 출발해
   그 정류장 승객이 많이 가는 역(허브가 아니어도 됨) 2~multi_max_hubs곳의 access 지정 정류장을 차례로 들른다.
   경로는 망 최단경로, 편도 ≤ multi_l_max_min분, 우회율 ≤ multi_max_detour, U턴·중복 정차 금지. kind = "multi".
6. 패턴(가는 길): access(정류장들 → 역 access 정류장), egress(역 egress 정류장 → 정류장들),
   connect(역 i egress 정류장 → 정류장들 → 역 j access 정류장), multi(5b).
6b. 순환(`loop`, 3b단계 원칙: 셔틀은 무조건 순환 운행): 가는 길마다 돌아오는 길을 붙여 한 바퀴 정차열을 만든다
   (`loop.LoopBuilder`, 방식 demand = 수요에 맞춘 삽입, deadhead = 공차 복귀 경로를 영업으로). 고리 닫기 공차 ≤
   loop_closure_max_s, 방향마다 정차 ≤ loop_max_stops·편도 한도·우회율, 고리 닫는 이음 포함 U턴 금지.
   loop=None은 예전 결과 재현용(편도 + 공차 복귀)이다. 기본 경로(gu_params)는 demand.
7. 후보 처리: 덮개(노선 위 o가 d보다 앞인 OD), 주기(`cycle.route_cycle`), 중복 제거(Jaccard).
"""

from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass, field

from busstrike.sim.models import Scenario

from .cycle import route_cycle
from .data import Demand, Network, haversine_m
from .graph import Graph, Limits


@dataclass(frozen=True)
class Params:
    seat: int = 45
    c0_s: int = 2400                 # C₀ = 40분
    horizon_s: int = 4 * 3600        # 06~10시 240분
    radius_m: float = 3000.0         # R
    tau: float = 0.2
    tau_step: float = 0.05
    cluster_max: int = 3
    sectors: tuple[int, ...] = (2, 3, 4)
    l_max_min: tuple[int, ...] = (20, 30)
    max_stops: int | None = 8        # N_max. None = 정차 수를 검사하지 않음(편도 L_max·우회율만)
    max_detour: float = 1.6
    sp_top: int = 15
    jaccard: float = 0.8
    home_gu: str = "구로구"
    # "crow"(기본, 이전 결과 재현) 또는 "network"(권역·우회율·seed 거리를 모두 망 위 버스 노선 거리로)
    distance_basis: str = "crow"
    # 여러 역 경유 피더(5b). 기본은 끔(이전 결과 재현)
    multi: bool = False
    multi_top: int = 40               # 출발 정류장 수(수요 큰 순)
    multi_spread: float = 0.7         # 가장 많이 가는 역 몫이 이보다 작아야 "흩어진" 정류장
    multi_max_hubs: int = 3
    multi_l_max_min: int = 40
    multi_max_detour: float = 3.0
    # 허브 기준(하루 수요) 직접 지정. None = 2 × 좌석 × (240 ÷ C₀) = 540
    hub_min: float | None = None
    # 구 수요에 맞춘 허브 기준: 역 수요를 큰 순으로 더해 전체의 hub_share가 될 때까지의 역을 허브로(hub_min보다 우선).
    # hub_floor = 그래도 이보다 작은 역은 허브로 두지 않는다(하루 명)
    hub_share: float | None = None
    hub_floor: float = 30.0
    hub_min_count: int = 0            # 허브 최소 개수: 기준을 넘는 역이 이보다 적으면 수요 큰 역부터 채운다(파업 비율이 낮을 때)
    # 필수 정류장(설계 6.1c, 통합 2026-11-21). 비면 필수 정류장 후보를 만들지 않는다
    must_stops: tuple[str, ...] = ()
    must_insert_radius_m: float = 600.0   # 이 거리 안에 정류장이 있는 후보에만 끼워 본다
    must_feeder_stations: int = 3         # 전용 후보: 직선거리로 가까운 역 몇 곳까지 시도
    # 순환(6b). None = 편도 + 공차 복귀(예전 결과 재현용), "demand"(기본 경로) | "deadhead"(비교 기준) | "both"
    loop: str | None = None
    loop_closure_max_s: int = 300     # 고리 닫기 공차 상한(초). 지자체안 최대 약 4분
    loop_max_stops: int | None = 12   # 방향마다 정차 상한(역 지정 정류장 제외). 한 바퀴 최대 2배
    loop_max_stops_total: int | None = None   # 한 바퀴 전체 정차 상한(역 지정 정류장 제외). None = 검사하지 않음
    # 돌아오는 길 우회율 상한(꺾는 정류장 → 첫 정류장, 고리 닫기 포함). None = 가는 길과 같은 값(max_detour·multi_max_detour)
    loop_ret_detour: float | None = None
    # 순환 시작점 최적화(개선 2026-11-22). True면 순환 후보마다 같은 고리를 덮는 수요가 가장 큰 시작점으로 돌린다(loop.best_rotation).
    # 기본 False(예전 결과 재현)
    loop_rotate: bool = False
    # 닫힌 순회(tour.py, 작업 2026-11-24). None(기본) = 만들지 않음(예전 결과 그대로). 켜면 ("sector", "feeder", "pair") 중 일부.
    # 순환 시간 ≤ 2 × L(l_max_min 각각), 정차 ≤ tour_max_stops, tour_detour(None = 우회율 검사 안 함, 기록만),
    # ③-T 역 쌍: 같은 군집이거나 망 최단 시간 ≤ tour_pair_max_s, 셔틀 수요 합 상위 tour_pair_top쌍
    tour: tuple[str, ...] | None = None
    tour_max_stops: int | None = 24
    tour_detour: float | None = None
    tour_pair_max_s: int = 900
    tour_pair_top: int = 10

    def __post_init__(self):
        if self.loop not in (None, "demand", "deadhead", "both"):
            raise ValueError(f"loop은 None, demand, deadhead, both 중 하나: {self.loop}")
        if self.tour is not None:
            from .tour import TOUR_KINDS
            bad = [x for x in self.tour if x not in TOUR_KINDS]
            if bad or not self.loop:
                raise ValueError(f"tour는 {TOUR_KINDS} 중 일부이고 순환(loop)이 켜져 있어야 한다: {self.tour}")

    @property
    def f_min(self) -> float:
        if self.hub_min is not None:
            return self.hub_min
        return 2 * self.seat * (self.horizon_s / self.c0_s)


@dataclass
class Candidate:
    route_id: str
    kind: str                         # access | egress | connect
    stops: tuple[str, ...]
    origin: str                       # generated | shortest_path_feeder | multi_station_feeder
    hubs: tuple[str, ...]             # 패턴의 역(access/egress: 1개, connect: i, j, multi: 들르는 순서)
    cluster: int | None
    made_by: tuple[str, ...]          # 이 정차열을 낸 (m, L_max) 조합 또는 "sp:seed"
    oneway_s: int = 0
    length_m: int = 0
    detour: float = 0.0
    n_stops: int = 0                  # 역 지정 정류장을 뺀 정차 수
    cycle_s: int = 0
    return_stops: tuple[str, ...] = ()
    cover: float = 0.0                # 덮는 수요(4시간 합, 5일 평균)
    cover_keys: tuple[tuple[str, str], ...] = ()
    cross_gu: bool = False
    # 순환(6b). loop = ""(편도, 예전) | "demand" | "deadhead". return_stops = 변환기 공차 경로(순환이면 고리 닫기)
    loop: str = ""
    loop_variant: str = ""            # multi 돌아오는 길: rev(경유 역 반대 순서) | direct
    turn: int = 0                     # 꺾는 정류장 순번(가는 길 끝 = 돌아오는 길 처음)
    closure_s: int = 0                # 고리 닫기 공차(초, 구간 06~09시 최댓값 합)
    out_oneway_s: int = 0
    ret_oneway_s: int = 0
    out_n_stops: int = 0              # 방향별 정차 수(역 지정 정류장 제외, 꺾는 정류장은 가는 길)
    ret_n_stops: int = 0
    must: tuple[str, ...] = ()        # 첫·끝이 아닌 정차로 지나는 필수 정류장(덮음)

    def record(self) -> dict:
        d = asdict(self)
        d["stops"] = list(self.stops)
        d["return_stops"] = list(self.return_stops)
        d["cover_keys"] = [list(k) for k in self.cover_keys]
        d["cover"] = round(self.cover, 4)
        d["detour"] = round(self.detour, 4)
        return d


# ---------------------------------------------------------------- 덮개

def covered_keys(stops: tuple[str, ...], od: dict) -> list[tuple[str, str]]:
    """노선이 덮는 OD 키: 노선 위에서 출발 정류장이 도착 정류장보다 앞.

    access 키 (s, 역 access 정류장)는 s가 그 정류장보다 앞일 때, egress 키 (역 egress 정류장, s)는
    반대일 때 덮는다. 시뮬레이터가 승객을 태우는 조건(같은 영업 leg에서 o 다음에 d)과 같다.
    """
    out = []
    n = len(stops)
    for a in range(n):
        for b in range(a + 1, n):
            k = (stops[a], stops[b])
            if k in od:
                out.append(k)
    return out


def cover_value(stops: tuple[str, ...], od_total: dict[tuple[str, str], float]) -> float:
    n = len(stops)
    v = 0.0
    for a in range(n):
        sa = stops[a]
        for b in range(a + 1, n):
            v += od_total.get((sa, stops[b]), 0.0)
    return v


# ---------------------------------------------------------------- 1~3 허브·권역·병합

def hub_threshold(demand: Demand, net: Network, p: Params) -> float:
    """허브 기준(하루 수요). hub_share가 있으면 구 수요에 맞춰 정하고, 없으면 f_min."""
    if p.hub_share is None:
        return p.f_min
    F = demand.station_total()
    vals = sorted((v for k, v in F.items() if k in net.stations
                   and (net.stations[k].access_stop or net.stations[k].egress_stop)), reverse=True)
    total, acc = sum(vals), 0.0
    for v in vals:
        acc += v
        if total and acc / total >= p.hub_share:
            return max(v, p.hub_floor)
    return max(vals[-1] if vals else p.f_min, p.hub_floor)


def hubs_of(demand: Demand, net: Network, p: Params) -> dict[str, float]:
    F = demand.station_total()
    th = hub_threshold(demand, net, p)
    ok = [k for k in sorted(F) if F[k] > 0 and k in net.stations
          and (net.stations[k].access_stop or net.stations[k].egress_stop)]
    hubs = {k: F[k] for k in ok if F[k] >= th}
    for k in sorted(ok, key=lambda x: (-F[x], x)):       # 허브 최소 개수(기본 0 = 예전 동작)
        if len(hubs) >= p.hub_min_count:
            break
        hubs.setdefault(k, F[k])
    return dict(sorted(hubs.items()))


def station_reach(graph: Graph, net: Network, k: str, s: str) -> float:
    """정류장 s와 역 k 사이 망 위 거리(m): min(s → 역 access 정류장, 역 egress 정류장 → s). 못 닿으면 inf."""
    st = net.stations[k]
    best = float("inf")
    if st.access_stop:
        best = min(best, graph.dist_to(st.access_stop).get(s, float("inf")))
    if st.egress_stop:
        best = min(best, graph.dist_from(st.egress_stop).get(s, float("inf")))
    return best


def catchments(demand: Demand, net: Network, hubs: dict[str, float], p: Params,
               graph: Graph | None = None) -> dict[str, dict[str, float]]:
    """허브 → {정류장: r(s↔k) 4시간 합}. distance_basis가 network면 반경 R을 망 위 버스 노선 거리로 잰다."""
    out: dict[str, dict[str, float]] = {k: {} for k in hubs}
    for (kind, s, k), v in demand.pair.items():
        if k not in hubs or s not in net.stops:
            continue
        d = station_reach(graph, net, k, s) if p.distance_basis == "network" else net.station_stop_dist_m(k, s)
        if d <= p.radius_m:
            out[k][s] = out[k].get(s, 0.0) + sum(v)
    return {k: {s: v for s, v in sorted(m.items()) if v > 0} for k, m in out.items()}


def overlap(i: str, j: str, K: dict[str, dict[str, float]], F: dict[str, float]) -> float:
    common = set(K[i]) & set(K[j])
    return sum(K[i][s] + K[j][s] for s in common) / min(F[i], F[j])


def _union_find(nodes: list[str], edges: list[tuple[str, str]]) -> list[list[str]]:
    parent = {n: n for n in nodes}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    groups: dict[str, list[str]] = {}
    for n in nodes:
        groups.setdefault(find(n), []).append(n)
    return sorted((sorted(g) for g in groups.values()), key=lambda g: g[0])


def merge_hubs(hubs: list[str], O: dict[tuple[str, str], float], tau: float, tau_step: float,
               cluster_max: int) -> list[dict]:
    """겹침 병합. 반환: [{hubs, tau}] — 군집이 cluster_max를 넘으면 그 군집만 τ를 올려 다시 나눈다."""
    def split(nodes: list[str], t: float) -> list[dict]:
        edges = [(a, b) for a in nodes for b in nodes if a < b and O[(a, b)] >= t - 1e-12]
        out = []
        for g in _union_find(nodes, edges):
            if len(g) > cluster_max:
                out.extend(split(g, round(t + tau_step, 10)))
            else:
                out.append({"hubs": g, "tau": t})
        return out
    return sorted(split(sorted(hubs), tau), key=lambda c: c["hubs"][0])


# ---------------------------------------------------------------- 4 섹터·삽입

@dataclass(frozen=True)
class Pattern:
    kind: str
    hubs: tuple[str, ...]
    start: str | None      # 고정 시작 정류장(egress·connect)
    end: str | None        # 고정 끝 정류장(access·connect)
    center: tuple[float, float]

    @property
    def station_stops(self) -> frozenset[str]:
        return frozenset(x for x in (self.start, self.end) if x)

    def anchors_with(self, seed: str) -> list[str]:
        return [x for x in (self.start, seed, self.end) if x]

    def movable(self, anchors: list[str]) -> range:
        lo = 1 if self.start else 0
        hi = len(anchors) - (1 if self.end else 0)
        return range(lo, hi)

    def insert_positions(self, anchors: list[str]) -> range:
        """새 anchor를 넣을 수 있는 자리(그 인덱스 앞에 넣음)."""
        lo = 1 if self.start else 0
        hi = len(anchors) - (1 if self.end else 0)
        return range(lo, hi + 1)


def stop_value(pattern: Pattern, s: str, demand: Demand) -> float:
    def r(kind: str, k: str) -> float:
        v = demand.pair.get((kind, s, k))
        return sum(v) if v else 0.0
    if pattern.kind == "access":
        return r("access", pattern.hubs[0])
    if pattern.kind == "egress":
        return r("egress", pattern.hubs[0])
    return r("egress", pattern.hubs[0]) + r("access", pattern.hubs[1])


def _bearing(center: tuple[float, float], lat: float, lon: float) -> float:
    dy = lat - center[0]
    dx = (lon - center[1]) * math.cos(math.radians(center[0]))
    return math.atan2(dy, dx) % (2 * math.pi)


def sectors(stops: list[str], values: dict[str, float], center: tuple[float, float], m: int,
            net: Network) -> list[list[str]]:
    """방위각 순으로 늘어놓고 가장 큰 빈 각에서 시작해, 수요가 거의 같은 연속 m조각으로 나눈다."""
    if not stops:
        return []
    ang = sorted(((_bearing(center, net.stops[s].lat, net.stops[s].lon), s) for s in stops))
    if len(ang) > 1:
        gaps = [((ang[(i + 1) % len(ang)][0] - ang[i][0]) % (2 * math.pi), i) for i in range(len(ang))]
        _, gi = max(gaps, key=lambda g: (g[0], -g[1]))
        ang = ang[gi + 1:] + ang[:gi + 1]
    total = sum(values[s] for _, s in ang)
    out: list[list[str]] = [[] for _ in range(m)]
    acc = 0.0
    for _, s in ang:
        idx = min(m - 1, int(acc / total * m)) if total > 0 else 0
        out[idx].append(s)
        acc += values[s]
    return [g for g in out if g]


class Builder:
    def __init__(self, net: Network, graph: Graph, demand: Demand, p: Params, reach=None):
        self.net, self.g, self.demand, self.p = net, graph, demand, p
        self.od_total = {k: sum(v) for k, v in demand.od.items()}
        # 평가 규칙(reach.Reach): 있으면 삽입·2-opt의 덮는 수요와 후보의 덮는 OD 키를 그 규칙(목적 역 대체·역 도착·걷기)으로
        # 센다. 없으면 예전처럼 정차열 위 (출발, 도착) 그대로만 센다.
        self.reach = reach
        # 거절 사유 집계: (단계, kind, 사유) → 횟수. 사유는 여러 개가 함께 붙을 수 있다(편도·정차 수·우회율).
        self.reasons: dict[tuple[str, str, str], int] = {}
        # 거절 사유 조합: (단계, kind, "l_max+detour" 같은 조합) → 횟수(단독 원인을 셀 때 쓴다)
        self.reason_sets: dict[tuple[str, str, str], int] = {}

    def cover_of(self, stops: tuple[str, ...]) -> float:
        if self.reach is not None:
            return self.reach.cover_value(stops, self.od_total)
        return cover_value(stops, self.od_total)

    def cover_keys_of(self, stops: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
        if self.reach is not None:
            return tuple(k for k in self.reach.cover_keys(stops) if k in self.demand.od)
        return tuple(covered_keys(stops, self.demand.od))

    def check(self, pattern: Pattern, anchors: list[str], lim: Limits) -> tuple[tuple[str, ...] | None, list[str]]:
        stops = self.g.expand(anchors)
        if stops is None:
            # cutoff 안 경로 없음 또는 이어 붙인 정차열에 같은 정류장(S2): 상한 없는 최단경로로 제약별 사유를 매긴다
            return None, self.g.diagnose(anchors, pattern.station_stops, lim)
        bad = self.g.violations(stops, pattern.station_stops, lim)
        return (None if bad else stops), bad

    def route_of(self, pattern: Pattern, anchors: list[str], lim: Limits, stage: str | None = None) -> tuple[str, ...] | None:
        stops, bad = self.check(pattern, anchors, lim)
        if stage is not None:
            for r in (bad + ["rejected"]) if bad else ["ok"]:   # rejected = 거절된 시도 수(사유는 겹쳐 셈)
                key = (stage, pattern.kind, r)
                self.reasons[key] = self.reasons.get(key, 0) + 1
            if bad:
                key = (stage, pattern.kind, "+".join(sorted(bad)))
                self.reason_sets[key] = self.reason_sets.get(key, 0) + 1
        return stops

    def insertion(self, pattern: Pattern, seed: str, pool: list[str], lim: Limits,
                  stage: str = "insertion") -> tuple[str, ...] | None:
        anchors = pattern.anchors_with(seed)
        stops = self.route_of(pattern, anchors, lim)
        if stops is None:
            return None
        cur_dem = self.cover_of(stops)
        cur_t = self.g.oneway_s(stops)
        while True:
            on = set(stops)
            best = None
            for s in pool:
                if s in on:
                    continue
                for pos in pattern.insert_positions(anchors):
                    trial = anchors[:pos] + [s] + anchors[pos:]
                    new = self.route_of(pattern, trial, lim, stage)
                    if new is None:
                        continue
                    d_dem = self.cover_of(new) - cur_dem
                    if d_dem <= 1e-9:
                        continue
                    d_t = self.g.oneway_s(new) - cur_t
                    score = math.inf if d_t <= 0 else d_dem / d_t
                    key = (score, d_dem, -pos, s)   # 동률: 증가 수요 큰 쪽, 앞자리, 정류장 ID
                    if best is None or key > best[0]:
                        best = (key, trial, new)
            if best is None:
                break
            _, anchors, stops = best
            cur_dem = self.cover_of(stops)
            cur_t = self.g.oneway_s(stops)
        return self.two_opt(pattern, anchors, stops, lim)

    def two_opt(self, pattern: Pattern, anchors: list[str], stops: tuple[str, ...], lim: Limits) -> tuple[str, ...]:
        """한 번 훑기: 움직일 수 있는 anchor 구간을 뒤집어 편도가 줄고 덮개가 줄지 않으면 받는다."""
        cur_t = self.g.oneway_s(stops)
        cur_dem = self.cover_of(stops)
        mv = list(pattern.movable(anchors))
        for x in range(len(mv)):
            for y in range(x + 1, len(mv)):
                i, j = mv[x], mv[y]
                trial = anchors[:i] + anchors[i:j + 1][::-1] + anchors[j + 1:]
                new = self.route_of(pattern, trial, lim)
                if new is None:
                    continue
                t = self.g.oneway_s(new)
                dem = self.cover_of(new)
                if t < cur_t and dem >= cur_dem - 1e-9:
                    anchors, stops, cur_t, cur_dem = trial, new, t, dem
        return stops


def seed_dist(graph: Graph, net: Network, pat: Pattern, s: str, p: Params) -> float:
    """seed 점수의 거리: crow = 패턴 중심과의 직선, network = 패턴 역 정류장과의 망 위 거리(못 닿으면 0)."""
    if p.distance_basis != "network":
        return haversine_m(pat.center[0], pat.center[1], net.stops[s].lat, net.stops[s].lon)
    ds = []
    if pat.end:
        ds.append(graph.dist_to(pat.end).get(s))
    if pat.start:
        ds.append(graph.dist_from(pat.start).get(s))
    ds = [d for d in ds if d is not None]
    return float(min(ds)) if ds else 0.0


def _patterns(cluster: list[str], net: Network) -> list[Pattern]:
    out = []
    for h in cluster:
        st = net.stations[h]
        c = (st.lat, st.lon)
        if st.access_stop:
            out.append(Pattern("access", (h,), None, st.access_stop, c))
        if st.egress_stop:
            out.append(Pattern("egress", (h,), st.egress_stop, None, c))
    for i in cluster:
        for j in cluster:
            if i == j:
                continue
            si, sj = net.stations[i], net.stations[j]
            if si.egress_stop and sj.access_stop and si.egress_stop != sj.access_stop:
                c = ((si.lat + sj.lat) / 2, (si.lon + sj.lon) / 2)
                out.append(Pattern("connect", (i, j), si.egress_stop, sj.access_stop, c))
    return out


def _pool(pattern: Pattern, K: dict[str, dict[str, float]], demand: Demand) -> dict[str, float]:
    stops = set()
    for h in pattern.hubs:
        stops |= set(K[h])
    stops -= pattern.station_stops
    vals = {s: stop_value(pattern, s, demand) for s in sorted(stops)}
    return {s: v for s, v in vals.items() if v > 0}


# ---------------------------------------------------------------- 전체

@dataclass
class Result:
    params: Params
    hubs: dict[str, float]
    catchment_sizes: dict[str, int]
    overlaps: dict[str, float]
    clusters: list[dict]
    candidates: list[Candidate]
    dropped: dict[str, int] = field(default_factory=dict)
    seconds: dict[str, float] = field(default_factory=dict)
    reasons: dict[str, int] = field(default_factory=dict)   # "단계/kind/사유" → 횟수(단계 = seed_L20, insertion_L30 …)
    reason_sets: dict[str, int] = field(default_factory=dict)   # "단계/kind/사유1+사유2" → 횟수


def _finish(c: Candidate, b: Builder, scenario: Scenario, p: Params, dropped: dict | None = None) -> bool:
    g, net = b.g, b.net
    st_stops = frozenset(x for x in (
        [net.stations[h].access_stop for h in c.hubs] + [net.stations[h].egress_stop for h in c.hubs]) if x)
    end_stops = frozenset(x for x in (c.stops[0], c.stops[-1]) if x in st_stops)
    cyc = route_cycle(c.stops, net.tt, scenario)
    if cyc is None:
        return False
    c.cycle_s, c.return_stops = cyc.period_s, cyc.return_stops
    if c.loop == "tour":
        return _finish_tour(c, b, p, dropped)
    if c.loop:
        # 최종 판정은 변환기가 실제로 고르는 고리 닫기 공차 경로로 한다
        from .loop import closure_s, cyclic_seq, designated_stops
        c.closure_s = closure_s(g, cyc.return_stops)
        why = None
        if c.closure_s > p.loop_closure_max_s:
            why = "loop_closure"
        elif g.has_uturn(cyclic_seq(c.stops, cyc.return_stops)):
            why = "loop_uturn"
        if why:
            if dropped is not None:
                dropped[why] = dropped.get(why, 0) + 1
            return False
        dz = designated_stops(net)
        out, ret = c.stops[:c.turn + 1], c.stops[c.turn:]
        c.out_oneway_s = g.oneway_s(out)
        c.ret_oneway_s = g.oneway_s(ret) if len(ret) > 1 else 0
        c.out_n_stops = sum(1 for s in out if s not in dz)
        c.ret_n_stops = sum(1 for s in ret[1:] if s not in dz)
        c.oneway_s = g.oneway_s(c.stops)
        c.length_m = g.length_m(c.stops)
        c.detour = g.detour(out)
        c.n_stops = sum(1 for s in c.stops if s not in dz)
        c.cover_keys = b.cover_keys_of(c.stops)
        c.cover = sum(b.od_total[k] for k in c.cover_keys)
        c.cross_gu = any(net.stops[s].gu != p.home_gu for s in c.stops)
        if p.must_stops:
            ms = set(p.must_stops)
            c.must = tuple(s for s in c.stops[1:-1] if s in ms)
        return True
    if p.must_stops:
        ms = set(p.must_stops)
        c.must = tuple(s for s in c.stops[1:-1] if s in ms)
    c.oneway_s = g.oneway_s(c.stops)
    c.length_m = g.length_m(c.stops)
    c.detour = g.detour(c.stops)
    c.n_stops = sum(1 for s in c.stops if s not in end_stops)
    c.cover_keys = b.cover_keys_of(c.stops)
    c.cover = sum(b.od_total[k] for k in c.cover_keys)
    c.cross_gu = any(net.stops[s].gu != p.home_gu for s in c.stops)
    return True


def direction_key(c: Candidate) -> tuple:
    """같은 방향 = 같은 kind + 같은 고정 역 정류장(access 끝, egress 시작, connect 둘 다).

    역 ID가 아니라 정류장으로 본다(온수 1821·2752처럼 역 둘이 지정 정류장 하나를 같이 쓰면 같은 노선이다).
    순환 후보는 양방향 수요를 함께 태우므로 모두 한 방향으로 본다(정류장이 80% 이상 겹치면 같은 노선). 반대로 도는 순환은
    길 건너편 정류장(다른 ID)을 써서 겹치지 않는다.
    """
    if c.loop:
        return ("loop",)
    if c.kind == "access":
        return ("access", c.stops[-1])
    if c.kind == "egress":
        return ("egress", c.stops[0])
    return ("connect", c.stops[0], c.stops[-1])


def dedupe(cands: list[Candidate], threshold: float) -> tuple[list[Candidate], int]:
    """같은 방향끼리 정차 정류장 Jaccard ≥ threshold면 주기가 짧은 쪽만 남긴다.

    방향이 다른 노선(예: access A→역과 egress 역→A)은 정류장 집합이 같아도 다른 승객을 태우므로 비교하지 않는다.
    동률이면 덮개가 큰 쪽, 그다음 정차열 사전순.
    """
    order = sorted(cands, key=lambda c: (c.cycle_s, -c.cover, c.stops, c.origin))
    kept: list[Candidate] = []
    for c in order:
        sc = set(c.stops)
        dup = False
        dk = direction_key(c)
        for k in kept:
            if direction_key(k) != dk or k.must != c.must:   # 필수 정류장을 덮는 버전은 같은 필수 정류장끼리만 비교
                continue
            sk = set(k.stops)
            if len(sc & sk) / len(sc | sk) >= threshold:
                dup = True
                break
        if not dup:
            kept.append(c)
    kept.sort(key=lambda c: c.route_id)
    return kept, len(cands) - len(kept)


def generate(net: Network, demand: Demand, scenario: Scenario, p: Params = Params(), reach=None,
             jobs: int = 1, tour_inputs=None) -> Result:
    """reach(`reach.Reach`, 선택): 덮는 수요를 평가 규칙으로 센다(설계와 평가가 같은 규칙, 설계 6.2·7절).
    jobs: 순환(6b) 돌아오는 길 만들기를 나눠 돌릴 프로세스 수(결과는 같다).
    tour_inputs(`tour.TourInputs`, p.tour일 때): 역 끝점 후보·호선. 비우면 지정 정류장만(③-T 없음)."""
    t0 = time.time()
    hubs = hubs_of(demand, net, p)
    graph = Graph(net, scenario.policy.dwell_base_s, cutoff_s=max(p.l_max_min) * 60, distance_basis=p.distance_basis)
    K = catchments(demand, net, hubs, p, graph)
    hub_ids = sorted(hubs)
    O = {(a, b): overlap(a, b, K, hubs) for a in hub_ids for b in hub_ids if a < b}
    clusters = merge_hubs(hub_ids, O, p.tau, p.tau_step, p.cluster_max)
    for ci, cl in enumerate(clusters):
        cl["id"] = ci
        gus = sorted({net.station_gu(h) or "?" for h in cl["hubs"]})
        cl["gu"] = gus
        cl["cross_gu"] = gus != [p.home_gu]
    t_setup = time.time()

    b = Builder(net, graph, demand, p, reach)
    raw: dict[tuple, Candidate] = {}
    dropped = {"seed_infeasible": 0, "conversion_failed": 0}

    def add(kind, stops, origin, hubs_, cluster, tag):
        key = (origin, kind, hubs_, stops)
        if key in raw:
            raw[key].made_by = tuple(sorted(set(raw[key].made_by) | {tag}))
            return
        raw[key] = Candidate("", kind, stops, origin, hubs_, cluster, (tag,))

    # 4. 섹터 분할 + 최선 삽입
    for cl in clusters:
        for pat in _patterns(cl["hubs"], net):
            values = _pool(pat, K, demand)
            for L in p.l_max_min:
                lim = Limits(L * 60, p.max_stops, p.max_detour)
                feas = [s for s in values if b.route_of(pat, pat.anchors_with(s), lim, f"seed_L{L}") is not None]
                dropped["seed_infeasible"] += len(values) - len(feas)
                for m in p.sectors:
                    for sec in sectors(feas, values, pat.center, m, net):
                        seed = max(sec, key=lambda s: (values[s] * seed_dist(graph, net, pat, s, p), s))
                        stops = b.insertion(pat, seed, sec, lim, f"insertion_L{L}")
                        if stops is not None:
                            add(pat.kind, stops, "generated", pat.hubs, cl["id"], f"m{m}_L{L}")
    t_gen = time.time()

    # 5. 최단경로 피더(허브·방향마다 수요 상위 sp_top seed, 같은 제약·L_max 최댓값)
    lim = Limits(max(p.l_max_min) * 60, p.max_stops, p.max_detour)
    cl_of = {h: cl["id"] for cl in clusters for h in cl["hubs"]}
    for h in hub_ids:
        for pat in _patterns([h], net):
            values = _pool(pat, K, demand)
            ranked = sorted(values, key=lambda s: (-values[s], s))
            picked = 0
            for s in ranked:
                if picked >= p.sp_top:
                    break
                stops = b.route_of(pat, pat.anchors_with(s), lim, f"sp_L{max(p.l_max_min)}")
                if stops is None:
                    continue
                picked += 1
                add(pat.kind, stops, "shortest_path_feeder", pat.hubs, cl_of[h], f"sp:{s}")
    t_sp = time.time()

    # 5b. 여러 역 경유 피더
    if p.multi:
        _multi_feeders(net, graph, demand, p, add)
    t_multi = time.time()

    # 6b. 순환
    loop_reasons: dict[str, int] = {}
    if p.loop:
        raw, loop_reasons = _loops(b, raw, p, scenario, dropped, jobs)
    t_loop = time.time()

    # 6e. 닫힌 순회(tour, 옵션). 순환 다음·필수 정류장 전
    tour_reasons: dict[str, int] = {}
    if p.tour:
        tour_reasons = _tours(b, raw, p, scenario, clusters, K, hubs, tour_inputs)

    # 6c. 필수 정류장: 가까운 기존 후보에 끼운 버전, 덮는 후보가 없을 때만 전용 순환
    must_stats: dict = {}
    if p.must_stops:
        raw, must_stats = _must_candidates(b, raw, p, scenario)
    t_must = time.time()

    # 6d. 순환 시작점 최적화(개선, 옵션)
    rotate_stats: dict = {}
    if p.loop and p.loop_rotate:
        raw, rotate_stats = _rotate_loops(b, raw, p)

    # 7. 덮개·주기
    cands = []
    for key in sorted(raw):
        c = raw[key]
        if _finish(c, b, scenario, p, dropped):
            cands.append(c)
        elif not c.loop:
            dropped["conversion_failed"] += 1
    _TOUR_STATE.clear()
    cands = [c for c in cands if c.cover > 0 or c.must]
    for i, c in enumerate(cands, start=1):
        c.route_id = f"c{i:04d}"
    t_fin = time.time()
    if p.must_stops:
        dropped["must"] = must_stats
    if rotate_stats:
        dropped["loop_rotate"] = rotate_stats
    return Result(p, hubs, {k: len(v) for k, v in K.items()}, {f"{a}-{b_}": round(v, 4) for (a, b_), v in O.items()},
                  clusters, cands, dropped,
                  {"setup": round(t_setup - t0, 2), "insertion": round(t_gen - t_setup, 2),
                   "shortest_path_feeders": round(t_sp - t_gen, 2), "multi_station_feeders": round(t_multi - t_sp, 2),
                   "loops": round(t_loop - t_multi, 2), "cover_cycle": round(t_fin - t_loop, 2)},
                  {**{"/".join(k): v for k, v in sorted(b.reasons.items())}, **loop_reasons, **tour_reasons},
                  {"/".join(k): v for k, v in sorted(b.reason_sets.items())})


def loop_limits(c: Candidate, p: Params):
    """방향마다 한도: 그 편도 후보를 만든 L_max(여러 개면 가장 짧은 것, 최단경로 피더는 L_max 최댓값),
    여러 역 경유는 multi_l_max_min·multi_max_detour."""
    from .loop import LoopLimits
    if c.kind == "multi":
        det = p.multi_max_detour if p.loop_ret_detour is None else max(p.loop_ret_detour, p.multi_max_detour)
        return LoopLimits(p.multi_l_max_min * 60, p.loop_max_stops, det, p.loop_closure_max_s, p.loop_max_stops_total)
    Ls = [int(t.split("_L")[1]) for t in c.made_by if "_L" in t]
    L = min(Ls) if Ls else max(p.l_max_min)
    det = p.max_detour if p.loop_ret_detour is None else p.loop_ret_detour
    return LoopLimits(L * 60, p.loop_max_stops, det, p.loop_closure_max_s, p.loop_max_stops_total)


_LOOP_STATE: dict = {}


def _loop_one(c: Candidate) -> tuple[list[tuple[str, str, tuple[str, ...]]], dict]:
    """편도 후보 하나 → [(방식, 변형, 한 바퀴 정차열)], 사유 집계."""
    lb, p, scenario, modes = (_LOOP_STATE[k] for k in ("lb", "p", "scenario", "modes"))
    lb.reasons = {}
    S = c.stops
    lim = loop_limits(c, p)
    out = []
    if len(S) < 2:
        return out, {}
    n_out = sum(1 for s in S if s not in lb.st_stops)
    if (lim.max_stops is not None and n_out > lim.max_stops) or \
            (lim.max_stops_total is not None and n_out >= lim.max_stops_total):
        for m in modes:
            lb._count(m, c.kind, "outbound_max_stops")
        return out, lb.reasons
    if "demand" in modes:
        for var in (("rev", "direct") if c.kind == "multi" else ("",)):
            ret = lb.demand_return(c.kind, c.hubs, S, lim, var)
            if ret is not None:
                out.append(("demand", var, S + ret[1:]))
    if "deadhead" in modes:
        cyc = route_cycle(S, lb.net.tt, scenario)
        if cyc is None:
            lb._count("deadhead", c.kind, "no_return")
        else:
            ret = lb.deadhead_return(c.kind, S, tuple(cyc.return_stops), lim)
            if ret is not None:
                out.append(("deadhead", "", S + ret[1:]))
    return out, lb.reasons


def _loops(b: Builder, raw: dict, p: Params, scenario: Scenario, dropped: dict, jobs: int = 1
           ) -> tuple[dict, dict[str, int]]:
    """6b. 편도 후보(가는 길)마다 한 바퀴 후보를 만든다. 반환: (새 raw, "loop/방식/kind/사유" → 횟수)."""
    from .loop import LoopBuilder
    modes = ("demand", "deadhead") if p.loop == "both" else (p.loop,)
    _LOOP_STATE.update(lb=LoopBuilder(b, p.radius_m), p=p, scenario=scenario, modes=modes)
    one = [raw[k] for k in sorted(raw)]
    if jobs > 1:
        import multiprocessing as mp
        with mp.get_context("fork").Pool(jobs) as pool:
            res = pool.map(_loop_one, one, chunksize=max(1, len(one) // (jobs * 8)))
    else:
        res = [_loop_one(c) for c in one]
    reasons: dict[str, int] = {}
    new: dict[tuple, Candidate] = {}
    for c, (loops, rs) in zip(one, res):
        for (m, k, r), v in rs.items():
            key = f"loop/{m}/{k}/{r}"
            reasons[key] = reasons.get(key, 0) + v
        if not loops:
            dropped["loop_failed"] = dropped.get("loop_failed", 0) + 1
        for mode, var, stops in loops:
            key = (c.origin, c.kind, c.hubs, stops, mode, var)
            if key in new:
                new[key].made_by = tuple(sorted(set(new[key].made_by) | set(c.made_by)))
                continue
            new[key] = Candidate("", c.kind, stops, c.origin, c.hubs, c.cluster, c.made_by,
                                 loop=mode, loop_variant=var, turn=len(c.stops) - 1)
    _LOOP_STATE.clear()
    return new, dict(sorted(reasons.items()))


def _rotate_loops(b: Builder, raw: dict, p: Params) -> tuple[dict, dict]:
    """6d. 순환 후보마다 시작점을 덮는 수요가 가장 큰 곳으로 돌린다(loop.best_rotation).

    원래 고리 닫기(마지막 → 첫)가 망의 한 구간이어야 한다(돌리면 그 구간이 영업 구간이 된다). 새 고리 닫기(원래 이웃한 두 정류장)는
    망의 한 구간이고 06~09시 최댓값이 고리 닫기 상한 이하여야 한다. 필수 정류장은 첫·끝으로 보내지 않는다.
    최종 고리 닫기·U턴 판정은 _finish가 변환기 경로로 다시 한다. 반환: (새 raw, {"loops", "rotated"})."""
    from dataclasses import replace
    from .loop import best_rotation
    tt, g = b.net.tt, b.g
    keep = frozenset(p.must_stops)

    def closure_ok(a: str, z: str) -> bool:
        return tt.has(a, z) and g.weight(a, z) <= p.loop_closure_max_s

    new: dict = {}
    stats = {"loops": 0, "rotated": 0}
    for key in sorted(raw):
        c = raw[key]
        S = c.stops
        i = 0
        if c.loop and c.loop != "tour" and len(S) > 2:
            stats["loops"] += 1
            if tt.has(S[-1], S[0]):
                i = best_rotation(S, b.cover_of, closure_ok, keep)
        if not i:
            new.setdefault(key, c)
            continue
        R = tuple(S[i:]) + tuple(S[:i])
        k2 = (c.origin, c.kind, c.hubs, R, c.loop, c.loop_variant, "rot")
        stats["rotated"] += 1
        if k2 in new:
            new[k2].made_by = tuple(sorted(set(new[k2].made_by) | set(c.made_by)))
            continue
        new[k2] = replace(c, stops=R, turn=(c.turn - i) % len(S), made_by=tuple(sorted(set(c.made_by) | {f"rot{i}"})))
    return new, stats


def _insert_must(g: Graph, stops: tuple[str, ...], turn: int, m: str):
    """필수 정류장 m을 이웃한 두 정류장 사이에 끼운다(영업 시간 증가가 가장 작은 자리, 동률이면 앞자리).

    사이에 끼우므로 m은 첫·끝이 아니다. 같은 정류장 두 번(S2)이 되면 그 자리는 버린다. 돌려주는 값: (정차열, 꺾는 순번).
    """
    best = None
    for i in range(len(stops) - 1):
        p1, p2 = g.path(stops[i], m), g.path(m, stops[i + 1])
        if p1 is None or p2 is None:
            continue
        new = stops[:i + 1] + p1[1:] + p2[1:] + stops[i + 2:]
        if len(set(new)) != len(new):
            continue
        t = g.oneway_s(new)
        nt = turn if i >= turn else turn + (len(new) - len(stops))
        if best is None or (t, i) < best[0]:
            best = ((t, i), new, nt)
    return (best[1], best[2]) if best else None


def _must_candidates(b: "Builder", raw: dict, p: Params, scenario: Scenario) -> tuple[dict, dict]:
    """6c. 필수 정류장 후보(설계 6.1c).

    ① 끼운 버전(origin must_insert): 필수 정류장에서 must_insert_radius_m 안에 정류장이 있는 기존 후보에 끼운다.
       순환 후보는 3b와 같은 제약(가는 길: 편도·정차·우회율·U턴, 돌아오는 길: `LoopBuilder.full_ok`)을 지킨 것만 남긴다.
    ② 전용 후보(origin must_feeder): 기존 후보(①포함)가 하나도 덮지 못한 필수 정류장에만 만든다. 직선거리로 가까운
       역부터 must_feeder_stations곳까지: 역 egress 정류장 → 필수 정류장(꺾는 정류장)을 가는 길로, 3b 수요 방식으로
       돌아오는 길을 붙인다(egress 패턴: 고정 정류장 = 그 역 access 지정 정류장). 우회율은 보지 않는다(길이·정차·U턴·
       고리 닫기는 지킨다). 필수 정류장은 꺾는 정류장이라 첫·끝이 아니다.
    """
    from .graph import Limits
    from .loop import LoopBuilder, LoopLimits
    g, net = b.g, b.net
    known = net.tt.stop_ids()
    must = sorted(set(p.must_stops))
    stats = {"must_stops": must, "not_in_network": [m for m in must if m not in known],
             "covered_by_existing": [], "insert": {}, "dedicated": {}}
    lb = LoopBuilder(b, p.radius_m) if p.loop else None

    def covers(c, m):
        return m in c.stops[1:-1]

    new = dict(raw)
    for m in must:
        if m in known and any(covers(c, m) for c in raw.values()):
            stats["covered_by_existing"].append(m)
    for key in sorted(raw):
        c = raw[key]
        for m in must:
            if m not in known or m in c.stops:
                continue
            if min(net.stop_dist_m(m, s) for s in c.stops) > p.must_insert_radius_m:
                continue
            st = stats["insert"].setdefault(m, {"tried": 0, "ok": 0})
            st["tried"] += 1
            turn = c.turn if c.loop else len(c.stops) - 1
            got = _insert_must(g, c.stops, turn, m)
            if got is None:
                continue
            stops, nt = got
            st_stops = frozenset(x for h in c.hubs for x in (net.stations[h].access_stop, net.stations[h].egress_stop) if x)
            if c.loop == "tour":
                if _TOUR_STATE.get("tb") is None:
                    continue
                bad = _TOUR_STATE["tb"].violations(stops, tour_limits(c, p))
                if bad:
                    continue
                st["ok"] += 1
                k2 = (c.origin, c.kind, c.hubs, stops, c.loop, "must")
                if k2 not in new:
                    new[k2] = Candidate("", c.kind, stops, c.origin, c.hubs, c.cluster,
                                        tuple(sorted(set(c.made_by) | {f"must:{m}"})), loop="tour")
                continue
            if c.loop:
                lim = loop_limits(c, p)
                out, ret = stops[:nt + 1], stops[nt:]
                bad = g.violations(out, st_stops, Limits(lim.l_max_s, lim.max_stops, lim.max_detour)) + \
                    lb.full_ok(out, ret, lim)
            else:
                L = min([int(t.split("_L")[1]) for t in c.made_by if "_L" in t] or [max(p.l_max_min)])
                bad = g.violations(stops, st_stops, Limits(L * 60, p.max_stops, p.max_detour))
            if bad:
                continue
            st["ok"] += 1
            k2 = (("must_insert", c.kind, c.hubs, stops, c.loop, c.loop_variant) if c.loop
                  else ("must_insert", c.kind, c.hubs, stops))
            if k2 not in new:
                new[k2] = Candidate("", c.kind, stops, "must_insert", c.hubs, c.cluster, (f"must:{m}",),
                                    loop=c.loop, loop_variant=c.loop_variant, turn=nt if c.loop else 0)
    stations = sorted(k for k, st_ in net.stations.items() if (st_.egress_stop or st_.access_stop) in known)
    L_free = max(p.l_max_min) * 60
    for m in must:
        if m not in known or any(covers(c, m) for c in new.values()):
            continue
        made = None
        for k in sorted(stations, key=lambda x: (net.station_stop_dist_m(x, m), x))[: p.must_feeder_stations]:
            st_ = net.stations[k]
            e = st_.egress_stop or st_.access_stop
            S = g.path(e, m)
            if S is None or len(S) < 2 or g.violations(S, frozenset({e}), Limits(L_free, p.loop_max_stops, float("inf"))):
                continue
            if lb is not None:
                lim = LoopLimits(L_free, p.loop_max_stops, float("inf"), p.loop_closure_max_s, p.loop_max_stops_total)
                ret = lb.demand_return("egress", (k,), S, lim)
                if ret is None:
                    continue
                stops = S + ret[1:]
                cand = Candidate("", "egress", stops, "must_feeder", (k,), None, (f"must:{m}",),
                                 loop="demand", turn=len(S) - 1)
                key = ("must_feeder", "egress", (k,), stops, "demand", "")
            else:
                back = g.path(m, st_.access_stop or e)
                if back is None:
                    continue
                stops = S + back[1:]
                if len(set(stops)) != len(stops):
                    continue
                cand = Candidate("", "egress", stops, "must_feeder", (k,), None, (f"must:{m}",))
                key = ("must_feeder", "egress", (k,), stops)
            if route_cycle(cand.stops, net.tt, scenario) is None:
                continue
            new[key] = cand
            made = k
            break
        stats["dedicated"][m] = made
    return new, stats


def _multi_feeders(net: Network, graph: Graph, demand: Demand, p: Params, add) -> None:
    """5b. 흩어진 수요 정류장 → 역 여러 곳(access 지정 정류장)을 차례로 들르는 후보."""
    from itertools import permutations
    access_of = {k: st.access_stop for k, st in net.stations.items() if st.access_stop}
    by_stop: dict[str, dict[str, float]] = {}
    for (o, d), hs in demand.od.items():
        for k, a in access_of.items():
            if d == a:
                by_stop.setdefault(o, {})
                by_stop[o][k] = by_stop[o].get(k, 0.0) + sum(hs)
    station_stops = frozenset(access_of.values())
    lim = Limits(p.multi_l_max_min * 60, None, p.multi_max_detour)
    seeds = sorted((s for s, m in by_stop.items() if s not in station_stops and net.stops.get(s)
                    and net.stops[s].gu == p.home_gu and sum(m.values()) > 0
                    and max(m.values()) / sum(m.values()) < p.multi_spread),
                   key=lambda s: (-sum(by_stop[s].values()), s))[:p.multi_top]
    for s in seeds:
        top = sorted(by_stop[s], key=lambda k: (-by_stop[s][k], k))[:p.multi_max_hubs]
        tried: set[tuple[str, ...]] = set()
        for n in range(2, len(top) + 1):
            for order in permutations(top, n):
                anchors = [s] + [access_of[k] for k in order]
                if len(set(anchors)) != len(anchors) or tuple(anchors) in tried:
                    continue
                tried.add(tuple(anchors))
                stops: tuple[str, ...] | None = (s,)
                for tgt in anchors[1:]:      # 역 정류장에서 되돌아 나가지 않게(U턴·같은 정류장 반복 없이) 이어 붙인다
                    leg = graph.path_continue(stops[-2] if len(stops) > 1 else None, stops[-1], tgt, set(stops[:-1]))
                    if leg is None:
                        stops = None
                        break
                    stops = stops + leg[1:]
                if stops is None or graph.violations(stops, station_stops, lim):
                    continue
                add("multi", stops, "multi_station_feeder", tuple(order), None, f"multi:{s}")


def pools(cands: list[Candidate], threshold: float) -> dict[str, tuple[list[Candidate], int]]:
    """배분 후보 풀. ours = 생성 + 최단경로 피더(중복 제거), sp = 최단경로 피더만(중복 제거)."""
    return {"ours": dedupe(cands, threshold),
            "sp": dedupe([c for c in cands if c.origin == "shortest_path_feeder"], threshold)}


# ---------------------------------------------------------------- 6e 닫힌 순회(tour.py)

_TOUR_STATE: dict = {}


def tour_limits(c: Candidate, p: Params):
    """후보를 만든 L(made_by의 "_L"; 없으면 목록 최댓값)로 순환 상한 2L."""
    from .tour import TourLimits
    Ls = [int(t.split("_L")[1]) for t in c.made_by if "_L" in t]
    L = min(Ls) if Ls else max(p.l_max_min)
    return TourLimits(2 * L * 60, p.tour_max_stops, p.tour_detour, p.loop_closure_max_s)


def select_pool(cands: list[Candidate], which: str) -> list[Candidate]:
    """배분 후보 풀(중복 제거 전): ours = tour가 아닌 후보, tour = tour만, all = 전부, tour_<생성기> = 그 생성기만."""
    if which == "all":
        return list(cands)
    if which == "ours":
        return [c for c in cands if not c.origin.startswith("tour_")]
    if which == "tour":
        return [c for c in cands if c.origin.startswith("tour_")]
    if which in ("tour_sector", "tour_feeder", "tour_pair"):
        return [c for c in cands if c.origin == which]
    raise ValueError(f"풀은 ours, tour, all, tour_sector, tour_feeder, tour_pair 중 하나: {which}")


def _finish_tour(c: Candidate, b: Builder, p: Params, dropped: dict | None) -> bool:
    """tour 최종 판정: 변환기 공차 경로로 고리 닫기·U턴, route_cycle 주기 ≤ 2L."""
    from .loop import closure_s, cyclic_seq, designated_stops
    g, net = b.g, b.net
    lim = tour_limits(c, p)
    c.closure_s = closure_s(g, c.return_stops)
    why = None
    if c.closure_s > lim.closure_max_s:
        why = "tour_closure"
    elif g.has_uturn(cyclic_seq(c.stops, c.return_stops)):
        why = "tour_uturn"
    elif c.cycle_s > lim.cycle_max_s:
        why = "tour_cycle"
    if why:
        if dropped is not None:
            dropped[why] = dropped.get(why, 0) + 1
        return False
    dz = designated_stops(net)
    tb = _TOUR_STATE.get("tb")
    c.turn = tb.far_index(c.stops) if tb is not None else len(c.stops) - 1
    out, ret = c.stops[:c.turn + 1], c.stops[c.turn:]
    c.out_oneway_s = g.oneway_s(out)
    c.ret_oneway_s = g.oneway_s(ret) if len(ret) > 1 else 0
    c.out_n_stops = sum(1 for s in out if s not in dz)
    c.ret_n_stops = sum(1 for s in ret[1:] if s not in dz)
    c.oneway_s = g.oneway_s(c.stops)
    c.length_m = g.length_m(c.stops)
    c.detour = g.detour(out) if len(out) > 1 else 1.0
    c.n_stops = sum(1 for s in c.stops if s not in dz)
    c.cover_keys = b.cover_keys_of(c.stops)
    c.cover = sum(b.od_total[k] for k in c.cover_keys)
    c.cross_gu = any(net.stops[s].gu != p.home_gu for s in c.stops)
    if p.must_stops:
        ms = set(p.must_stops)
        c.must = tuple(s for s in c.stops[1:-1] if s in ms)
    return True


def _tours(b: Builder, raw: dict, p: Params, scenario: Scenario, clusters: list[dict],
           K: dict[str, dict[str, float]], hubs: dict[str, float], inputs) -> dict[str, int]:
    """6e. ①-T 섹터 순회, ②-T 피더 순회, ③-T 역 쌍 순회를 raw에 더한다. 반환: "tour/단계/사유" → 횟수."""
    from .tour import TourBuilder, TourLimits, default_inputs, line_pairs
    net, graph = b.net, b.g
    tb = TourBuilder(b, scenario.policy, inputs or default_inputs(net), p.loop_closure_max_s)
    _TOUR_STATE["tb"] = tb
    cl_of = {h: cl["id"] for cl in clusters for h in cl["hubs"]}

    def lim_of(L: int) -> TourLimits:
        return TourLimits(2 * L * 60, p.tour_max_stops, p.tour_detour, p.loop_closure_max_s)

    def add(stops, origin, hubs_, cluster, tag):
        key = (origin, "tour", hubs_, stops, "tour", "")
        if key in raw:
            raw[key].made_by = tuple(sorted(set(raw[key].made_by) | {tag}))
            return
        raw[key] = Candidate("", "tour", stops, origin, hubs_, cluster, (tag,), loop="tour")

    def values_of(h: str) -> dict[str, float]:
        ea = tb.ends(h)
        ends = {ea[0], ea[1]} if ea else set()
        vals = {s: tb.value(h, "access", s) + tb.value(h, "egress", s) for s in sorted(K.get(h, {})) if s not in ends}
        return {s: v for s, v in vals.items() if v > 0}

    hub_ids = sorted(hubs)
    for h in hub_ids:
        if tb.ends(h) is None:
            tb.count("ends", "no_end_pair")
    # ①-T
    if "sector" in p.tour:
        for h in hub_ids:
            ea = tb.ends(h)
            if ea is None:
                continue
            e, a, _ = ea
            st = net.stations[h]
            pat = Pattern("tour", (h,), e, a, (st.lat, st.lon))
            values = values_of(h)
            for L in p.l_max_min:
                lim = lim_of(L)
                feas = [s for s in values if tb.route([e, s, a], lim, f"sector_seed_L{L}") is not None]
                for m in p.sectors:
                    for sec in sectors(feas, values, pat.center, m, net):
                        seed = max(sec, key=lambda s: (values[s] * seed_dist(graph, net, pat, s, p), s))
                        stops = tb.insertion([e, seed, a], sec, lim, f"sector_L{L}", frozenset({e, a}))
                        if stops is not None:
                            add(stops, "tour_sector", (h,), cl_of[h], f"tm{m}_L{L}")
    # ②-T
    if "feeder" in p.tour:
        L = max(p.l_max_min)
        lim = lim_of(L)
        for h in hub_ids:
            if tb.ends(h) is None:
                continue
            values = values_of(h)
            picked = 0
            for s in sorted(values, key=lambda x: (-values[x], x)):
                if picked >= p.sp_top:
                    break
                stops = tb.feeder(h, s, lim, f"feeder_L{L}")
                if stops is None:
                    continue
                picked += 1
                add(stops, "tour_feeder", (h,), cl_of[h], f"tsp:{s}_L{L}")
    # ③-T
    if "pair" in p.tour:
        lines = (inputs.lines if inputs is not None else {})
        ok = {h: hubs[h] for h in hub_ids if tb.ends(h) is not None}
        chosen = []
        for P, Q in line_pairs(ok, lines):
            eP, aP, _ = tb.ends(P)
            eQ, aQ, _ = tb.ends(Q)
            same = cl_of.get(P) == cl_of.get(Q)
            if not same:
                tP = graph.tree(eP).get(aQ)
                tQ = graph.tree(eQ).get(aP)
                t = min(x[0] for x in (tP, tQ) if x) if (tP or tQ) else None
                if t is None or t > p.tour_pair_max_s:
                    tb.count("pair", "far")
                    continue
            chosen.append((P, Q))
            if len(chosen) >= p.tour_pair_top:
                break
        for P0, Q0 in chosen:
            for P, Q in ((P0, Q0), (Q0, P0)):
                eP, aP, _ = tb.ends(P)
                eQ, aQ, _ = tb.ends(Q)
                ends = {eP, aP, eQ, aQ}
                pool = sorted((set(K.get(P, {})) | set(K.get(Q, {}))) - ends)
                v1 = {s: tb.value(P, "egress", s) + tb.value(Q, "access", s) for s in pool}
                v2 = {s: tb.value(Q, "egress", s) + tb.value(P, "access", s) for s in pool}
                vals = {s: v1[s] + v2[s] for s in pool}
                pool = [s for s in pool if vals[s] > 0]

                mid = [aQ] if aQ == eQ else [aQ, eQ]
                for L in p.l_max_min:
                    lim = lim_of(L)
                    # 씨앗(1절과 같은 규칙): 구간 1 → 그 씨앗만 넣은 순회가 제약을 지키는 정류장 중 가치 × 망 거리 최대,
                    # 구간 2 → 구간 1 씨앗을 둔 채 같은 규칙
                    s1 = tb.pair_seed(v1, lambda s: [eP, s] + mid + [aP], lim, frozenset(), eP, aQ)
                    pre = [eP] + ([s1] if s1 else []) + mid
                    s2 = tb.pair_seed(v2, lambda s: pre + [s, aP], lim, frozenset({s1} if s1 else ()), eQ, aP)
                    anchors = [eP] + ([s1] if s1 else []) + mid + ([s2] if s2 else []) + [aP]
                    stops = tb.insertion(anchors, pool, lim, f"pair_L{L}", frozenset(ends))
                    if stops is not None:
                        add(stops, "tour_pair", (P, Q), cl_of.get(P) if cl_of.get(P) == cl_of.get(Q) else None,
                            f"tpair_L{L}")
    return {f"tour/{st}/{r}": v for (st, r), v in sorted(tb.reasons.items())}
