"""정류장 사이 도보 거리(OSM 도로를 따라, 방향·일방통행 무시). 직선거리를 쓰지 않는다.

도보 거리 = 정류장 → 도로 위 붙인 자리(옆 거리) + 도로 위 최단 거리 + 도로 위 자리 → 정류장(옆 거리).
- edge 망(`RoadNet(snap_mode="edge")`): 붙인 자리 = 가장 가까운 도로 선분 위 수직 투영점. 사람은 길을 건널 수 있으므로
  버스 진행 방향(정차 노드)과 상관없이 정류장마다 투영점 하나를 쓴다. 길 양쪽 정류장은 투영점 사이 거리 + 두 옆 거리로 잰다.
- node 망(예전): 붙인 자리 = 가장 가까운 도로 노드.
받은 OSM은 차도 종류만 담고 있어(보도·골목길 일부 없음) 실제 도보 거리보다 길게 나올 수 있다(보수적).
"""

from __future__ import annotations

import heapq
from collections import defaultdict

from roadnet import RoadNet


def walk_neighbors(rn: RoadNet, radius_m: float = 400.0, stops: set[str] | None = None) -> dict[str, dict[str, float]]:
    """{정류장: {도보 radius_m 안 정류장: 거리 m}}. 자기 자신(0m)을 포함한다. 도로에 안 붙은 정류장은 자기 자신만."""
    und, anchor, snapd = rn.walk_graph()
    at_node: dict[int, list[str]] = defaultdict(list)
    for s, n in anchor.items():
        at_node[n].append(s)
    out: dict[str, dict[str, float]] = {}
    for s in sorted(stops if stops is not None else rn.stops):
        out[s] = {s: 0.0}
        if s not in anchor:
            continue
        cut = radius_m - snapd[s]
        if cut <= 0:
            continue
        dist = {anchor[s]: 0.0}
        h = [(0.0, anchor[s])]
        while h:
            d, u = heapq.heappop(h)
            if d > dist[u]:
                continue
            for t in at_node.get(u, ()):
                w = snapd[s] + d + snapd[t]
                if t != s and w <= radius_m and w < out[s].get(t, 1e18):
                    out[s][t] = round(w, 1)
            for v, L in und.get(u, ()):
                nd = d + L
                if nd <= cut and nd < dist.get(v, 1e18):
                    dist[v] = nd
                    heapq.heappush(h, (nd, v))
    return out
