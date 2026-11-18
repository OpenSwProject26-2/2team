"""실제 도로망(OSM)으로 '버스가 다니는 도로 = 1' 망을 만든다(실험용).

1. OSM 도로를 방향 그래프로 만든다(일방통행 반영). 회전 제한은 반영하지 않는다.
2. 정류장을 60m 안의 가장 가까운 도로 노드에 붙인다. 길 건너편 정류장은 같은 노드에 붙을 수 있다.
3. 기존 버스 구간 A→B마다 도로 위 최단경로를 찾는다(길이 ≤ max(2×직선, 직선+400m)). 그 도로에 1을 표시하고,
   구간 실측 시간 ÷ 경로 길이로 시간대별 속도 표본을 남긴다.
4. 버스 도로 = 1인 도로 + 양방향 도로면 그 반대 방향.
5. 정류장 a에서 버스 도로로 `cut_m` 안에 닿는 정류장 b마다, 경로 중간에 다른 정류장이 없으면 a→b 구간을 더한다.
   시간 = Σ 도로 길이 ÷ 그 도로의 버스 실측 속도(시간대별, 없으면 전체 중앙값). 이미 있는 구간은 그대로 둔다.

새 구간은 실측이 아니라 추정이다. 출처를 `road_estimated`로 따로 기록한다.
"""

from __future__ import annotations

import csv
import heapq
import json
import math
import statistics as st
from collections import defaultdict
from pathlib import Path

ONEWAY_TRUE = ("yes", "true", "1")


def hav(a1: float, o1: float, a2: float, o2: float) -> float:
    r = 6371008.8
    p1, p2 = math.radians(a1), math.radians(a2)
    dp, dl = p2 - p1, math.radians(o2 - o1)
    return 2 * r * math.asin(math.sqrt(math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2))


class RoadNet:
    def __init__(self, osm_path: Path, root: Path, snap_m: float = 60.0):
        osm = json.loads(Path(osm_path).read_text(encoding="utf-8"))
        self.node = {e["id"]: (e["lat"], e["lon"]) for e in osm["elements"] if e["type"] == "node"}
        self.adj: dict[int, list[tuple[int, float]]] = defaultdict(list)
        for e in osm["elements"]:
            if e["type"] != "way":
                continue
            t = e.get("tags", {})
            hw, ow = t.get("highway", ""), t.get("oneway", "")
            fwd = rev = True
            if ow in ONEWAY_TRUE or t.get("junction") == "roundabout" or (hw.startswith("motorway") and ow != "no"):
                rev = False
            elif ow == "-1":
                fwd = False
            ns = [n for n in e["nodes"] if n in self.node]
            for a, b in zip(ns, ns[1:]):
                L = hav(*self.node[a], *self.node[b])
                if fwd:
                    self.adj[a].append((b, L))
                if rev:
                    self.adj[b].append((a, L))
        self.has = {(u, v) for u in self.adj for v, _ in self.adj[u]}
        self.root = Path(root)
        with open(self.root / "network/stops.csv", encoding="utf-8", newline="") as f:
            self.stops = {r["stop_id"]: (float(r["stop_lat"]), float(r["stop_lon"])) for r in csv.DictReader(f)}
        grid = defaultdict(list)
        for n in self.adj:
            la, lo = self.node[n]
            grid[(int(la / 0.001), int(lo / 0.001))].append(n)
        self.snap: dict[str, int] = {}
        for sid, (la, lo) in sorted(self.stops.items()):
            best = None
            gi, gj = int(la / 0.001), int(lo / 0.001)
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    for n in grid.get((gi + di, gj + dj), ()):
                        d = hav(la, lo, *self.node[n])
                        if best is None or (d, n) < best:
                            best = (d, n)
            if best and best[0] <= snap_m:
                self.snap[sid] = best[1]
        self.tt = json.loads((self.root / "network/travel_times.json").read_text(encoding="utf-8"))
        self.bins = [b for b, _ in self.tt["segments"][0]["profile"]]
        self.seg_path: dict[tuple[str, str], list[int]] = {}
        self.busg: dict[int, list[tuple[int, float]]] = defaultdict(list)
        self.stats: dict = {}

    # ------------------------------------------------------------ 최단경로
    @staticmethod
    def dijkstra(edges, src, cutoff, target=None):
        dist, prev, h = {src: 0.0}, {}, [(0.0, src)]
        while h:
            d, u = heapq.heappop(h)
            if d > dist.get(u, 1e18):
                continue
            if u == target:
                break
            for v, L in edges.get(u, ()) if isinstance(edges, dict) else edges[u]:
                nd = d + L
                if nd <= cutoff and (nd < dist.get(v, 1e18) or (nd == dist.get(v) and u < prev.get(v, u))):
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(h, (nd, v))
        return dist, prev

    @staticmethod
    def walk(prev, src, dst):
        p = [dst]
        while p[-1] != src:
            p.append(prev[p[-1]])
        return p[::-1]

    def coords(self, nodes: list[int]) -> list[list[float]]:
        return [[round(self.node[n][1], 6), round(self.node[n][0], 6)] for n in nodes]

    # ------------------------------------------------------------ 만들기
    def build(self, cut_m: float = 900.0) -> dict:
        samples = defaultdict(lambda: defaultdict(list))
        bus_edge = set()
        matched = unmatched = 0
        for s in self.tt["segments"]:
            a, b = s["from_stop_id"], s["to_stop_id"]
            if a not in self.snap or b not in self.snap or self.snap[a] == self.snap[b]:
                unmatched += 1
                continue
            crow = hav(*self.stops[a], *self.stops[b])
            dist, prev = self.dijkstra(self.adj, self.snap[a], max(2 * crow, crow + 400), self.snap[b])
            if self.snap[b] not in dist:
                unmatched += 1
                continue
            matched += 1
            p = self.walk(prev, self.snap[a], self.snap[b])
            self.seg_path[(a, b)] = p
            L = dist[self.snap[b]]
            for u, v in zip(p, p[1:]):
                bus_edge.add((u, v))
                for i, (_, t) in enumerate(s["profile"]):
                    if t > 0:
                        samples[(u, v)][i].append(L / t)
        bus_set = set()
        for (u, v) in sorted(bus_edge):
            for (x, y) in ((u, v), (v, u)):
                if (x, y) in self.has and (x, y) not in bus_set:
                    bus_set.add((x, y))
                    self.busg[x].append((y, hav(*self.node[x], *self.node[y])))
        self.bus_set = bus_set
        allsp = defaultdict(list)
        for e in samples.values():
            for i, xs in e.items():
                allsp[i].extend(xs)
        med = {i: st.median(allsp[i]) for i in range(len(self.bins))}

        def speed(u, v, i):
            xs = samples.get((u, v), {}).get(i) or samples.get((v, u), {}).get(i)
            return st.median(xs) if xs else med[i]

        stop_nodes = defaultdict(set)
        for sid, n in self.snap.items():
            stop_nodes[n].add(sid)
        have = {(s["from_stop_id"], s["to_stop_id"]) for s in self.tt["segments"]}
        add, sources = [], []
        for a in sorted(self.snap):
            src = self.snap[a]
            if src not in self.busg:
                continue
            dist, prev = self.dijkstra(self.busg, src, cut_m)
            for n in sorted(dist):
                if n == src or n not in stop_nodes:
                    continue
                p = self.walk(prev, src, n)
                if any(m in stop_nodes for m in p[1:-1]):
                    continue
                for b in sorted(stop_nodes[n]):
                    if b == a or (a, b) in have:
                        continue
                    prof = []
                    for i, t0 in enumerate(self.bins):
                        t = sum(hav(*self.node[u], *self.node[v]) / speed(u, v, i) for u, v in zip(p, p[1:]))
                        prof.append([t0, max(1, round(t))])
                    add.append({"from_stop_id": a, "to_stop_id": b, "distance_m": max(1, round(dist[n])), "profile": prof})
                    self.seg_path[(a, b)] = p
                    sources.append((a, b))
                    have.add((a, b))
        out = dict(self.tt)
        out["segments"] = sorted(self.tt["segments"] + add, key=lambda s: (s["from_stop_id"], s["to_stop_id"]))
        self.road_doc = out
        self.added = sources
        self.stats = {"road_nodes": len(self.adj), "stops": len(self.stops), "stops_snapped": len(self.snap),
                      "bus_segments": len(self.tt["segments"]), "bus_segments_matched": matched,
                      "bus_segments_unmatched": unmatched, "bus_road_edges": len(bus_set), "added_segments": len(add),
                      "median_bus_speed_kmh": {str(self.bins[i]): round(3.6 * med[i], 1) for i in med}}
        return self.stats

    def path_any(self, a: str, b: str, bus_only: bool = False) -> list[list[float]]:
        """정류장 a→b의 도로 모양. 저장된 구간 경로가 있으면 그것, 없으면 도로 최단경로, 그래도 없으면 직선."""
        if (a, b) in self.seg_path:
            return self.coords(self.seg_path[(a, b)])
        if a in self.snap and b in self.snap and self.snap[a] != self.snap[b]:
            crow = hav(*self.stops[a], *self.stops[b])
            g = self.busg if bus_only else self.adj
            dist, prev = self.dijkstra(g, self.snap[a], max(2 * crow, crow + 400), self.snap[b])
            if self.snap[b] in dist:
                return self.coords(self.walk(prev, self.snap[a], self.snap[b]))
        return [[round(self.stops[a][1], 6), round(self.stops[a][0], 6)], [round(self.stops[b][1], 6), round(self.stops[b][0], 6)]]

    def bus_roads_geojson(self) -> dict:
        seen, lines = set(), []
        for (u, v) in sorted(self.bus_set):
            k = (min(u, v), max(u, v))
            if k in seen:
                continue
            seen.add(k)
            lines.append(self.coords([u, v]))
        return {"type": "Feature", "properties": {"layer": "bus_roads", "edges": len(lines)},
                "geometry": {"type": "MultiLineString", "coordinates": lines}}
