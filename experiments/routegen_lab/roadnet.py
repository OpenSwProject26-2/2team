"""실제 도로망(OSM)으로 '버스가 다니는 도로 = 1' 망을 만든다(실험용).

U턴 원칙: U턴을 막는 이유는 "도로가 좁아 버스가 실제로 돌 수 없어서"다. 그래서 정류장이나 길 중간에서 제자리로 돌아
나오는 것(방금 지난 도로 조각을 반대로 되짚기, 길 건너편 정차 노드로 바꿔 타기)은 막고, 블록·순환로를 돌아 반대쪽으로
가는 것(지난 도로를 되짚지 않음)은 허용한다. 경로 탐색과 turns-v2 판정(graph.Graph.is_uturn)이 모두 이 원칙을 따른다.

1. OSM 도로를 방향 그래프로 만든다(일방통행 반영). U턴(방금 지나온 도로 조각으로 되돌아가기)은 모든 경로에서 금지한다.
   회전 제한 관계(turn restriction)는 반영하지 않는다.
2. 정류장을 도로에 붙인다(`snap_mode`).
   - "edge"(기본): 정류장을 60m 안의 가장 가까운 도로 선분(OSM 노드 쌍)에 수직 투영하고, 그 자리에 가상 정차 노드를 끼운다.
     양방향 도로에서는 그 정류장을 쓰는 진행 방향의 유향 간선에만 정차 노드를 둔다(맞은편 정류장은 다른 노드가 된다).
     진행 방향 근거: (a) 그 정류장의 기존 버스 구간(travel_times의 p→s, s→q)이 가리키는 방위,
     (b) 근거가 없으면 우측 통행(진행 방향 오른쪽에 정류장), (c) 근거가 엇갈리거나(회차·종점) 판단이 안 되면 양방향 모두.
     기존 버스 구간 방위와 맞는 선분이 가장 가까운 선분보다 `align_slack_m` 안에 있으면 그 선분을 고른다(교차로·곁길 대책).
     다듬기(`_refine`, 기존 버스 구간을 U턴 없는 도로 경로로 잇는 배치로 고침)는 기존 버스 구간 방위와 반대 방향인 일방
     선분(반대 표 > 같은 방향 표)으로는 정류장을 옮기지 않는다(`refine_oneway_guard`, 분리 차로의 반대 차로로 옮겨
     직진 노선이 되짚기가 되는 것 방지).
     정류장을 둘 수 없는 도로(`no_stop_highways`, 기본 고속도로)에는 붙이지 않는다.
   - "node": 예전 방식(60m 안의 가장 가까운 도로 노드). 옛 망 재현·비교용.
3. 기존 버스 구간 A→B마다 도로 위 최단경로를 찾는다(길이 ≤ max(2×직선, 직선+400m)). 그 도로에 1을 표시하고,
   구간 실측 시간 ÷ 경로 길이로 시간대별 속도 표본을 남긴다.
   - 상한 안에 U턴 없는 경로가 없으면(edge 망) 더 큰 상한 max(`long_factor`×직선, 직선+`long_extra_m`)(기본 4×, 1,500m)으로
     U턴 없는 경로를 다시 찾는다("긴 경로 매칭", `long_segments`). 찾으면 도로 조각 기록(turns)만 남긴다. 구간 시간·
     `distance_m`은 실측 그대로 두고, 속도 표본·버스 도로(1)에는 넣지 않는다(실제 버스가 다니지 않을 수 있는 긴 길이
     버스 도로가 되어 추가 구간이 생기지 않게). OSM 연결·회전 정보가 실제와 다른 곳이 많아, 도로 경로 속도
     (`road_speed_kmh` = 경로 길이 ÷ 실측 최소 시간)를 함께 기록한다. 이 속도가 `long_max_kmh`(기본 60km/h)를 넘으면
     실측 시간 안에 그 길로 갈 수 없으니 실제 경로가 아니다: 긴 경로 매칭을 버리고 "판단하지 않음"(끝까지 없음과 같은
     처리, 조각 기록 없음, 도중 U턴 경로도 찾지 않음, 사유 `long_too_fast`, `long_rejected` 목록)으로 둔다.
   - 그래도 없을 때만 도중 U턴 1회 허용 경로(기존 상한)로 잇는다(`uturn_segments`). 원칙상 도중 U턴은 실제로 불가능하므로
     따로 센다. 끝까지 못 찾으면 조각 기록 없이 둔다(그 구간이 낀 3정류장은 판단하지 않음).
4. 버스 도로 = 1인 도로 + 양방향 도로면 그 반대 방향.
5. 정류장 a에서 버스 도로로 `cut_m` 안에 닿는 정류장 b마다, 경로 중간에 다른 정류장이 없으면 a→b 구간을 더한다.
   시간 = Σ 도로 길이 ÷ 그 도로의 버스 실측 속도(시간대별, 없으면 전체 중앙값). 이미 있는 구간은 그대로 둔다.

새 구간은 실측이 아니라 추정이다(`added`에 따로 남긴다).
구간마다 도로 조각 기록(`turn_edges`)을 남겨, 정류장에서 두 구간을 이을 때 U턴이 되는지 판정할 수 있게 한다.
- "edge" 망: 형식 turns-v2. 구간 "a>b" → {"s": 출발 정차 노드, "e": 도착 정차 노드,
  "tail": 도착 전 `tail_m`(기본 300m) 안에서 지난 도로 조각(OSM 유향 간선) 목록, "head": 출발 뒤 `head_m` 안의 도로 조각
  목록(기본 None = 구간 전체)}. 도로 경로를 U턴 1회 허용으로만 찾은 기존 버스 구간은 "u": 1, 긴 경로 매칭 구간은 "l": 1.
  판정(graph.Graph.is_uturn): b에서 도착 노드 ≠ 출발 노드(길 건너편으로 바꿔 탐)이거나, b→c 경로가 a→b의 도착 전
  tail_m 안 도로를 반대 방향으로 지나면(되짚기) U턴이다. 되짚는 도로는 정류장 b에서 tail_m 안에 있다.
- "node" 망: 예전 형식(구간 → [첫 조각 u, v, 마지막 조각 u, v]).
기존 버스 구간의 `distance_m`도 매칭한 도로 길이로 바꾼다(매칭 못 한 구간·긴 경로 매칭 구간은 원래 값).
`write_net`은 망 파일(road.json, turns.json, roadnet_stats.json, snap.json)을 한 폴더에 쓴다.
`osm_link`(build 뒤)는 망 밖 이음(지자체안처럼 망에 직접 구간이 없는 연속 정차)을 원 OSM 도로 최단 경로(U턴만 금지,
버스 도로 여부 무관, 지나야 할 좌표 가능)로 재고, 시간은 그 도로 길이 ÷ 버스 속도(`edge_speed`)로 낸다.
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
VBASE = 10 ** 15          # 가상 정차 노드 id(OSM id와 겹치지 않게 큰 수부터)
WBASE = 2 * 10 ** 15      # 도보 그래프의 정류장 투영점 id
KM = math.pi / 180 * 6371008.8   # 위도 1도의 길이(m)
TURNS_V2 = "turns-v2"


def hav(a1: float, o1: float, a2: float, o2: float) -> float:
    r = 6371008.8
    p1, p2 = math.radians(a1), math.radians(a2)
    dp, dl = p2 - p1, math.radians(o2 - o1)
    return 2 * r * math.asin(math.sqrt(math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2))


def _project(la: float, lo: float, a: tuple[float, float], b: tuple[float, float]):
    """정류장 (la, lo)를 선분 a→b에 수직 투영. (거리 m, t∈[0,1], 부호 있는 옆 거리 m(+ = a→b 진행 방향 오른쪽), 단위 방향)."""
    kx = math.cos(math.radians(la)) * KM
    ax, ay = (a[1] - lo) * kx, (a[0] - la) * KM
    bx, by = (b[1] - lo) * kx, (b[0] - la) * KM
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / L2))
    px, py = ax + t * dx, ay + t * dy
    d = math.hypot(px, py)
    n = math.sqrt(L2) or 1.0
    cross = dx * (-py) - dy * (-px)           # 정류장(원점) - 투영점 과 진행 방향의 외적. 음수 = 오른쪽
    return d, t, -cross / n, (dx / n, dy / n)


class RoadNet:
    def __init__(self, osm_path: Path, root: Path, snap_m: float = 60.0, snap_mode: str = "edge",
                 align_slack_m: float = 20.0, no_stop_highways: tuple[str, ...] = ("motorway", "motorway_link"),
                 center_m: float = 1.0, refine_m: float = 30.0, refine_gain_m: float = 30.0, refine_loop_m: float = 100.0, uturn_fallback: bool = True,
                 tail_m: float = 300.0, head_m: float | None = None,
                 long_match: bool = True, long_factor: float = 4.0, long_extra_m: float = 1500.0,
                 long_max_kmh: float | None = 60.0, refine_oneway_guard: bool = True):
        if snap_mode not in ("edge", "node"):
            raise ValueError(f"snap_mode는 edge 또는 node: {snap_mode}")
        self.snap_mode = snap_mode
        self.snap_m, self.align_slack_m, self.center_m, self.refine_m = snap_m, align_slack_m, center_m, refine_m
        self.no_stop_highways = tuple(no_stop_highways)
        self.tail_m, self.head_m = tail_m, head_m
        self.uturn_fallback = uturn_fallback
        self.long_match, self.long_factor, self.long_extra_m = long_match, long_factor, long_extra_m
        self.long_max_kmh = long_max_kmh
        self.refine_oneway_guard = refine_oneway_guard
        self.refine_gain_m, self.refine_loop_m = refine_gain_m, refine_loop_m
        osm = json.loads(Path(osm_path).read_text(encoding="utf-8"))
        self.node = {e["id"]: (e["lat"], e["lon"]) for e in osm["elements"] if e["type"] == "node"}
        self.adj: dict[int, list[tuple[int, float]]] = defaultdict(list)
        self.edge_hw: dict[tuple[int, int], str] = {}
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
                    self.edge_hw.setdefault((a, b), hw)
                if rev:
                    self.adj[b].append((a, L))
                    self.edge_hw.setdefault((b, a), hw)
        self.has = {(u, v) for u in self.adj for v, _ in self.adj[u]}
        self.base_has = set(self.has)               # OSM 유향 간선(가상 노드 끼우기 전)
        self.base_of: dict[tuple[int, int], tuple[int, int]] = {}   # 쪼갠 조각 → 원래 OSM 유향 간선
        self.root = Path(root)
        with open(self.root / "network/stops.csv", encoding="utf-8", newline="") as f:
            self.stops = {r["stop_id"]: (float(r["stop_lat"]), float(r["stop_lon"])) for r in csv.DictReader(f)}
        self.tt = json.loads((self.root / "network/travel_times.json").read_text(encoding="utf-8"))
        self.bins = [b for b, _ in self.tt["segments"][0]["profile"]] if self.tt["segments"] else []
        self.snap: dict[str, int] = {}               # 정류장 → 대표 정차 노드(호환용)
        self.stop_nodes: dict[str, tuple[int, ...]] = {}
        self.proj: dict[str, dict] = {}              # edge 방식의 투영 기록
        self.snap_stats: dict = {}
        if snap_mode == "node":
            self._snap_nodes(snap_m)
        else:
            self._snap_edges(snap_m)
        self.node_stops: dict[int, list[str]] = defaultdict(list)
        for sid, ns in sorted(self.stop_nodes.items()):
            for n in ns:
                self.node_stops[n].append(sid)
        self.unsnapped = sorted(s for s in self.stops if s not in self.stop_nodes)
        self.seg_path: dict[tuple[str, str], list[int]] = {}
        self.busg: dict[int, list[tuple[int, float]]] = defaultdict(list)
        self.stats: dict = {}

    # ------------------------------------------------------------ 정류장 붙이기
    def _snap_nodes(self, snap_m: float) -> None:
        """예전 방식: 60m 안의 가장 가까운 도로 노드."""
        grid = defaultdict(list)
        for n in self.adj:
            la, lo = self.node[n]
            grid[(int(la / 0.001), int(lo / 0.001))].append(n)
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
                self.stop_nodes[sid] = (best[1],)

    def _evidence(self) -> dict[str, list[tuple[float, float]]]:
        """정류장 → 기존 버스 구간(들어오는 p→s, 나가는 s→q)의 단위 방위 벡터(동, 북)."""
        ev: dict[str, list[tuple[float, float]]] = defaultdict(list)
        for s in self.tt["segments"]:
            a, b = s["from_stop_id"], s["to_stop_id"]
            if a not in self.stops or b not in self.stops or a == b:
                continue
            (la1, lo1), (la2, lo2) = self.stops[a], self.stops[b]
            dx, dy = (lo2 - lo1) * math.cos(math.radians((la1 + la2) / 2)) * KM, (la2 - la1) * KM
            n = math.hypot(dx, dy)
            if n < 1.0:
                continue
            ev[a].append((dx / n, dy / n))
            ev[b].append((dx / n, dy / n))
        return ev

    def _snap_edges(self, snap_m: float) -> None:
        """선분 투영 + 진행 방향별 정차 노드. 도로 그래프(self.adj)를 가상 노드를 끼운 그래프로 바꾼다.

        1. 첫 배치: 가장 가까운 선분(기존 버스 구간 방위와 맞는 선분이 align_slack_m 안에 있으면 그것), 방향은 (a)/(b)/(c).
        2. 다듬기(`refine_m` > 0, `_refine`): 기존 버스 구간이 U턴 없이 가장 짧게 이어지는 배치로 고친다(지도 매칭).
        3. 고른 배치대로 가상 정차 노드를 끼운다.
        """
        und: dict[tuple[int, int], float] = {}
        for (u, v) in self.base_has:
            und.setdefault((min(u, v), max(u, v)), hav(*self.node[u], *self.node[v]))
        self._und_len = und
        grid = defaultdict(list)
        for key in sorted(und):
            (a1, o1), (a2, o2) = self.node[key[0]], self.node[key[1]]
            for i in range(int(min(a1, a2) / 0.001), int(max(a1, a2) / 0.001) + 1):
                for j in range(int(min(o1, o2) / 0.001), int(max(o1, o2) / 0.001) + 1):
                    grid[(i, j)].append(key)
        ev = self._evidence()
        self._ev, self._grid = ev, grid
        hw_of = {k: self.edge_hw.get(k) or self.edge_hw.get((k[1], k[0]), "") for k in und}
        self._hw_of = hw_of
        alt = oneway_conflict = 0
        self._cands: dict[str, list] = {}
        for sid, (la, lo) in sorted(self.stops.items()):
            gi, gj = int(la / 0.001), int(lo / 0.001)
            seen, cands = set(), []
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    for key in grid.get((gi + di, gj + dj), ()):
                        if key in seen:
                            continue
                        seen.add(key)
                        if hw_of[key] in self.no_stop_highways:
                            continue
                        d, t, side, dvec = _project(la, lo, self.node[key[0]], self.node[key[1]])
                        if d <= snap_m:
                            cands.append((round(d, 6), key, t, side, dvec))
            if not cands:
                continue
            cands.sort(key=lambda c: (c[0], c[1]))
            self._cands[sid] = cands
            evs = ev.get(sid, [])

            def votes(dvec):
                f = sum(1 for e in evs if e[0] * dvec[0] + e[1] * dvec[1] >= 0.5)
                b = sum(1 for e in evs if e[0] * dvec[0] + e[1] * dvec[1] <= -0.5)
                return f, b

            def supported(c):
                _, key, _, _, dvec = c
                f, b = votes(dvec)
                return any((f if x == key else b) > 0 for x in self._dirs_of(key))

            pick = cands[0]
            if evs and not supported(pick):
                for c in cands[1:]:
                    if c[0] > cands[0][0] + self.align_slack_m:
                        break
                    if supported(c):
                        pick, alt = c, alt + 1
                        break
            d, key, t, side, dvec = pick
            U, V = key
            ds = self._dirs_of(key)
            f, b = votes(dvec)
            if len(ds) == 1:
                how = "oneway"
                if (ds[0] == key and b and not f) or (ds[0] != key and f and not b):
                    oneway_conflict += 1
            elif f and not b:
                ds, how = [(U, V)], "a"
            elif b and not f:
                ds, how = [(V, U)], "a"
            elif f and b:
                how = "c_conflict"
            elif abs(side) < self.center_m:
                how = "c_center"
            else:
                ds, how = ([(U, V)] if side > 0 else [(V, U)]), "b"
            self._place(sid, pick, ds, how, pick is cands[0])
        refined = self._refine() if self.refine_m > 0 else {}
        self._split_graph()
        how_n: dict[str, int] = defaultdict(int)
        for p in self.proj.values():
            how_n[p["how"]] += 1
        self.snap_stats = {"how": dict(sorted(how_n.items())), "aligned_not_nearest": alt,
                           "oneway_conflict": oneway_conflict, "refined": refined,
                           "two_direction_stops": sum(1 for p in self.proj.values() if len(p["dirs"]) == 2)}

    def _dirs_of(self, key: tuple[int, int]) -> list[tuple[int, int]]:
        U, V = key
        return [x for x in ((U, V), (V, U)) if x in self.base_has]

    def _place(self, sid: str, cand, ds, how: str, nearest: bool, how0: str | None = None) -> None:
        d, key, t, side, _ = cand
        U, V = key
        (a1, o1), (a2, o2) = self.node[U], self.node[V]
        self.proj[sid] = {"edge": [U, V], "t": t, "offset_m": round(d, 2), "side": "right" if side > 0 else "left",
                          "how": how, "dirs": [list(x) for x in ds], "highway": self._hw_of[key],
                          "nearest": nearest, "lat": a1 + t * (a2 - a1), "lon": o1 + t * (o2 - o1)}
        if how0:
            self.proj[sid]["how0"] = how0

    def _positions(self, sid: str) -> list[tuple[int, int, float, float]]:
        p = self.proj[sid]
        key = tuple(p["edge"])
        L = self._und_len[key]
        return [(x[0], x[1], p["t"] if tuple(x) == key else 1.0 - p["t"], L) for x in p["dirs"]]

    def _pos_route(self, A, B, cutoff: float) -> float | None:
        """가상 노드 없이 원래 OSM 그래프에서 위치(유향 간선 X→Y 위 t) A들 → B들 최단 거리(U턴 금지). 없으면 None."""
        best_total = math.inf
        tgt: dict[int, list[tuple[int, float]]] = defaultdict(list)
        for (X2, Y2, t2, L2) in B:
            tgt[X2].append((Y2, t2 * L2))
            for (X, Y, t, L) in A:
                if (X, Y) == (X2, Y2) and t2 >= t:
                    best_total = min(best_total, (t2 - t) * L)
        best: dict[tuple[int, int], float] = {}
        h = []
        for (X, Y, t, L) in A:
            d = (1.0 - t) * L
            if d < best.get((X, Y), math.inf):
                best[(X, Y)] = d
                heapq.heappush(h, (d, X, Y))
        while h:
            d, p, u = heapq.heappop(h)
            if d >= best_total:
                break
            if d > best[(p, u)]:
                continue
            for Y2, off in tgt.get(u, ()):
                if Y2 != p:
                    best_total = min(best_total, d + off)
            for v, L in self._base_adj.get(u, ()):
                if v == p:
                    continue
                nd = d + L
                if nd <= cutoff and nd < best.get((u, v), math.inf):
                    best[(u, v)] = nd
                    heapq.heappush(h, (nd, u, v))
        return best_total if best_total <= cutoff else None

    def _refine(self) -> dict:
        """기존 버스 구간으로 정류장 배치를 다듬는다(지도 매칭, 모듈 설명 2). 바꾼 수를 돌려준다.

        정류장마다 그 정류장의 기존 버스 구간(p→s, s→q, 상대 정류장 배치는 고정)을 U턴 없는 도로 경로로 이어 보고,
        (못 잇는 구간 수, 이은 구간 도로 길이 합)이 가장 작은 배치(가장 가까운 선분에서 refine_m 안의 선분 × 진행 방향)를 찾는다.
        - 한 방향 정류장: 못 잇는 구간이 줄거나, 같으면서 어떤 구간이 `refine_loop_m`(100m) 넘게 짧아지고 다른 구간은
          `refine_gain_m`(30m) 넘게 길어지지 않을 때만 바꾼다("refined"). 잘못된 쪽·곁길에 두면 버스 구간이 한 바퀴 돌아
          길어지므로 그 차이가 근거가 된다. 길이 합만 줄이면 정류장이 진행 방향 앞뒤로 끌려가므로(나가는 구간이 많으면 앞으로)
          합은 쓰지 않는다.
        - 양방향 정류장(근거가 엇갈림): 한 방향으로도 못 잇는 구간이 늘지 않고 구간마다 길이가 refine_gain_m 안이면
          그 한 방향으로 줄인다("c_resolved"). 아니면(회차·종점처럼 한 방향이면 돌아야 하는 곳) 양방향 그대로.
        2회 반복한다(앞에서 바꾼 이웃 배치를 다음 정류장 평가에 쓴다).
        """
        self._base_adj = self.adj
        inc: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for s in self.tt["segments"]:
            a, b = s["from_stop_id"], s["to_stop_id"]
            if a in self.proj and b in self.proj and a != b:
                inc[a].append((a, b))
                inc[b].append((a, b))
        cut = {}
        for v in inc.values():
            for ab in v:
                crow = hav(*self.stops[ab[0]], *self.stops[ab[1]])
                cut[ab] = max(2 * crow, crow + 400)

        def lens(sid):
            return [self._pos_route(self._positions(a), self._positions(b), cut[(a, b)]) for a, b in inc[sid]]

        def summ(ls):
            return sum(1 for x in ls if x is None), sum(x for x in ls if x is not None)

        g = self.refine_gain_m
        out = {"stops": 0, "road_changed": 0, "direction_changed": 0, "two_to_one": 0, "segments_fixed": 0,
               "oneway_against_skipped": 0}
        skipped_stops: set[str] = set()
        for _ in range(2):
            changed = 0
            for sid in sorted(inc):
                cur = self.proj[sid]
                cl = lens(sid)
                cm, ct = summ(cl)
                cands = self._cands[sid]
                opts = []
                for c in cands:
                    if c[0] > cands[0][0] + self.refine_m:
                        break
                    dl = self._dirs_of(c[1])
                    for x in dl:
                        if self.refine_oneway_guard and len(dl) == 1 and self._against(sid, c, x):
                            skipped_stops.add(sid)     # 기존 버스 구간 방위와 반대인 일방 선분으로는 옮기지 않는다
                            continue
                        self._place(sid, c, [x], "refined", c is cands[0])
                        ls = lens(sid)
                        m, t = summ(ls)
                        opts.append((m, t, c[0], c[1], x, c, ls))
                self.proj[sid] = cur
                if not opts:
                    continue
                def mark(new_how):
                    p = self.proj[sid]
                    p["moved_m"] = round(hav(cur["lat"], cur["lon"], p["lat"], p["lon"]), 1)
                    (a1, o1), (a2, o2) = self.node[cur["dirs"][0][0]], self.node[cur["dirs"][0][1]]
                    (b1, q1), (b2, q2) = self.node[p["dirs"][0][0]], self.node[p["dirs"][0][1]]
                    k = math.cos(math.radians(a1))
                    dot = (o2 - o1) * k * (q2 - q1) * k + (a2 - a1) * (b2 - b1)
                    p["flipped"] = len(cur["dirs"]) == 1 and dot < 0
                if len(cur["dirs"]) == 2:
                    fit = [o for o in opts if o[0] <= cm and all(
                        y is None and x is None or (y is not None and x is not None and y <= x + g) or (x is None)
                        for x, y in zip(cl, o[6]))]
                    if not fit:
                        continue
                    m, t, _, _, x, c, _ = min(fit, key=lambda o: o[:4])
                    self._place(sid, c, [x], "c_resolved", c is cands[0], how0=cur.get("how0", cur["how"]))
                    mark("c_resolved")
                    out["two_to_one"] += 1
                else:
                    def better(o):
                        if o[0] < cm:
                            return True
                        if o[0] > cm:
                            return False
                        diffs = [(x - y) for x, y in zip(cl, o[6]) if x is not None and y is not None]
                        return bool(diffs) and max(diffs) >= self.refine_loop_m and min(diffs) >= -g
                    fit = [o for o in opts if better(o)]
                    if not fit:
                        continue
                    m, t, _, _, x, c, _ = min(fit, key=lambda o: o[:4])
                    if list(c[1]) == cur["edge"] and [list(x)] == cur["dirs"]:
                        continue
                    self._place(sid, c, [x], "refined", c is cands[0], how0=cur.get("how0", cur["how"]))
                    mark("refined")
                    out["road_changed"] += int(list(c[1]) != cur["edge"])
                    out["direction_changed"] += int(self.proj[sid]["flipped"])
                out["stops"] += 1
                out["segments_fixed"] += cm - m
                changed += 1
            if not changed:
                break
        out["oneway_against_skipped"] = len(skipped_stops)
        self.refine_guarded = sorted(skipped_stops)
        return out

    def _against(self, sid: str, cand, x: tuple[int, int]) -> bool:
        """선분 cand의 진행 방향 x가 그 정류장의 기존 버스 구간 방위와 반대인가(반대 표 > 같은 방향 표, |cos| ≥ 0.5만 셈)."""
        _, key, _, _, dvec = cand
        v = dvec if tuple(x) == tuple(key) else (-dvec[0], -dvec[1])
        evs = self._ev.get(sid, [])
        f = sum(1 for e in evs if e[0] * v[0] + e[1] * v[1] >= 0.5)
        b = sum(1 for e in evs if e[0] * v[0] + e[1] * v[1] <= -0.5)
        return b > f

    def _split_graph(self) -> None:
        """고른 배치대로 가상 정차 노드를 유향 간선에 끼운다."""
        on_edge: dict[tuple[int, int], list[tuple[float, str]]] = defaultdict(list)
        for sid in sorted(self.proj):
            for (X, Y, t, _) in self._positions(sid):
                on_edge[(X, Y)].append((t, sid))
        nid = VBASE
        vnode: dict[tuple[str, tuple[int, int]], int] = {}
        for sid in sorted(self.proj):
            for x in self.proj[sid]["dirs"]:
                vnode[(sid, tuple(x))] = nid
                self.node[nid] = (self.proj[sid]["lat"], self.proj[sid]["lon"])
                nid += 1
        adj: dict[int, list[tuple[int, float]]] = defaultdict(list)
        self.sub_edges: dict[tuple[int, int], list[tuple[int, int, float]]] = {}
        done = set()
        for u in list(self.adj):
            for v, L in self.adj[u]:
                if (u, v) in done:
                    continue                        # 같은 노드 쌍을 잇는 OSM way가 둘이면 한 번만
                done.add((u, v))
                pts = sorted(on_edge.get((u, v), []))
                if not pts:
                    adj[u].append((v, L))
                    self.sub_edges[(u, v)] = [(u, v, L)]
                    continue
                chain = [(0.0, u)] + [(tt, vnode[(sid, (u, v))]) for tt, sid in pts] + [(1.0, v)]
                subs = []
                for (t1, x), (t2, y) in zip(chain, chain[1:]):
                    adj[x].append((y, L * (t2 - t1)))
                    self.base_of[(x, y)] = (u, v)
                    subs.append((x, y, L * (t2 - t1)))
                self.sub_edges[(u, v)] = subs
        self.adj = adj
        self.has = {(u, v) for u in self.adj for v, _ in self.adj[u]}
        for sid in sorted(self.proj):
            ns = tuple(vnode[(sid, tuple(x))] for x in self.proj[sid]["dirs"])
            self.stop_nodes[sid] = ns
            self.snap[sid] = ns[0]

    def base(self, u: int, v: int) -> tuple[int, int]:
        return self.base_of.get((u, v), (u, v))

    # ------------------------------------------------------------ 최단경로
    def dijkstra(self, edges, srcs, cutoff, targets=None):
        """U턴 없는 최단경로. 상태 = (직전 노드, 현재 노드). 다음 조각이 방금 지나온 OSM 간선의 반대 방향이면 막는다.

        srcs = 출발 노드(하나 또는 여러 개), targets = 도착 노드 집합(하나라도 꺼내면 멈춘다).
        돌려주는 dist[node] = 그 노드까지 최단 거리, prev[node] = 그 최단 상태의 경로 복원 정보.
        """
        if isinstance(srcs, int):
            srcs = (srcs,)
        if targets is not None and isinstance(targets, int):
            targets = {targets}
        best, back, dist, prev, h = {}, {}, {}, {}, []
        for s in srcs:
            start = (None, s)
            best[start] = 0.0
            back[start] = None
            dist[s], prev[s] = 0.0, start
            h.append((0.0, -1, s))
        heapq.heapify(h)
        bo = self.base_of
        while h:
            d, pu, u = heapq.heappop(h)
            state = (None if pu == -1 else pu, u)
            if d > best.get(state, 1e18):
                continue
            if u not in dist or d < dist[u]:
                dist[u], prev[u] = d, state
            if targets is not None and u in targets:
                break
            if state[0] is not None:
                bx, by = bo.get((state[0], u), (state[0], u))
                rev = (by, bx)
            else:
                rev = None
            for v, L in edges.get(u, ()) if isinstance(edges, dict) else edges[u]:
                if rev is not None and bo.get((u, v), (u, v)) == rev:
                    continue                                   # U턴 금지
                nd = d + L
                ns = (u, v)
                if nd <= cutoff and nd < best.get(ns, 1e18):
                    best[ns] = nd
                    back[ns] = state
                    heapq.heappush(h, (nd, u, v))
        return dist, (prev, back)

    def path_one_uturn(self, srcs, targets, cutoff):
        """U턴을 한 번까지 허용한 최단경로(도로 경로를 못 찾은 기존 버스 구간용). (길이, 노드 열) 또는 None.

        실제 버스가 구간 도중에 돌아 나오는 곳(길 건너편 정류장으로 가는 회차, 막다른 길 종점 등)을 잇는다.
        """
        bo = self.base_of
        best, back, h = {}, {}, []
        for s in srcs:
            st0 = (None, s, 0)
            best[st0], back[st0] = 0.0, None
            h.append((0.0, 0, -1, s))
        heapq.heapify(h)
        while h:
            d, k, pu, u = heapq.heappop(h)
            state = (None if pu == -1 else pu, u, k)
            if d > best.get(state, 1e18):
                continue
            if u in targets:
                p = []
                while state is not None:
                    p.append(state[1])
                    state = back[state]
                return d, p[::-1]
            if state[0] is not None:
                bx, by = bo.get((state[0], u), (state[0], u))
                rev = (by, bx)
            else:
                rev = None
            for v, L in self.adj.get(u, ()):
                kk = k + (1 if rev is not None and bo.get((u, v), (u, v)) == rev else 0)
                if kk > 1:
                    continue
                nd = d + L
                ns = (u, v, kk)
                if nd <= cutoff and nd < best.get(ns, 1e18):
                    best[ns] = nd
                    back[ns] = state
                    heapq.heappush(h, (nd, kk, u, v))
        return None

    @staticmethod
    def walk(prev, src, dst):
        last, back = prev
        st = last[dst]
        p = []
        while st is not None:
            p.append(st[1])
            st = back[st]
        return p[::-1]

    def coords(self, nodes: list[int]) -> list[list[float]]:
        return [[round(self.node[n][1], 6), round(self.node[n][0], 6)] for n in nodes]

    # ------------------------------------------------------------ 만들기
    def long_cutoff(self, crow: float) -> float:
        """긴 경로 매칭 거리 상한 = max(long_factor×직선, 직선+long_extra_m)."""
        return max(self.long_factor * crow, crow + self.long_extra_m)

    @staticmethod
    def _seg_info(seg: dict, crow: float, cutoff: float, L: float) -> dict:
        """긴 경로·도중 U턴 매칭 구간의 기록: 직선·상한·도로 경로 길이, 실측 최소 시간으로 본 도로 경로 속도(km/h)."""
        ts = [t for _, t in seg["profile"] if t > 0]
        return {"crow_m": round(crow), "cutoff_m": round(cutoff), "road_m": round(L), "distance_m": seg["distance_m"],
                "min_time_s": min(ts) if ts else None,
                "road_speed_kmh": round(3.6 * L / min(ts), 1) if ts else None}

    def build(self, cut_m: float = 900.0) -> dict:
        samples = defaultdict(lambda: defaultdict(list))
        bus_edge = set()
        self.road_len: dict[tuple[str, str], int] = {}
        matched = 0
        unmatched: dict[str, int] = defaultdict(int)
        self.unmatched_segments: list[dict] = []
        self.uturn_segments: set[tuple[str, str]] = set()
        self.uturn_info: dict[tuple[str, str], dict] = {}
        self.long_segments: dict[tuple[str, str], dict] = {}
        self.long_rejected: dict[tuple[str, str], dict] = {}
        SN = self.stop_nodes
        for s in self.tt["segments"]:
            a, b = s["from_stop_id"], s["to_stop_id"]
            why = None
            if a not in SN or b not in SN:
                why = "unsnapped"
            elif set(SN[a]) & set(SN[b]):
                why = "same_node"
            if why is None:
                crow = hav(*self.stops[a], *self.stops[b])
                tg = set(SN[b])
                dist, prev = self.dijkstra(self.adj, SN[a], max(2 * crow, crow + 400), tg)
                hit = [(dist[x], x) for x in sorted(tg) if x in dist]
                if not hit:
                    why = "no_path"
                    if self.snap_mode == "edge" and self.long_match:
                        lcut = self.long_cutoff(crow)
                        dist, prev = self.dijkstra(self.adj, SN[a], lcut, tg)
                        lhit = [(dist[x], x) for x in sorted(tg) if x in dist]
                        if lhit:
                            # 긴 경로 매칭: 조각 기록(turns)만 남긴다. 구간 시간·distance_m은 실측 그대로,
                            # 속도 표본·버스 도로(bus_set)에는 넣지 않는다(실제 버스가 안 다니는 길에 추가 구간이 생기지 않게).
                            L, end = min(lhit)
                            info = self._seg_info(s, crow, lcut, L)
                            sp = info["road_speed_kmh"]
                            if self.long_max_kmh is not None and sp is not None and sp > self.long_max_kmh:
                                # 실측 최소 시간으로 그 길을 갈 수 없다(도로 경로 속도 > long_max_kmh) → 실제 경로가 아니다.
                                # 판단하지 않음(끝까지 없음과 같은 처리, 조각 기록 없음, 도중 U턴 경로도 찾지 않음).
                                why = "long_too_fast"
                                self.long_rejected[(a, b)] = info
                            else:
                                p = self.walk(prev, None, end)
                                self.long_segments[(a, b)] = info
                                self.seg_path[(a, b)] = p
                                continue
                    if why == "no_path" and self.snap_mode == "edge" and self.uturn_fallback:
                        got = self.path_one_uturn(SN[a], tg, max(2 * crow, crow + 400))
                        if got:
                            # 도중 U턴 1회 경로. 원칙상 도로가 좁아 실제로는 돌 수 없는 곳이므로 따로 센다.
                            L, p = got
                            self.uturn_segments.add((a, b))
                            self.uturn_info[(a, b)] = self._seg_info(s, crow, max(2 * crow, crow + 400), L)
                            self.seg_path[(a, b)] = p
                            self.road_len[(a, b)] = max(1, round(L))
                            bus_edge.update(zip(p, p[1:]))        # 속도 표본에는 넣지 않는다
                            continue
            if why:
                unmatched[why] += 1
                u = {"from": a, "to": b, "why": why}
                if why in ("no_path", "long_too_fast"):
                    u["crow_m"] = round(crow)
                self.unmatched_segments.append(u)
                continue
            matched += 1
            L, end = min(hit)
            p = self.walk(prev, None, end)
            self.seg_path[(a, b)] = p
            self.road_len[(a, b)] = max(1, round(L))
            for u, v in zip(p, p[1:]):
                bus_edge.add((u, v))
                for i, (_, t) in enumerate(s["profile"]):
                    if t > 0:
                        samples[self.base(u, v)][i].append(L / t)
        bus_set = set()
        if self.snap_mode == "node":
            for (u, v) in sorted(bus_edge):
                for (x, y) in ((u, v), (v, u)):
                    if (x, y) in self.has and (x, y) not in bus_set:
                        bus_set.add((x, y))
                        self.busg[x].append((y, hav(*self.node[x], *self.node[y])))
        else:
            for (U, V) in sorted({self.base(u, v) for u, v in bus_edge}):
                for X in ((U, V), (V, U)):
                    if X in self.base_has and X not in bus_set:
                        bus_set.add(X)
                        for x, y, L in self.sub_edges[X]:
                            self.busg[x].append((y, L))
        self.bus_set = bus_set
        allsp = defaultdict(list)
        for e in samples.values():
            for i, xs in e.items():
                allsp[i].extend(xs)
        med = {i: st.median(allsp[i]) for i in range(len(self.bins))}
        self._samples, self._med = samples, med
        speed, seglen = self.edge_speed, self.seglen

        stop_nodes = self.node_stops
        have = {(s["from_stop_id"], s["to_stop_id"]) for s in self.tt["segments"]}
        add, sources = [], []
        for a in sorted(SN):
            found: dict[str, tuple[float, list[int]]] = {}
            for src in SN[a]:
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
                        if b not in found or dist[n] < found[b][0]:
                            found[b] = (dist[n], p)
            for b in sorted(found):
                dn, p = found[b]
                prof = []
                for i, t0 in enumerate(self.bins):
                    t = sum(seglen(u, v) / speed(u, v, i) for u, v in zip(p, p[1:]))
                    prof.append([t0, max(1, round(t))])
                add.append({"from_stop_id": a, "to_stop_id": b, "distance_m": max(1, round(dn)), "profile": prof})
                self.seg_path[(a, b)] = p
                sources.append((a, b))
                have.add((a, b))
        orig = [dict(s, distance_m=self.road_len.get((s["from_stop_id"], s["to_stop_id"]), s["distance_m"]))
                for s in self.tt["segments"]]
        out = dict(self.tt)
        out["segments"] = sorted(orig + add, key=lambda s: (s["from_stop_id"], s["to_stop_id"]))
        self.road_doc = out
        self.orig_doc = dict(self.tt, segments=sorted(orig, key=lambda s: (s["from_stop_id"], s["to_stop_id"])))
        self.added = sources
        self.stats = {"road_nodes": len(self.adj), "stops": len(self.stops), "stops_snapped": len(self.stop_nodes),
                      "bus_segments": len(self.tt["segments"]), "bus_segments_matched": matched,
                      "bus_segments_unmatched": sum(unmatched.values()), "bus_road_edges": len(bus_set),
                      "added_segments": len(add),
                      "median_bus_speed_kmh": {str(self.bins[i]): round(3.6 * med[i], 1) for i in med}}
        if self.snap_mode == "edge":
            self.stats.update({"snap_mode": "edge", "unmatched_by_reason": dict(sorted(unmatched.items())),
                               "bus_segments_matched_with_uturn": len(self.uturn_segments),
                               "bus_segments_long_match": len(self.long_segments),
                               "long_match_rule": {"enabled": self.long_match, "factor": self.long_factor,
                                                   "extra_m": self.long_extra_m, "max_road_speed_kmh": self.long_max_kmh},
                               "bus_segments_long_rejected": len(self.long_rejected),
                               "long_rejected": [dict(v, **{"from": a, "to": b}) for (a, b), v in sorted(self.long_rejected.items())],
                               "long_match": [dict(v, **{"from": a, "to": b}) for (a, b), v in sorted(self.long_segments.items())],
                               "uturn_match": [dict(v, **{"from": a, "to": b}) for (a, b), v in sorted(self.uturn_info.items())],
                               "unmatched": list(self.unmatched_segments),
                               "snap": self.snap_stats, "turns_format": TURNS_V2,
                               "tail_m": self.tail_m, "head_m": self.head_m})
        return self.stats

    def edge_speed(self, u: int, v: int, i: int) -> float:
        """조각 u→v의 i번째 시간대 버스 속도(m/s): 그 도로(양방향 합침)의 실측 표본 중앙값, 없으면 그 시간대 전체 중앙값."""
        U, V = self.base(u, v)
        xs = self._samples.get((U, V), {}).get(i) or self._samples.get((V, U), {}).get(i)
        return st.median(xs) if xs else self._med[i]

    def seglen(self, u: int, v: int) -> float:
        if self.snap_mode == "node":
            return hav(*self.node[u], *self.node[v])
        return next(L for y, L in self.adj[u] if y == v)

    def nearest_segment(self, la: float, lo: float, max_m: float | None = None):
        """(edge 망) 좌표에서 가장 가까운 정차 가능 도로 선분(무방향 키)과 거리. 없으면 None."""
        lim = self.snap_m if max_m is None else max_m
        gi, gj = int(la / 0.001), int(lo / 0.001)
        best = None
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for key in self._grid.get((gi + di, gj + dj), ()):
                    if self._hw_of[key] in self.no_stop_highways:
                        continue
                    d = _project(la, lo, self.node[key[0]], self.node[key[1]])[0]
                    if d <= lim and (best is None or (d, key) < best):
                        best = (d, key)
        return None if best is None else (best[1], best[0])

    def osm_link(self, a: str, b: str, via: list[tuple[float, float]] = (), cutoff: float | None = None) -> dict | None:
        """(edge 망, build 뒤) 정류장 a→b의 원 OSM 도로 최단 경로(U턴만 금지, 버스 도로 여부 무관).

        via = 지나야 하는 좌표 목록(순서대로). 각 좌표는 가장 가까운 정차 가능 선분에 투영하고, 경로가 그 선분을
        (어느 방향으로든) 지나면 그 좌표를 지난 것으로 본다(층 그래프 탐색). 시간대별 시간 = Σ 조각 길이 ÷ 그 도로의
        버스 속도(`edge_speed`: 실측 표본 중앙값, 없으면 그 시간대 전체 중앙값).
        돌려주는 것: {"length_m", "profile"(travel_times 형식 [[bin, s]]), "path"(노드 열), "via_segments", "via_offset_m"} 또는 None.
        """
        SN = self.stop_nodes
        if a not in SN or b not in SN:
            return None
        vs, voff = [], []
        for la, lo in via:
            got = self.nearest_segment(la, lo)
            if got is None:
                return None
            key, d = got
            vs.append({key, (key[1], key[0])})
            voff.append(round(d, 1))
        crow = hav(*self.stops[a], *self.stops[b])
        cut = cutoff if cutoff is not None else max(6 * crow, crow + 5000)
        K = len(vs)
        tg = set(SN[b])
        bo = self.base_of
        best, back, h = {}, {}, []
        for s0 in SN[a]:
            st0 = (0, None, s0)
            best[st0], back[st0] = 0.0, None
            h.append((0.0, 0, -1, s0))
        heapq.heapify(h)
        found = None
        while h:
            d, k, pu, u = heapq.heappop(h)
            state = (k, None if pu == -1 else pu, u)
            if d > best.get(state, 1e18):
                continue
            if k == K and u in tg:
                found = (d, state)
                break
            if state[1] is not None:
                bx, by = bo.get((state[1], u), (state[1], u))
                rev = (by, bx)
            else:
                rev = None
            for v, L in self.adj.get(u, ()):
                bv = bo.get((u, v), (u, v))
                if rev is not None and bv == rev:
                    continue                                   # U턴 금지
                kk = k + 1 if k < K and bv in vs[k] else k
                nd = d + L
                ns = (kk, u, v)
                if nd <= cut and nd < best.get(ns, 1e18):
                    best[ns], back[ns] = nd, state
                    heapq.heappush(h, (nd, kk, u, v))
        if found is None:
            return None
        L, state = found
        p = []
        while state is not None:
            p.append(state[2])
            state = back[state]
        p = p[::-1]
        prof = [[t0, max(1, round(sum(self.seglen(x, y) / self.edge_speed(x, y, i) for x, y in zip(p, p[1:]))))]
                for i, t0 in enumerate(self.bins)]
        return {"length_m": max(1, round(L)), "profile": prof, "path": p, "via_offset_m": voff}

    def _window(self, p: list[int], from_end: bool) -> list[list[int]]:
        """경로 p의 앞(또는 끝)에서 head_m(tail_m) 안에 걸친 OSM 유향 간선(중복 없이, 지나는 순서). None = 경로 전체."""
        lim = self.tail_m if from_end else self.head_m
        lim = math.inf if lim is None else lim
        pairs = list(zip(p, p[1:]))
        if from_end:
            pairs = pairs[::-1]
        out, acc = [], 0.0
        for u, v in pairs:
            if acc >= lim:
                break
            acc += next(L for y, L in self.adj[u] if y == v)
            b = list(self.base(u, v))
            if not out or out[-1] != b:
                out.append(b)
        return out[::-1] if from_end else out

    def turn_edges(self) -> dict:
        """U턴 판정용 구간 도로 조각 기록. edge 망 = turns-v2(모듈 설명), node 망 = 예전 형식."""
        out: dict = {}
        if self.snap_mode == "node":
            for (a, b), p in sorted(self.seg_path.items()):
                if len(p) >= 2:
                    out[f"{a}>{b}"] = [p[0], p[1], p[-2], p[-1]]
            return out
        out["_meta"] = {"format": TURNS_V2, "snap_mode": "edge", "tail_m": self.tail_m, "head_m": self.head_m}
        for (a, b), p in sorted(self.seg_path.items()):
            if len(p) >= 2:
                out[f"{a}>{b}"] = {"s": p[0], "e": p[-1], "tail": self._window(p, True), "head": self._window(p, False)}
                if (a, b) in self.uturn_segments:
                    out[f"{a}>{b}"]["u"] = 1           # 구간 도중 U턴 1회(path_one_uturn)
                if (a, b) in getattr(self, "long_segments", {}):
                    out[f"{a}>{b}"]["l"] = 1           # 긴 경로 매칭(거리 상한 밖, U턴 없음)
        return out

    def path_any(self, a: str, b: str, bus_only: bool = False) -> list[list[float]]:
        """정류장 a→b의 도로 모양. 저장된 구간 경로가 있으면 그것, 없으면 도로 최단경로, 그래도 없으면 직선."""
        if (a, b) in self.seg_path:
            return self.coords(self.seg_path[(a, b)])
        SN = self.stop_nodes
        if a in SN and b in SN and not set(SN[a]) & set(SN[b]):
            crow = hav(*self.stops[a], *self.stops[b])
            g = self.busg if bus_only else self.adj
            tg = set(SN[b])
            dist, prev = self.dijkstra(g, SN[a], max(2 * crow, crow + 400), tg)
            hit = [(dist[x], x) for x in sorted(tg) if x in dist]
            if hit:
                return self.coords(self.walk(prev, None, min(hit)[1]))
        return [[round(self.stops[a][1], 6), round(self.stops[a][0], 6)], [round(self.stops[b][1], 6), round(self.stops[b][0], 6)]]

    # ------------------------------------------------------------ 도보
    def walk_graph(self):
        """도보 그래프(방향 무시). edge 망은 정류장마다 투영점 하나를 원래 도로 선분에 끼운다(진행 방향 구분 없음).

        돌려주는 것: (무방향 인접 {노드: [(노드, m)]}, 정류장 → 그래프 노드, 정류장 → 정류장~그 노드 거리 m).
        """
        und: dict[int, list[tuple[int, float]]] = defaultdict(list)
        if self.snap_mode == "node":
            for u, es in self.adj.items():
                for v, L in es:
                    und[u].append((v, L))
                    und[v].append((u, L))
            anchor = dict(self.snap)
            off = {s: hav(*self.stops[s], *self.node[n]) for s, n in self.snap.items()}
            return und, anchor, off
        lens: dict[tuple[int, int], float] = {}
        for (u, v) in self.base_has:
            lens.setdefault((min(u, v), max(u, v)), hav(*self.node[u], *self.node[v]))
        on: dict[tuple[int, int], list[tuple[float, str]]] = defaultdict(list)
        for sid in sorted(self.proj):
            U, V = self.proj[sid]["edge"]
            on[(U, V)].append((self.proj[sid]["t"], sid))
        anchor, off = {}, {}
        wid = WBASE
        for key in sorted(lens):
            L = lens[key]
            pts = sorted(on.get(key, []))
            chain = [(0.0, key[0])]
            for t, sid in pts:
                anchor[sid] = wid
                off[sid] = self.proj[sid]["offset_m"]
                chain.append((t, wid))
                wid += 1
            chain.append((1.0, key[1]))
            for (t1, x), (t2, y) in zip(chain, chain[1:]):
                und[x].append((y, L * (t2 - t1)))
                und[y].append((x, L * (t2 - t1)))
        return und, anchor, off

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


def write_net(rn: RoadNet, out: Path, stats: dict | None = None) -> None:
    """망 파일을 out/에 쓴다: road.json(travel_times), turns.json, roadnet_stats.json, snap.json(edge 망만, 정류장 배치).

    snap.json = {정류장: 붙인 선분·진행 방향·근거, 그 정류장이 낀 긴 경로·도중 U턴 매칭 구간("long_match"·"uturn_match")}.
    """
    from busstrike.sim import io as sio
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    sio.write_bytes(out / "road.json", sio.json_bytes(rn.road_doc))
    (out / "turns.json").write_text(json.dumps(rn.turn_edges(), separators=(",", ":")), encoding="utf-8")
    (out / "roadnet_stats.json").write_text(json.dumps(stats if stats is not None else rn.stats, ensure_ascii=False, indent=1),
                                            encoding="utf-8")
    if rn.proj:
        snap = {s: {k: v for k, v in p.items() if k not in ("lat", "lon")} for s, p in sorted(rn.proj.items())}
        for key, segs in (("long_match", getattr(rn, "long_segments", {})), ("uturn_match", getattr(rn, "uturn_segments", ()))):
            for a, b in sorted(segs):
                for x in (a, b):
                    if x in snap:
                        snap[x].setdefault(key, []).append(f"{a}>{b}")
        (out / "snap.json").write_text(json.dumps(snap, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
