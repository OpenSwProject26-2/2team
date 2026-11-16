"""망(travel_times) 위 최단경로와 노선 지표.

- 경로 가중치는 구간의 06~09시 프로필 중 **최댓값**이다(편도 상한을 재는 기준과 같다).
  같은 비용이면 정류장열이 사전순으로 앞선 경로(변환기 공차 경로 규칙과 같은 동률 처리).
- 통과 정류장 표현이 시뮬레이터에 없으므로 경로 위 정류장은 모두 정차로 센다.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass

from .data import Network


@dataclass(frozen=True)
class Limits:
    l_max_s: int                 # 편도 상한(초)
    max_stops: int = 8           # 역 지정 정류장을 뺀 정차 수 상한
    max_detour: float = 1.6      # 경로 길이 ÷ 첫·끝 정류장 직선거리


class Graph:
    def __init__(self, net: Network, dwell_s: int, cutoff_s: int):
        self.net = net
        self.tt = net.tt
        self.dwell_s = dwell_s
        self.cutoff_s = cutoff_s
        self._w: dict[tuple[str, str], int] = {}
        self._prof: dict[tuple[str, str], tuple[int, ...]] = {}
        bins = self.tt.bin_starts()
        for a in sorted(self.tt.stop_ids()):
            for b in self.tt.successors(a):
                prof = tuple(self.tt.lookup(a, b, t) for t in bins)
                self._prof[(a, b)] = prof
                self._w[(a, b)] = max(prof)
        self.n_bins = len(bins)
        self._fwd: dict[str, dict[str, tuple[int, tuple[str, ...]]]] = {}

    def weight(self, a: str, b: str) -> int:
        return self._w[(a, b)]

    def tree(self, src: str) -> dict[str, tuple[int, tuple[str, ...]]]:
        """src에서 비용 cutoff_s 이하로 닿는 정류장 → (비용, 경로)."""
        got = self._fwd.get(src)
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
                if c <= self.cutoff_s:
                    heapq.heappush(heap, (c, path + (nxt,)))
        self._fwd[src] = done
        return done

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
        straight = self.net.stop_dist_m(stops[0], stops[-1])
        if straight <= 0:
            return float("inf")
        return self.length_m(stops) / straight

    def violations(self, stops: tuple[str, ...], station_stops: frozenset[str], lim: Limits) -> list[str]:
        """제약 위반 목록(비면 통과). station_stops = 이 패턴의 역 지정 정류장(정차 수에서 뺀다)."""
        bad = []
        if len(set(stops)) != len(stops):
            bad.append("repeat")
        if self.oneway_s(stops) > lim.l_max_s:
            bad.append("l_max")
        if sum(1 for s in stops if s not in station_stops) > lim.max_stops:
            bad.append("max_stops")
        if self.detour(stops) > lim.max_detour:
            bad.append("detour")
        return bad
