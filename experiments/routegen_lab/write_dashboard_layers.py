"""운영자 대시보드(시뮬레이션 화면)가 노선 실험실과 같은 지도를 그리도록 구별 보조 자료를 만든다.

    python3 experiments/routegen_lab/write_dashboard_layers.py --results-root <결과 루트> --lab-all <lab_all> --osm-dir <osm>

- 입력: <결과 루트>/<구>/manifest.json(routegen-results/1), <lab_all>/<구>/layers/{segpaths,demand,stops,stations,meta}.json
- 출력: <결과 루트>/<구>/dashboard_layers.json, manifest에 "dashboard_layers" 키를 더한다.
  - route_geometry: 안(manifest plans[].id) → 노선 → 도로 좌표(plan_id는 안끼리 같을 수 있어 쓰지 않는다). 정류장 쌍은 lab의 segpaths(버스 통행 도로 매칭 경로)를 먼저 쓰고,
    없으면 같은 RoadNet의 버스 도로 최단경로(bus_only)로 잇는다. 그래도 길이 없으면 직선으로 채우지 않고 gap으로 센다.
    순환 노선의 마지막 정류장 → 첫 정류장 구간은 closing으로 따로 둔다.
  - absorb_demand: 정류장별 하루 파업 잔여 수요(실험 5일 평균). affected = 흡수 없음,
    precise_affected_maeul / _gg / _maeul_gg = 마을·경기버스가 남는 정원만큼 태운 뒤 남는 수요(lab의 정밀 흡수 가정).
  - stations: 지하철역 이름과 역 접근·하차 정류장(정차 순서에 역 표시용).
  - bus_roads: lab의 버스 통행 가능 도로(MultiLineString 하나).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def same(a, b) -> bool:
    return abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) < 1e-6


def concat(parts: list[list[list[float]]]) -> list[list[float]]:
    out: list[list[float]] = []
    for part in parts:
        for p in part:
            if not out or not same(out[-1], p):
                out.append(p)
    return out


class Paths:
    """정류장 a→b 도로 좌표. segpaths → (필요할 때만 만드는) RoadNet 버스 도로 최단경로."""

    def __init__(self, layers: Path, osm: Path | None, region_root: Path | None):
        self.seg = load(layers / "segpaths.json")
        self.stops = load(layers / "stops.json")
        self.osm, self.root, self.rn = osm, region_root, None
        self.routed = self.gaps = 0

    def _net(self):
        if self.rn is None:
            from roadnet import RoadNet
            self.rn = RoadNet(self.osm, self.root)
            self.rn.build()
        return self.rn

    def pair(self, a: str, b: str) -> list[list[float]] | None:
        got = self.seg.get(f"{a}>{b}")
        if got:
            return got
        if not (self.osm and self.root and self.osm.is_file()):
            self.gaps += 1
            return None
        rn = self._net()
        path = rn.path_any(a, b, bus_only=True)
        sa, sb = self.stops.get(a), self.stops.get(b)
        straight = len(path) == 2 and sa and sb and same(path[0], sa[:2]) and same(path[1], sb[:2])
        if straight:                      # path_any는 길이 없으면 직선을 준다 → 그리지 않는다
            self.gaps += 1
            return None
        self.routed += 1
        return path

    def route(self, stops: list[str]) -> dict:
        pieces, gaps = [], 0
        for a, b in zip(stops, stops[1:]):
            p = self.pair(a, b)
            if p is None:
                gaps += 1
                pieces.append(None)
            else:
                pieces.append(p)
        # 이어진 구간끼리 묶는다(빈 구간에서 선을 끊는다)
        lines, cur = [], []
        for p in pieces:
            if p is None:
                if len(cur) >= 2:
                    lines.append(cur)
                cur = []
            else:
                cur = concat([cur, p])
        if len(cur) >= 2:
            lines.append(cur)
        closing = self.pair(stops[-1], stops[0]) if len(stops) >= 2 and stops[0] != stops[-1] else None
        return {"lines": lines, "closing": closing, "gaps": gaps + (1 if len(stops) >= 2 and stops[0] != stops[-1] and closing is None else 0)}


def one(gu_dir: Path, lab_all: Path, osm_dir: Path | None) -> str:
    man_path = gu_dir / "manifest.json"
    man = load(man_path)
    region = man["region"]
    layers = lab_all / region / "layers"
    if not (layers / "segpaths.json").is_file():
        return f"{region}: lab_all 자료 없음, 건너뜀"
    meta = load(layers / "meta.json")
    if meta.get("network_snapshot_id") and man.get("network_snapshot_id") and meta["network_snapshot_id"] != man["network_snapshot_id"]:
        return f"{region}: 망 ID 다름({meta['network_snapshot_id']} ≠ {man['network_snapshot_id']}), 건너뜀"
    region_root = Path(meta["region_root"]) if meta.get("region_root") else None
    osm = osm_dir / f"{region}_roads.json" if osm_dir else None
    paths = Paths(layers, osm, region_root)
    geometry: dict[str, dict] = {}
    for entry in man["plans"]:
        plan_file = Path(entry["plan_file"])
        plan_file = plan_file if plan_file.is_absolute() else gu_dir / plan_file
        doc = load(plan_file)
        geometry[entry["id"]] = {r["route_id"]: paths.route(r["stops"]) for r in doc["routes"] if len(r.get("stops", [])) >= 2}
    demand = load(layers / "demand.json")
    keep = ["affected", "affected_maeul", "affected_gg", "affected_maeul_gg",
            "precise_affected_maeul", "precise_affected_gg", "precise_affected_maeul_gg"]
    variants = {k: {sid: round(v, 2) for sid, v in demand[k].items()} for k in keep if k in demand}
    used = set().union(*[set(v) for v in variants.values()]) if variants else set()
    stops = load(layers / "stops.json")
    stations = load(layers / "stations.json")
    out = {"schema": "dashboard-layers/1", "region": region, "network_snapshot_id": man.get("network_snapshot_id"),
           "source": f"routegen lab layers ({layers})",
           "route_geometry": geometry,
           "absorb_demand": {"unit": "하루 인원(실험 5일 평균)", "stops": {sid: stops[sid][:3] for sid in used if sid in stops},
                             "variants": variants,
                             "note": "precise_* = 마을·경기버스가 남는 정원만큼 태운 뒤 남는 수요(lab 정밀 흡수 기본 가정)"},
           "stations": [{"name": s.get("name"), "access": s.get("access"), "egress": s.get("egress"),
                         "lon": s.get("lon"), "lat": s.get("lat")} for s in stations],
           "bus_roads": load(layers / "bus_roads.json") if (layers / "bus_roads.json").is_file() else None}
    (gu_dir / "dashboard_layers.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    man["dashboard_layers"] = "dashboard_layers.json"
    man_path.write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
    n_routes = sum(len(v) for v in geometry.values())
    return f"{region}: 안 {len(geometry)}개 · 노선 {n_routes}개 · 도로 최단경로로 채운 쌍 {paths.routed} · 길 없는 쌍 {paths.gaps}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--results-root", required=True, type=Path)
    ap.add_argument("--lab-all", required=True, type=Path)
    ap.add_argument("--osm-dir", type=Path, help="<구>_roads.json 폴더(segpaths에 없는 정류장 쌍을 버스 도로로 잇는다)")
    ap.add_argument("--gu", default="")
    args = ap.parse_args()
    only = set(filter(None, args.gu.split(",")))
    for gu_dir in sorted(p for p in args.results_root.iterdir() if (p / "manifest.json").is_file()):
        if only and gu_dir.name not in only:
            continue
        print(one(gu_dir, args.lab_all, args.osm_dir), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
