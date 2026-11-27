"""8단계 내보내기: 시민 길찾기·지도용 운행안 export/<plan id>.json (docs/design/pipeline.md 5절).

    {plan_id, routes: [{route_id, name, vehicles, cycle_min, headway_min,
                        stops: [{seq, stop_id, name, lat, lon, kind: station|stop}], shape: [[lat, lon], ...]}]}

- 정류장 이름·좌표 = 묶음의 network/stops.csv. kind = 역 지정 정류장(data.load_network의 역 access·egress 정류장)이면 station.
- shape = 정류장 사이 실제 도로 좌표(RoadNet.path_any: 저장된 버스 도로 구간 경로, 없으면 도로 최단경로). 지도 앱 길찾기를 거치지 않는다.
  고리를 닫는 마지막 → 첫 정류장 구간도 넣는다(순환 운행). 도로 경로를 못 찾아 직선으로 이은 구간 수는 shape_straight에 남긴다.
- 순환 시간 = 생성기 순환 시간(plan_report.json route_info cycle_s, 배분기와 같은 값), 배차 = 순환 ÷ 대수.
- 카드 ID(TRCR_ID)는 쓰지 않는다(입력 plan·정류장 자료에 없다).
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
LAB = REPO / "experiments" / "routegen_lab"


def concat(parts: list[list[list[float]]]) -> list[list[float]]:
    """구간 좌표를 잇는다(앞 구간 끝 = 다음 구간 처음이면 한 번만)."""
    out: list[list[float]] = []
    for p in parts:
        out.extend(p if not out else p[1:] if out[-1] == p[0] else p)
    return out


class Exporter:
    def __init__(self, root: Path, osm: Path | None, road_json: Path | None = None, roadnet=None,
                 station_stops: set[str] | None = None):
        self.root = Path(root)
        self.names: dict[str, str] = {}
        self.coord: dict[str, tuple[float, float]] = {}
        with (self.root / "network" / "stops.csv").open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                self.names[r["stop_id"]] = r["stop_name"]
                self.coord[r["stop_id"]] = (float(r["stop_lat"]), float(r["stop_lon"]))
        if station_stops is None:                         # 역 지정 정류장(access·egress) = kind station
            from busstrike.planning.routegen import data as D
            net = D.load_network(self.root)
            station_stops = {x for s in net.stations.values() for x in (s.access_stop, s.egress_stop) if x}
        self.station_stops = set(station_stops)
        self.rn = roadnet
        if self.rn is None and osm is not None:
            if str(LAB) not in sys.path:
                sys.path.insert(0, str(LAB))
            from roadnet import RoadNet
            self.rn = RoadNet(Path(osm), self.root)
            self.rn.build()

    def leg(self, a: str, b: str) -> tuple[list[list[float]], bool]:
        """정류장 a → b 도로 좌표([[lat, lon]…])와 직선 여부."""
        if self.rn is None:
            return [list(self.coord[a]), list(self.coord[b])], True
        lonlat = self.rn.path_any(a, b)
        straight = len(lonlat) == 2 and lonlat[0] == [round(self.rn.stops[a][1], 6), round(self.rn.stops[a][0], 6)] \
            and lonlat[1] == [round(self.rn.stops[b][1], 6), round(self.rn.stops[b][0], 6)]
        return [[y, x] for x, y in lonlat], straight

    def route(self, rid: str, stops: list[str], vehicles: int, cycle_s: int | None) -> dict:
        seq = [{"seq": i, "stop_id": s, "name": self.names.get(s, s), "lat": self.coord[s][0], "lon": self.coord[s][1],
                "kind": "station" if s in self.station_stops else "stop"} for i, s in enumerate(stops, 1)]
        parts, straight = [], 0
        ring = list(zip(stops, stops[1:])) + ([(stops[-1], stops[0])] if len(stops) > 1 and stops[-1] != stops[0] else [])
        for a, b in ring:
            pts, st = self.leg(a, b)
            parts.append(pts)
            straight += st
        cyc = None if cycle_s is None else round(cycle_s / 60, 2)
        first, last = self.names.get(stops[0], stops[0]), self.names.get(stops[len(stops) // 2], stops[len(stops) // 2])
        return {"route_id": rid, "name": f"{rid} {first} 순환({last} 경유)", "vehicles": vehicles, "cycle_min": cyc,
                "headway_min": None if cyc is None or not vehicles else round(cyc / vehicles, 2),
                "stops": seq, "shape": concat(parts), "shape_straight": straight}


def write(ex: Exporter, plan_id: str, plan_path: Path, report_path: Path | None, out: Path) -> dict:
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    cyc: list[int | None] = [None] * len(plan["routes"])
    if report_path is not None and Path(report_path).exists():
        rep = json.loads(Path(report_path).read_text(encoding="utf-8"))
        info = [r for r in rep.get("routes", []) if "cycle_s" in r]
        if len(info) == len(plan["routes"]):
            cyc = [r["cycle_s"] for r in info]
    doc = {"plan_id": plan_id, "routes": [ex.route(r["route_id"], r["stops"], r["vehicles"], c)
                                          for r, c in zip(plan["routes"], cyc)]}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return doc
