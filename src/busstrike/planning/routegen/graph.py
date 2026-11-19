"""망(travel_times) 위 최단경로와 노선 지표.

- 경로 가중치는 구간의 06~09시 프로필 중 **최댓값**이다(편도 상한을 재는 기준과 같다).
  같은 비용이면 정류장열이 사전순으로 앞선 경로(변환기 공차 경로 규칙과 같은 동률 처리).
- 통과 정류장 표현이 시뮬레이터에 없으므로 경로 위 정류장은 모두 정차로 센다.
- `distance_basis`
  - "crow"(기본, v1·v4 결과 재현): 우회율 분모 = 첫·끝 정류장 직선거리.
  - "network": 우회율 분모 = 망 위 첫 → 끝 최단 거리(구간 `distance_m` 합). 직선거리를 쓰지 않는다.
- 망에 `turns`(구간별 도로 조각 기록)가 있으면, 정류장에서 되돌아가는 이음(U턴)을 위반 "uturn"으로 센다(`is_uturn`).
  U턴을 막는 이유는 "도로가 좁아 버스가 실제로 돌 수 없어서"다. 그래서 정류장이나 길 중간에서 제자리로 돌아 나오는 것은
  막고, 블록·순환로를 돌아 반대쪽으로 가는 것(지난 도로를 되짚지 않음)은 U턴으로 보지 않는다(허용).
  - turns-v2(`roadnet` edge 망, `{"_meta": {"format": "turns-v2"}, "a>b": {"s", "e", "tail", "head"}}`):
    a→b→c가 b에서 U턴 ⇔ (1) a→b 도착 정차 노드 ≠ b→c 출발 정차 노드(양방향 정차 정류장에서 길 건너편으로 바꿔 탐), 또는
    (2) b→c 앞부분(head)이 a→b 끝부분(tail)이 지난 OSM 도로 조각을 반대 방향으로 되짚는다.
  - 예전 형식(`"a>b": [첫 조각 u, v, 마지막 조각 u, v]`, node 망): b→c 첫 조각 = a→b 마지막 조각의 역방향일 때.
    옛 파일은 예전 규칙 그대로 읽는다(옛 결과 재현용). 정류장이 곁길 노드에 붙은 곳에서 직진도 U턴으로 잡는 오탐이 있다.
  - 조각 기록이 없는 구간(도로 경로를 못 찾은 기존 버스 구간)이 끼면 판단하지 않는다(U턴 아님으로 본다).
    `roadnet`은 거리 상한 안에 경로가 없는 기존 버스 구간도 더 큰 상한으로 U턴 없는 경로를 찾아 조각 기록을 남긴다
    (긴 경로 매칭, 기록에 "l": 1). 판정 규칙은 같다.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass

from .data import Network


@dataclass(frozen=True)
class Limits:
    l_max_s: int                 # 편도 상한(초)
    max_stops: int | None = 8    # 역 지정 정류장을 뺀 정차 수 상한 N_max(None = 검사하지 않음)
    max_detour: float = 1.6      # 경로 길이 ÷ 첫·끝 정류장 직선거리


TURNS_V2 = "turns-v2"


def parse_turns(turns: dict | None):
    """turns 기록을 읽어 (형식, 구간 → 판정용 값)으로. 형식 = None(기록 없음) | "v1"(예전) | "v2".

    v2 값 = (출발 정차 노드, 도착 정차 노드, tail 조각 집합, head 조각을 뒤집은 집합).
    """
    if not turns:
        return None, {}
    meta = turns.get("_meta")
    if isinstance(meta, dict):
        if meta.get("format") != TURNS_V2:
            raise ValueError(f"모르는 turns 형식: {meta.get('format')}")
        out = {}
        for k, r in turns.items():
            if k == "_meta":
                continue
            out[k] = (r["s"], r["e"], frozenset(tuple(x) for x in r["tail"]),
                      frozenset((x[1], x[0]) for x in r["head"]))
        return "v2", out
    if all(isinstance(v, list) and len(v) == 4 for v in turns.values()):
        return "v1", turns
    raise ValueError("turns 형식을 알 수 없다(예전 [u, v, u, v] 목록도 turns-v2도 아님)")


class Graph:
    def __init__(self, net: Network, dwell_s: int, cutoff_s: int, distance_basis: str = "crow"):
        if distance_basis not in ("crow", "network"):
            raise ValueError(f"distance_basis는 crow 또는 network: {distance_basis}")
        self.net = net
        self.tt = net.tt
        self.dwell_s = dwell_s
        self.cutoff_s = cutoff_s
        self.distance_basis = distance_basis
        self.turns_format, self._turns = parse_turns(net.turns)
        self._dfrom: dict[str, dict[str, int]] = {}
        self._dto: dict[str, dict[str, int]] = {}
        self._w: dict[tuple[str, str], int] = {}
        self._prof: dict[tuple[str, str], tuple[int, ...]] = {}
        bins = self.tt.bin_starts()
        for a in sorted(self.tt.stop_ids()):
            for b in self.tt.successors(a):
                prof = tuple(self.tt.lookup(a, b, t) for t in bins)
                self._prof[(a, b)] = prof
                self._w[(a, b)] = max(prof)
        self.n_bins = len(bins)
        self._dsucc: dict[str, list[tuple[str, int]]] = {}
        self._dpred: dict[str, list[tuple[str, int]]] = {}
        for (a, b) in sorted(self._w):
            d = self.tt.distance(a, b)
            self._dsucc.setdefault(a, []).append((b, d))
            self._dpred.setdefault(b, []).append((a, d))
        self._fwd: dict[str, dict[str, tuple[int, tuple[str, ...]]]] = {}
        self._any: dict[str, dict[str, tuple[int, tuple[str, ...]]]] = {}   # 진단용(cutoff 없음)

    @staticmethod
    def _dij(adj: dict[str, list[tuple[str, int]]], src: str) -> dict[str, int]:
        dist = {src: 0}
        h = [(0, src)]
        while h:
            d, u = heapq.heappop(h)
            if d > dist[u]:
                continue
            for v, L in adj.get(u, ()):
                nd = d + L
                if nd < dist.get(v, 1 << 62):
                    dist[v] = nd
                    heapq.heappush(h, (nd, v))
        return dist

    def dist_from(self, src: str) -> dict[str, int]:
        """src에서 망 위 최단 거리(m, 구간 distance_m 합)."""
        if src not in self._dfrom:
            self._dfrom[src] = self._dij(self._dsucc, src)
        return self._dfrom[src]

    def dist_to(self, dst: str) -> dict[str, int]:
        """각 정류장에서 dst까지 망 위 최단 거리(m)."""
        if dst not in self._dto:
            self._dto[dst] = self._dij(self._dpred, dst)
        return self._dto[dst]

    def weight(self, a: str, b: str) -> int:
        return self._w[(a, b)]

    def tree(self, src: str, capped: bool = True) -> dict[str, tuple[int, tuple[str, ...]]]:
        """src에서 비용 cutoff_s 이하로 닿는 정류장 → (비용, 경로). capped=False는 거절 사유 진단용(상한 없음)."""
        cache = self._fwd if capped else self._any
        limit = self.cutoff_s if capped else float("inf")
        got = cache.get(src)
        if got is not None:
            return got
        heap: list[tuple[int, tuple[str, ...]]] = [(0, (src,))]
        done: dict[str, tuple[int, tuple[str, ...]]] = {}
        while heap:
            cost, path = heapq.heappop(heap)
            here = path[-1]
            if here in done:
                continue
            done[here] = (cost, path)
            for nxt in self.tt.successors(here):
                if nxt in done:
                    continue
                c = cost + self._w[(here, nxt)]
                if c <= limit:
                    heapq.heappush(heap, (c, path + (nxt,)))
        cache[src] = done
        return done

    def is_uturn(self, a: str, b: str, c: str) -> bool:
        """a→b→c가 정류장 b에서 U턴인가(두 구간 모두 도로 조각 기록이 있을 때만 판단). 규칙은 모듈 설명."""
        if self.turns_format is None:
            return False
        e1, e2 = self._turns.get(f"{a}>{b}"), self._turns.get(f"{b}>{c}")
        if not (e1 and e2):
            return False
        if self.turns_format == "v1":
            return e2[0] == e1[3] and e2[1] == e1[2]
        return e1[1] != e2[0] or not e1[2].isdisjoint(e2[3])

    def path_continue(self, prev: str | None, a: str, b: str, avoid: set[str] | frozenset[str] = frozenset(),
                      then: str | None = None) -> tuple[str, ...] | None:
        """a에서 b까지 최단경로. 직전 정류장 prev에서 a로 들어온 뒤 a·경로 중간 어디서도 U턴하지 않고, avoid 정류장을 지나지 않는다.

        상태 = (직전 정류장, 현재 정류장). 비용 상한 cutoff_s. 여러 역을 차례로 잇는 노선의 구간 연결에 쓴다.
        then(선택): b 다음에 갈 정류장. 주면 b에서도 U턴하지 않는(직전 → b → then이 U턴이 아닌) 경로만 받는다
        (순환 노선이 첫 정류장으로 돌아와 고리를 닫을 때).
        """
        start = (prev, a)
        best = {start: 0}
        back: dict[tuple, tuple | None] = {start: None}
        h = [(0, prev or "", a)]
        while h:
            c, pv, u = heapq.heappop(h)
            st = (pv or None, u)
            if c > best.get(st, 1 << 62):
                continue
            if u == b and (then is None or st[0] is None or not self.is_uturn(st[0], b, then)):
                out = []
                while st is not None:
                    out.append(st[1])
                    st = back[st]
                return tuple(reversed(out))
            if u == b:
                continue                         # b에서 then으로 U턴하는 도착: b를 지나 다시 오는 경로는 b가 두 번이라 보지 않는다
            for v in self.tt.successors(u):
                if v in avoid or v == st[0]:
                    continue
                if st[0] is not None and self.is_uturn(st[0], u, v):
                    continue
                nc = c + self._w[(u, v)]
                ns = (u, v)
                if nc <= self.cutoff_s and nc < best.get(ns, 1 << 62):
                    best[ns] = nc
                    back[ns] = st
                    heapq.heappush(h, (nc, u, v))
        return None

    def path(self, a: str, b: str) -> tuple[str, ...] | None:
        if a == b:
            return (a,)
        hit = self.tree(a).get(b)
        return hit[1] if hit else None

    def expand(self, anchors: list[str] | tuple[str, ...]) -> tuple[str, ...] | None:
        """고른 정류장(anchor)을 순서대로 최단경로로 이은 정차열. 못 잇거나 정류장이 겹치면 None(S2)."""
        out: list[str] = [anchors[0]]
        for a, b in zip(anchors, anchors[1:]):
            p = self.path(a, b)
            if p is None:
                return None
            out.extend(p[1:])
        if len(set(out)) != len(out):
            return None
        return tuple(out)

    def diagnose(self, anchors: list[str] | tuple[str, ...], station_stops: frozenset[str], lim: Limits) -> list[str]:
        """expand가 실패한 anchor열의 거절 사유. 결과에는 쓰지 않고 집계에만 쓴다.

        cutoff 없이 최단경로로 이어 붙인 정차열에 모든 제약을 매긴다. 망에서 아예 못 닿으면 ["unreachable"].
        cutoff(= L_max 최댓값)를 넘는 구간이 있으면 그 정차열은 반드시 l_max에 걸린다.
        """
        out: list[str] = [anchors[0]]
        for a, b in zip(anchors, anchors[1:]):
            hit = self.tree(a, capped=False).get(b) if a != b else (0, (a,))
            if hit is None:
                return ["unreachable"]
            out.extend(hit[1][1:])
        return self.violations(tuple(out), station_stops, lim) or ["no_path_or_repeat"]

    # ------------------------------------------------------------ 지표

    def oneway_s(self, stops: tuple[str, ...]) -> int:
        """편도 = max_h(Σ 구간 주행 h시) + 기본 정차 × 구간 수. 시간대마다 따로 재서 최댓값."""
        per_bin = [0] * self.n_bins
        for a, b in zip(stops, stops[1:]):
            prof = self._prof[(a, b)]
            for i, v in enumerate(prof):
                per_bin[i] += v
        return max(per_bin) + self.dwell_s * (len(stops) - 1)

    def length_m(self, stops: tuple[str, ...]) -> int:
        return sum(self.tt.distance(a, b) for a, b in zip(stops, stops[1:]))

    def detour(self, stops: tuple[str, ...]) -> float:
        if self.distance_basis == "network":
            base = self.dist_from(stops[0]).get(stops[-1], 0)
        else:
            base = self.net.stop_dist_m(stops[0], stops[-1])
        if base <= 0:
            return float("inf")
        return self.length_m(stops) / base

    def has_uturn(self, stops: tuple[str, ...]) -> bool:
        return any(self.is_uturn(a, b, c) for a, b, c in zip(stops, stops[1:], stops[2:]))

    def violations(self, stops: tuple[str, ...], station_stops: frozenset[str], lim: Limits) -> list[str]:
        """제약 위반 목록(비면 통과). station_stops = 이 패턴의 역 지정 정류장(정차 수에서 뺀다)."""
        bad = []
        if len(set(stops)) != len(stops):
            bad.append("repeat")
        if self.oneway_s(stops) > lim.l_max_s:
            bad.append("l_max")
        if lim.max_stops is not None and sum(1 for s in stops if s not in station_stops) > lim.max_stops:
            bad.append("max_stops")
        if self.detour(stops) > lim.max_detour:
            bad.append("detour")
        if self.has_uturn(stops):
            bad.append("uturn")
        return bad
