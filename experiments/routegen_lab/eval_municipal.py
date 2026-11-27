"""지자체 셔틀안(plan.json)을 hub_sweep과 같은 묶음(도로 망, 정밀 흡수 뒤 수요)에서 돌린다.

    python3 experiments/routegen_lab/eval_municipal.py --sweep <hub_sweep2/<구>> --plan <municipal/<구>/plan.json> [--jobs 3]
        [--links net|osm --osm <구_roads.json> --root <region>/<구>_0610_v4] [--service-start 07:00] [--name muni] [--no-run]

- 지자체 셔틀은 중간 정류장을 건너뛰고 선다. 연속 정차 사이에 망 구간이 없으면 "무정차 구간"을 더한다.
  이 구간은 지자체안 실행 묶음(`<sweep>/<name>/road_muni.json`)에만 들어간다(우리 생성기 후보에는 쓰지 않는다).
  - `--links net`(기본, 예전 방식): 망 구간을 이은 최단 경로(시간대별). 거리·시간 = 경로 위 구간 합.
  - `--links osm`: 원 OSM 도로 최단 경로(`roadnet.RoadNet.osm_link`: U턴만 금지, 버스 도로 여부 무관). 시간 = 그 도로 길이
    ÷ 버스 속도(그 도로의 시간대별 실측 속도, 표본이 없으면 그 시간대 전체 중앙값). 망에 없는 임시 정류장(plan의
    `_temporary_stops` 좌표)은 정차하지 않고, 그 앞뒤 정차를 잇는 경로가 그 좌표(가장 가까운 정차 가능 선분)를 지나게 한다.
    망을 같은 설정으로 다시 만들어 `<sweep>/net/road.json`과 같은지 기록한다.
    **같은 길 규칙**(`--same-road-tol`, 기본 0.01): 원 OSM 경로 길이가 버스 도로(망 구간) 최단 경로 길이와 1% 안으로 같고
    경유할 임시 정류장이 없으면, 같은 길로 보고 버스 도로 경로의 시간(실측 구간 시간 합, 시간대별)과 길이를 쓴다. 더 짧은
    다른 길일 때만 속도 규칙 시간을 쓴다(사용자 결정 2026-11-19). 이음마다 `time_rule`(net_same_road | osm_speed)을 남긴다.
- `--service-start HH:MM`: 지자체안 묶음의 차량 가용 시작 시각을 그 시각으로 늦춘다(시나리오 입력의 `available_start_s`,
  시간표 첫 출발 = max(운영 시작, 가용 시작)). 운영 시간대·수요는 그대로다(그 전에 온 승객은 기다리다 떠날 수 있다).
  시뮬레이터·검증기는 그대로다. `run_inputs`는 시뮬레이터 함수로 다시 만든다.
- 망에 없는 정류장은 빼고, 남은 정차가 2개 미만인 노선은 뺀다(대수는 남은 노선에 비례로 다시 나눔). 모두 muni.json에 남긴다.
- 대수 합은 묶음 대수 중 하나와 같아야 한다(변환기 C2). 다르면 가장 가까운 묶음 대수로 비례 조정하고 기록한다.
- 실행: run_matrix(변환기 → M05 → M07 `--max-recompute 200000`), 결과 `<sweep>/<name>/runs_{exp,test}`(`--no-run`이면 묶음만).
"""

from __future__ import annotations

import argparse
import heapq
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(HERE.parent))

from busstrike.sim import io as sio  # noqa: E402
from busstrike.sim.arrivals import build_run_inputs  # noqa: E402

from run_networks import RUN_MATRIX, bundles_for  # noqa: E402


def shortest(adj: dict, a: str, b: str, hour_i: int):
    """hour_i번째 프로필 시간으로 a → b 최단 경로(정류장 열)."""
    dist, prev, pq = {a: 0.0}, {}, [(0.0, a)]
    while pq:
        d, u = heapq.heappop(pq)
        if u == b:
            break
        if d > dist[u]:
            continue
        for v, seg in adj.get(u, ()):
            nd = d + seg["profile"][hour_i][1]
            if nd < dist.get(v, float("inf")):
                dist[v], prev[v] = nd, u
                heapq.heappush(pq, (nd, v))
    if b not in dist:
        return None
    p = [b]
    while p[-1] != a:
        p.append(prev[p[-1]])
    return p[::-1]


def apportion(total: int, weights: list[float]) -> list[int]:
    raw = [total * w / sum(weights) for w in weights]
    q = [max(1, int(x)) for x in raw]
    while sum(q) < total:
        q[max(range(len(q)), key=lambda i: raw[i] - q[i])] += 1
    while sum(q) > total:
        q[max(range(len(q)), key=lambda i: q[i] - raw[i])] -= 1
    return q


def same_road(osm_m: float, net_m: float | None, tol: float, has_via: bool) -> bool:
    """원 OSM 이음이 버스 도로 경로와 같은 길인가: 경유 임시 정류장이 없고 길이 차이가 tol 비율 안(tol < 0이면 끔)."""
    if tol < 0 or has_via or not net_m:
        return False
    return abs(osm_m - net_m) <= tol * net_m


def hhmm_s(x: str) -> int:
    h, m = x.split(":")
    return int(h) * 3600 + int(m) * 60


def delay_start(bundles: Path, fleet: int, start_s: int) -> None:
    """묶음마다 scenario_f{fleet}.json의 차량 가용 시작을 start_s로 늦추고 run_inputs를 다시 만든다."""
    tag = f"-s{start_s // 3600:02d}{start_s % 3600 // 60:02d}"
    for b in sorted(bundles.iterdir()):
        sp = b / f"scenario_f{fleet}.json"
        sc = json.loads(sp.read_text(encoding="utf-8"))
        for v in sc["fleet"]["vehicles"]:
            v["available_start_s"] = max(int(v["available_start_s"]), start_s)
        sio.write_bytes(sp, sio.json_bytes(sc))
        rp = b / f"run_inputs_f{fleet}.json"
        ri = json.loads(rp.read_text(encoding="utf-8"))
        build_run_inputs(ri["input_bundle_id"] + tag, sp, b / "demand_bins.csv", b / "travel_times.json", ri["seed"], rp)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sweep", required=True, type=Path)
    ap.add_argument("--plan", required=True, type=Path)
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--links", choices=("net", "osm"), default="net", help="망에 직접 구간이 없는 이음을 재는 방법")
    ap.add_argument("--osm", type=Path, default=None, help="--links osm: 구 OSM 도로(<구>_roads.json)")
    ap.add_argument("--root", type=Path, default=None, help="--links osm: 실험 묶음 루트(<region>/<구>_0610_v4)")
    ap.add_argument("--service-start", default=None, help="지자체안 운행 시작 HH:MM(차량 가용 시작). 비우면 묶음 그대로")
    ap.add_argument("--name", default="muni", help="결과 폴더 이름(<sweep>/<name>)")
    ap.add_argument("--no-run", action="store_true", help="묶음·plan만 만들고 시뮬레이션은 돌리지 않는다")
    ap.add_argument("--same-road-tol", type=float, default=0.01,
                    help="--links osm: 원 OSM 길이가 버스 도로 경로와 이 비율 안이면 버스 도로 경로 시간(음수면 끔)")
    ap.add_argument("--out-dir", type=Path, default=None, help="결과 폴더(비우면 <sweep>/<name>)")
    ap.add_argument("--close-return", action="store_true",
                    help="--links osm: 끝 → 첫 정류장 복귀 길이 망에 없으면 원 OSM 도로로 잇는다(기본 끔 = 예전과 같음)")
    args = ap.parse_args()
    sw = args.sweep.resolve()
    log = json.loads((sw / "hub_sweep.json").read_text(encoding="utf-8"))
    fleets = log["fleets"]
    doc = json.loads((sw / "net" / "road.json").read_text(encoding="utf-8"))
    seg = {(s["from_stop_id"], s["to_stop_id"]): s for s in doc["segments"]}
    adj: dict = {}
    for (a, b), s in seg.items():
        adj.setdefault(a, []).append((b, s))
    known = {x for k in seg for x in k}
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    temp = (plan.get("_temporary_stops") or {}).get("stops") or {}
    rec = {"plan": str(args.plan), "plan_revision": plan.get("revision"), "links_mode": args.links,
           "service_start": args.service_start, "same_road_tol": args.same_road_tol if args.links == "osm" else None,
           "dropped_stops": {}, "dropped_routes": [], "added_segments": 0,
           "unreachable": [], "links": []}
    rn = None
    if args.links == "osm":
        if not (args.osm and args.root):
            ap.error("--links osm에는 --osm, --root가 필요하다")
        from roadnet import RoadNet
        rn = RoadNet(args.osm, args.root)
        rn.build()
        rec["net_equal_rebuild"] = json.loads(json.dumps(rn.road_doc)) == doc
    nbin = len(doc["segments"][0]["profile"])
    added = {}
    routes = []
    for r in plan["routes"]:
        kept: list[tuple[str, list[str]]] = []      # (정차, 그 앞에서 지나야 할 임시 정류장)
        pending: list[str] = []
        for s in r["stops"]:
            if s in known:
                if s in {k for k, _ in kept}:
                    continue                          # 변환기 S2(중복 정차) 회피
                kept.append((s, pending))
                pending = []
            else:
                rec["dropped_stops"].setdefault(r["route_id"], []).append(s)
                if args.links == "osm" and s in temp:
                    pending.append(s)
        stops = [k for k, _ in kept]
        ok = True
        for (a, _), (b, via) in zip(kept, kept[1:]):
            if ((a, b) in seg and not via) or (a, b) in added:
                continue
            npaths = [shortest(adj, a, b, i) for i in range(nbin)]
            net_m = None if npaths[1] is None else sum(seg[(x, y)]["distance_m"] for x, y in zip(npaths[1], npaths[1][1:]))
            link = {"route_id": r["route_id"], "from": a, "to": b, "via": via, "direct_in_net": (a, b) in seg, "net_m": net_m}
            if args.links == "osm":
                got = rn.osm_link(a, b, via=[(temp[v]["lat"], temp[v]["lon"]) for v in via])
                if got is None:
                    rec["unreachable"].append([r["route_id"], a, b])
                    ok = False
                    break
                prof, dist = got["profile"], got["length_m"]
                link.update(osm_m=dist, via_offset_m=got["via_offset_m"], osm_time_s={str(t0): t for t0, t in prof})
                same = same_road(dist, net_m, args.same_road_tol, bool(via)) and all(p is not None for p in npaths)
                if same:     # 같은 길: 버스 도로 경로의 실측 구간 시간 합(시간대별 경로)
                    prof = [[t0, sum(seg[(x, y)]["profile"][i][1] for x, y in zip(p, p[1:]))]
                            for i, ((t0, _), p) in enumerate(zip(doc["segments"][0]["profile"], npaths))]
                    dist = net_m
                link["time_rule"] = "net_same_road" if same else "osm_speed"
            else:
                if any(p is None for p in npaths):
                    rec["unreachable"].append([r["route_id"], a, b])
                    ok = False
                    break
                prof = [[t0, sum(seg[(x, y)]["profile"][i][1] for x, y in zip(p, p[1:]))]
                        for i, ((t0, _), p) in enumerate(zip(doc["segments"][0]["profile"], npaths))]
                dist = net_m
            link["time_s"] = {str(t0): t for t0, t in prof}
            rec["links"].append(link)
            added[(a, b)] = {"from_stop_id": a, "to_stop_id": b, "profile": prof, "distance_m": dist}
        if ok and args.close_return and len(stops) >= 2:
            # 끝 → 첫 정류장 복귀(변환기 RETURN 검사)도 망에 길이 없으면 같은 규칙(원 OSM 도로)으로 잇는다. 길이 있으면 그대로.
            a, b = stops[-1], stops[0]
            adj2 = {u: list(vs) for u, vs in adj.items()}
            for (x, y), s in added.items():
                adj2.setdefault(x, []).append((y, s))
            if (a, b) not in seg and (a, b) not in added and any(shortest(adj2, a, b, i) is None for i in range(nbin)):
                got = rn.osm_link(a, b) if rn is not None else None
                if got is None:
                    rec["unreachable"].append([r["route_id"], a, b, "return"])
                    ok = False
                else:
                    added[(a, b)] = {"from_stop_id": a, "to_stop_id": b, "profile": got["profile"], "distance_m": got["length_m"]}
                    rec.setdefault("return_links", []).append({"route_id": r["route_id"], "from": a, "to": b,
                                                               "osm_m": got["length_m"], "time_rule": "osm_speed"})
        if ok and len(stops) >= 2:
            routes.append({"route_id": r["route_id"], "stops": stops, "vehicles": r["vehicles"]})
        else:
            rec["dropped_routes"].append(r["route_id"])
    total = sum(r["vehicles"] for r in plan["routes"])
    fleet = min(fleets, key=lambda f: (abs(f - total), f))
    if fleet != total or rec["dropped_routes"]:
        for r, v in zip(routes, apportion(fleet, [r["vehicles"] for r in routes])):
            r["vehicles"] = v
    rec.update({"plan_vehicles": total, "fleet": fleet, "routes": len(routes), "added_segments": len(added)})
    out = args.out_dir.resolve() if args.out_dir else sw / args.name
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    doc2 = dict(doc)
    # 망에 이미 있는 구간(임시 정류장을 지나는 이음 등)은 지자체안 이음으로 바꾼다
    doc2["segments"] = sorted([s for k, s in seg.items() if k not in added] + list(added.values()),
                              key=lambda s: (s["from_stop_id"], s["to_stop_id"]))
    tt_path = out / "road_muni.json"
    tt_path.write_text(json.dumps(doc2, separators=(",", ":")), encoding="utf-8")
    pl = {"plan_id": "plan-muni", "revision": 1, "network_snapshot_id": doc["network_snapshot_id"], "routes": routes}
    (out / "plan.json").write_text(json.dumps(pl, ensure_ascii=False, indent=1), encoding="utf-8")
    variant = "precise_affected_maeul_gg"
    for split in ("exp", "test"):
        b = bundles_for(sw / split, out / split, tt_path, variant, [fleet])
        if args.service_start:
            delay_start(b, fleet, hhmm_s(args.service_start))
        if args.no_run:
            continue
        cmd = [sys.executable, str(RUN_MATRIX), "--bundles", str(b), "--out", str(out / f"runs_{split}"),
               "--src", str(REPO / "src"), "--jobs", str(args.jobs), "--plan", f"muni:{fleet}={out / 'plan.json'}"]
        print(split, "rc", subprocess.run(cmd, check=False).returncode, flush=True)
    (out / "muni.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: rec[k] for k in ("plan_vehicles", "fleet", "routes", "added_segments", "dropped_routes",
                                          "links_mode", "service_start")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
