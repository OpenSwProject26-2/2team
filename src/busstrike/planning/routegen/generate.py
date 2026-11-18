"""후보 노선 생성(설계 v1 4절). 구 하나·결정론.

1. 허브: F_k = Σ_s r(s↔k)(access+egress, 4시간 합, 5일 평균) ≥ F_min = 2 × 좌석 × (240 ÷ C₀)
2. 권역 K_k: 허브 반경 R 안에서 r(s↔k) > 0인 정류장(중복 소속 허용)
3. 겹침 병합: O_ij = Σ_{s∈K_i∩K_j}(r(s↔i)+r(s↔j)) / min(F_i, F_j) ≥ τ → union-find.
   군집이 역 3개를 넘으면 그 군집만 τ를 τ_step씩 올려 다시 나눈다. 구 경계를 넘으면 cross_gu.
4. 섹터 분할 + 최선 삽입: 패턴·m·L_max마다 섹터별 seed = argmax v_s × dist(s, 중심),
   삽입 점수 = 증가 덮개 수요 ÷ 증가 편도 시간. 제약(편도·정차 수·우회율·S2) 위반 삽입은 버린다. 끝에 2-opt 1회.
5. 추가 후보: 허브·방향마다 수요 상위 seed의 최단경로 피더(삽입 휴리스틱의 비교 기준).
6. 패턴: 회송 피더(access: 정류장들 → 역 access 정류장, egress: 역 egress 정류장 → 정류장들),
   편도 연결(역 i egress 정류장 → 정류장들 → 역 j access 정류장). 순환·양방향 영업은 만들지 않는다.
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

    @property
    def f_min(self) -> float:
        return 2 * self.seat * (self.horizon_s / self.c0_s)


@dataclass
class Candidate:
    route_id: str
    kind: str                         # access | egress | connect
    stops: tuple[str, ...]
    origin: str                       # generated | shortest_path_feeder
    hubs: tuple[str, ...]             # 패턴의 역(access/egress: 1개, connect: i, j)
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

def hubs_of(demand: Demand, net: Network, p: Params) -> dict[str, float]:
    F = demand.station_total()
    return {k: F[k] for k in sorted(F)
            if F[k] >= p.f_min and k in net.stations
            and (net.stations[k].access_stop or net.stations[k].egress_stop)}


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
    def __init__(self, net: Network, graph: Graph, demand: Demand, p: Params):
        self.net, self.g, self.demand, self.p = net, graph, demand, p
        self.od_total = {k: sum(v) for k, v in demand.od.items()}
        # 거절 사유 집계: (단계, kind, 사유) → 횟수. 사유는 여러 개가 함께 붙을 수 있다(편도·정차 수·우회율).
        self.reasons: dict[tuple[str, str, str], int] = {}
        # 거절 사유 조합: (단계, kind, "l_max+detour" 같은 조합) → 횟수(단독 원인을 셀 때 쓴다)
        self.reason_sets: dict[tuple[str, str, str], int] = {}

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
        cur_dem = cover_value(stops, self.od_total)
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
                    d_dem = cover_value(new, self.od_total) - cur_dem
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
            cur_dem = cover_value(stops, self.od_total)
            cur_t = self.g.oneway_s(stops)
        return self.two_opt(pattern, anchors, stops, lim)

    def two_opt(self, pattern: Pattern, anchors: list[str], stops: tuple[str, ...], lim: Limits) -> tuple[str, ...]:
        """한 번 훑기: 움직일 수 있는 anchor 구간을 뒤집어 편도가 줄고 덮개가 줄지 않으면 받는다."""
        cur_t = self.g.oneway_s(stops)
        cur_dem = cover_value(stops, self.od_total)
        mv = list(pattern.movable(anchors))
        for x in range(len(mv)):
            for y in range(x + 1, len(mv)):
                i, j = mv[x], mv[y]
                trial = anchors[:i] + anchors[i:j + 1][::-1] + anchors[j + 1:]
                new = self.route_of(pattern, trial, lim)
                if new is None:
                    continue
                t = self.g.oneway_s(new)
                dem = cover_value(new, self.od_total)
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


def _finish(c: Candidate, b: Builder, scenario: Scenario, p: Params) -> bool:
    g, net = b.g, b.net
    st_stops = frozenset(x for x in (
        [net.stations[h].access_stop for h in c.hubs] + [net.stations[h].egress_stop for h in c.hubs]) if x)
    end_stops = frozenset(x for x in (c.stops[0], c.stops[-1]) if x in st_stops)
    cyc = route_cycle(c.stops, net.tt, scenario)
    if cyc is None:
        return False
    c.cycle_s, c.return_stops = cyc.period_s, cyc.return_stops
    c.oneway_s = g.oneway_s(c.stops)
    c.length_m = g.length_m(c.stops)
    c.detour = g.detour(c.stops)
    c.n_stops = sum(1 for s in c.stops if s not in end_stops)
    c.cover_keys = tuple(covered_keys(c.stops, b.demand.od))
    c.cover = sum(b.od_total[k] for k in c.cover_keys)
    c.cross_gu = any(net.stops[s].gu != p.home_gu for s in c.stops)
    return True


def direction_key(c: Candidate) -> tuple:
    """같은 방향 = 같은 kind + 같은 고정 역 정류장(access 끝, egress 시작, connect 둘 다).

    역 ID가 아니라 정류장으로 본다(온수 1821·2752처럼 역 둘이 지정 정류장 하나를 같이 쓰면 같은 노선이다).
    """
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
            if direction_key(k) != dk:
                continue
            sk = set(k.stops)
            if len(sc & sk) / len(sc | sk) >= threshold:
                dup = True
                break
        if not dup:
            kept.append(c)
    kept.sort(key=lambda c: c.route_id)
    return kept, len(cands) - len(kept)


def generate(net: Network, demand: Demand, scenario: Scenario, p: Params = Params()) -> Result:
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

    b = Builder(net, graph, demand, p)
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

    # 7. 덮개·주기
    cands = []
    for key in sorted(raw, key=lambda k: (k[0], k[1], k[2], k[3])):
        c = raw[key]
        if _finish(c, b, scenario, p):
            cands.append(c)
        else:
            dropped["conversion_failed"] += 1
    cands = [c for c in cands if c.cover > 0]
    for i, c in enumerate(cands, start=1):
        c.route_id = f"c{i:04d}"
    t_fin = time.time()
    return Result(p, hubs, {k: len(v) for k, v in K.items()}, {f"{a}-{b_}": round(v, 4) for (a, b_), v in O.items()},
                  clusters, cands, dropped,
                  {"setup": round(t_setup - t0, 2), "insertion": round(t_gen - t_setup, 2),
                   "shortest_path_feeders": round(t_sp - t_gen, 2), "cover_cycle": round(t_fin - t_sp, 2)},
                  {"/".join(k): v for k, v in sorted(b.reasons.items())},
                  {"/".join(k): v for k, v in sorted(b.reason_sets.items())})


def pools(cands: list[Candidate], threshold: float) -> dict[str, tuple[list[Candidate], int]]:
    """배분 후보 풀. ours = 생성 + 최단경로 피더(중복 제거), sp = 최단경로 피더만(중복 제거)."""
    return {"ours": dedupe(cands, threshold),
            "sp": dedupe([c for c in cands if c.origin == "shortest_path_feeder"], threshold)}
