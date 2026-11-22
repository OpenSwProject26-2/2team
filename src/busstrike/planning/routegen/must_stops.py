"""필수 정류장(설계 v2 1절) — 시설 목록과 묶음에서 그때그때 계산한다. 목록을 손으로 고치지 않는다.

규칙(확정, 바꾸지 않음)
1. 시설에서 반경 R(기본 200m) 안의 망 정류장(network/stops.csv)이 후보다. 없으면 "버스망 밖"(out_of_network)으로
   보고만 한다.
2. 후보 중 계속 운행하는 노선이 서는 정류장이 하나라도 있으면 필수가 아니다(served_by_running).
   계속 운행 = 묶음 GTFS(gtfs/)에서 등록부(registry/strike_route_registry.csv) strike_state가 affected가 아닌 노선
   + network/absorbing_routes.csv의 마을(maeul)·경기(gg) 노선(설정으로 경기 제외 가능).
3. 아니면 가장 가까운 후보 정류장을 필수 정류장으로 한다(거리가 같으면 정류장 ID 사전순).
4. 대상 = 필수 정류장 ID 앞 3자리가 그 구 코드인 것(아니면 other_gu).

해석 지점(원본 스크립트 recon_guro_v1/scripts/build_must_stops.py와 대조 전)
- 등록부에 없는 GTFS 노선은 "affected가 아닌 노선"으로 본다(문장 그대로). v4 묶음 4개 구는 GTFS 노선이 모두 등록부에 있다.
- strike_state = unknown 노선도 affected가 아니므로 계속 운행으로 본다.
- 거리는 `data.haversine_m`(지구 반지름 6,371,008.8m)이다. 반경 비교는 `≤ R`. 가장 가까운 정류장을 고를 때
  거리는 mm(소수 셋째 자리)로 반올림해 비교한다(부동소수점 차이로 동률이 갈리지 않게).

    python -m busstrike.planning.routegen.must_stops --facilities <facilities_seoul.csv> --root <구 묶음> \
        --out <폴더> [--radius-m 200] [--kinds medical,market] [--no-gg] [--gu-code 116]

출력: <out>/must_stops.csv(필수 정류장 행: stop_id, facility_id, kind, type, name, distance_m),
<out>/must_stops_report.json(제외 사유별 시설 수, 고유 필수 정류장, 설정).
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from .data import _read_csv, haversine_m

REASONS = ("must", "out_of_network", "served_by_running", "other_gu")


@dataclass(frozen=True)
class MustConfig:
    radius_m: float = 200.0
    kinds: tuple[str, ...] | None = None   # 시설 kind 또는 type 값 목록. None = 전부
    include_gg: bool = True                # absorbing_routes의 경기 노선을 계속 운행으로 볼지
    gu_code: str | None = None             # 정류장 ID 앞 3자리. None = 묶음 stops.csv에서 구 이름으로 찾는다


@dataclass(frozen=True)
class Facility:
    facility_id: str
    kind: str
    type: str
    name: str
    gu: str
    lat: float
    lon: float


@dataclass(frozen=True)
class MustRow:
    stop_id: str
    facility_id: str
    kind: str
    type: str
    name: str
    distance_m: float


def load_facilities(path: Path) -> list[Facility]:
    out = []
    for r in _read_csv(Path(path)):
        out.append(Facility(r["facility_id"], r["kind"], r["type"], r["name"], r["gu"], float(r["lat"]),
                            float(r["lon"])))
    return sorted(out, key=lambda f: f.facility_id)


def load_stops(root: Path) -> dict[str, tuple[float, float, str]]:
    """망 정류장 → (위도, 경도, 구)."""
    return {r["stop_id"]: (float(r["stop_lat"]), float(r["stop_lon"]), r["gu"])
            for r in _read_csv(Path(root) / "network" / "stops.csv")}


def running_stops(root: Path, include_gg: bool = True, affected_override: set[str] | None = None) -> dict:
    """계속 운행 노선이 서는 정류장. 돌려주는 값: {'stops': set, 'routes': {...}}.

    affected_override를 주면 등록부 대신 그 노선 집합을 affected로 본다(부분 파업 시나리오용).
    """
    root = Path(root)
    state = {r["route_id"]: r["strike_state"] for r in _read_csv(root / "registry" / "strike_route_registry.csv")}
    gtfs_routes = [r["route_id"] for r in _read_csv(root / "gtfs" / "routes.txt")]
    if affected_override is None:
        running = {rid for rid in gtfs_routes if state.get(rid) != "affected"}
    else:
        running = {rid for rid in gtfs_routes if rid not in affected_override}
    trip_route = {r["trip_id"]: r["route_id"] for r in _read_csv(root / "gtfs" / "trips.txt")}
    stops: set[str] = set()
    with open(root / "gtfs" / "stop_times.txt", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if trip_route.get(r["trip_id"]) in running:
                stops.add(r["stop_id"])
    absorbing = {"maeul": [], "gg": []}
    for r in _read_csv(root / "network" / "absorbing_routes.csv"):
        mode = r["mode"]
        if mode == "gg" and not include_gg:
            continue
        if mode in absorbing:
            absorbing[mode].append(r["route_id"])
            stops.update(r["stops"].split())
    return {"stops": stops, "gtfs_running": sorted(running), "absorbing": absorbing}


def gu_code_of(stops: dict[str, tuple[float, float, str]], gu: str) -> str:
    """그 구 정류장 ID 앞 3자리 중 가장 많은 것(동률이면 사전순)."""
    c = Counter(sid[:3] for sid, (_, _, g) in stops.items() if g == gu)
    if not c:
        raise ValueError(f"stops.csv에 {gu} 정류장이 없다")
    return min(c, key=lambda k: (-c[k], k))


def compute(facilities: list[Facility], stops: dict[str, tuple[float, float, str]], running: set[str],
            gu_code: str, cfg: MustConfig = MustConfig()) -> dict:
    """규칙 1~4. 돌려주는 값: rows(필수 행), by_facility(시설별 판정), counts(사유별 시설 수), must_stops(고유)."""
    rows: list[MustRow] = []
    by_facility: dict[str, dict] = {}
    for f in facilities:
        if cfg.kinds is not None and f.kind not in cfg.kinds and f.type not in cfg.kinds:
            continue
        cands = sorted((round(haversine_m(f.lat, f.lon, lat, lon), 3), sid) for sid, (lat, lon, _) in stops.items())
        cands = [(d, s) for d, s in cands if d <= cfg.radius_m]
        if not cands:
            by_facility[f.facility_id] = {"reason": "out_of_network"}
            continue
        served = sorted(s for _, s in cands if s in running)
        if served:
            by_facility[f.facility_id] = {"reason": "served_by_running", "running_stops": served}
            continue
        d, s = cands[0]                       # (거리, 정류장 ID) 정렬이라 동률이면 ID 사전순
        if s[:3] != gu_code:
            by_facility[f.facility_id] = {"reason": "other_gu", "stop_id": s, "distance_m": round(d, 1)}
            continue
        by_facility[f.facility_id] = {"reason": "must", "stop_id": s, "distance_m": round(d, 1)}
        rows.append(MustRow(s, f.facility_id, f.kind, f.type, f.name, round(d, 1)))
    counts = Counter(v["reason"] for v in by_facility.values())
    rows.sort(key=lambda r: (r.stop_id, r.facility_id))
    return {"rows": rows, "by_facility": by_facility,
            "counts": {k: counts.get(k, 0) for k in REASONS},
            "must_stops": sorted({r.stop_id for r in rows})}


def for_bundle(facilities_path: Path, root: Path, gu: str, cfg: MustConfig = MustConfig(),
               affected_override: set[str] | None = None) -> dict:
    """묶음 하나에 규칙을 적용한다. gu = 그 구 이름(구 코드를 cfg에서 안 주면 이것으로 찾는다)."""
    stops = load_stops(root)
    run = running_stops(root, cfg.include_gg, affected_override)
    code = cfg.gu_code or gu_code_of(stops, gu)
    out = compute(load_facilities(facilities_path), stops, run["stops"], code, cfg)
    out["gu"] = gu
    out["gu_code"] = code
    out["config"] = asdict(cfg)
    out["running_routes"] = {"gtfs": len(run["gtfs_running"]), "maeul": len(run["absorbing"]["maeul"]),
                             "gg": len(run["absorbing"]["gg"])}
    return out


def write(out_dir: Path, result: dict) -> None:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "must_stops.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["stop_id", "facility_id", "kind", "type", "name", "distance_m"])
        for r in result["rows"]:
            w.writerow([r.stop_id, r.facility_id, r.kind, r.type, r.name, f"{r.distance_m:.1f}"])
    rep = {k: v for k, v in result.items() if k != "rows"}
    (out_dir / "must_stops_report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    from .data import check_root
    ap = argparse.ArgumentParser(prog="python -m busstrike.planning.routegen.must_stops")
    ap.add_argument("--facilities", required=True, type=Path)
    ap.add_argument("--root", required=True, type=Path, help="구 묶음 폴더(network/, gtfs/, registry/)")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--gu", default=None, help="구 이름. 기본: README")
    ap.add_argument("--radius-m", type=float, default=MustConfig.radius_m)
    ap.add_argument("--kinds", default=None, help="쉼표 목록(kind 또는 type). 기본: 전부")
    ap.add_argument("--no-gg", action="store_true", help="경기 노선을 계속 운행에서 뺀다")
    ap.add_argument("--gu-code", default=None)
    args = ap.parse_args(argv)
    gu = args.gu or check_root(args.root, "exp")["gu"]
    cfg = MustConfig(args.radius_m, tuple(args.kinds.split(",")) if args.kinds else None, not args.no_gg,
                     args.gu_code)
    res = for_bundle(args.facilities, args.root, gu, cfg)
    write(args.out, res)
    print(f"{gu}({res['gu_code']}): 필수 정류장 {len(res['must_stops'])}곳 {res['must_stops']} · 시설 {res['counts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
