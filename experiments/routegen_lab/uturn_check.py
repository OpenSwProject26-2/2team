"""정류장 붙이기·U턴 판정 점검: 실제 버스 노선(GTFS) 연속 3정류장에서 U턴 판정이 얼마나 나오는지와 망 통계.

    python3 experiments/routegen_lab/uturn_check.py --root <region>/guro_0610_v4 --osm <osm>/guro_roads.json \
        [--snap-mode edge|node] [--net <망 폴더(road.json·turns.json)>] [--params gu_params.json --gu guro] \
        [--windows 150:600,50:100] [--compare <다른 결과 json>] --out <결과.json>

- 망: `roadnet.RoadNet(snap_mode)`로 다시 만든다. `--net`을 주면 그 폴더의 road.json·turns.json으로 판정하고,
  다시 만든 것과 같은지 기록한다(옛 망 재현 확인).
- GTFS 연속 3정류장(a, b, c): 운행편 정차열(stop_times)의 서로 다른 (a, b, c). 두 구간 a>b, b>c 모두 도로 조각 기록이
  있으면 "판정 가능". U턴 판정은 `graph.Graph.is_uturn`(turns 형식에 따라 규칙이 다르다).
- 방향 변화 = 정류장 좌표 기준 a→b와 b→c 방위 사이 각(0° 직진, 180° 되돌아감). 판정 결과를 각도 구간별로 센다.
- 같은 노드 정류장 = 정차 노드를 다른 정류장과 함께 쓰는 정류장. 도로에 안 붙은 정류장은 이름·구 안 수요(정밀 흡수 뒤,
  실험 주간 하루 평균, `--params`의 흡수 가정)와 함께 남긴다.
- 도보 이웃 = `walknet.walk_neighbors` 400m 안 (정류장, 다른 정류장) 순서쌍 수.
- `--windows T:H,...`(edge 망만): turns-v2의 tail(도착 전 T m)·head(출발 뒤 H m) 길이를 바꿨을 때의 판정 민감도.
- `--compare`: 다른 망의 결과 json과 같은 3정류장끼리 판정을 맞대어 본다(옛 망에서 U턴이던 것이 새 망에서 어떻게 되었나).
절대 경로·중간 파일 없이 인자만으로 다시 계산한다.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(HERE.parent))

from busstrike.planning.routegen import data as D  # noqa: E402
from busstrike.planning.routegen.graph import Graph  # noqa: E402
from busstrike.sim.traveltime import TravelTimeTable  # noqa: E402

from roadnet import RoadNet  # noqa: E402
from walknet import walk_neighbors  # noqa: E402


def angle(stops: dict, a: str, b: str, c: str) -> float | None:
    if not (a in stops and b in stops and c in stops):
        return None
    (la1, lo1), (la2, lo2), (la3, lo3) = stops[a], stops[b], stops[c]
    k = math.cos(math.radians(la2))
    v1, v2 = ((lo2 - lo1) * k, la2 - la1), ((lo3 - lo2) * k, la3 - la2)
    n1, n2 = math.hypot(*v1), math.hypot(*v2)
    if n1 == 0 or n2 == 0:
        return None
    return math.degrees(math.acos(max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))))


def abin(x: float | None) -> str:
    return "none" if x is None else "<45" if x < 45 else "45-135" if x < 135 else ">=135"


def gtfs_triples(root: Path) -> tuple[int, list[tuple[str, str, str]]]:
    trips = defaultdict(list)
    with open(root / "gtfs" / "stop_times.txt", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            trips[r["trip_id"]].append((int(r["stop_sequence"]), r["stop_id"]))
    tri = set()
    for v in trips.values():
        s = [x for _, x in sorted(v)]
        tri.update(zip(s, s[1:], s[2:]))
    return len(trips), sorted(tri)


def home_demand(root: Path, params: Path, gu: str, base) -> dict[str, float]:
    from busstrike.planning.routegen import gu_config
    from busstrike.planning.routegen import walk as WK
    from busstrike.planning.routegen.absorb import Absorber, residual_rows
    cfg = gu_config.load(params, gu)
    info = D.check_root(root, "exp")
    rows = D.load_flows(root)
    res, _ = Absorber(root, cfg.absorb_params()).absorb(rows, True, True)
    demand = D.build_demand(residual_rows(rows, res), "affected")
    st_stops = {x for s in base.stations.values() for x in (s.access_stop, s.egress_stop) if x}
    home = {s for s, x in base.stops.items() if x.gu == info["gu"]}
    return {s: w for s, w in WK.stop_weights(demand.od, st_stops).items() if s in home}


def judge(g: Graph, turns: dict, stops: dict, tri) -> dict:
    keys = {k for k in turns if k != "_meta"}
    rows = []
    for a, b, c in tri:
        ok = f"{a}>{b}" in keys and f"{b}>{c}" in keys
        rows.append({"t": [a, b, c], "judged": ok, "uturn": bool(ok and g.is_uturn(a, b, c)),
                     "angle": None if (ang := angle(stops, a, b, c)) is None else round(ang, 1)})
    netseg = {(a, b) for a in g.tt.stop_ids() for b in g.tt.successors(a)}
    for r in rows:
        if not r["judged"]:
            a, b, c = r["t"]
            if not all(x in stops for x in r["t"]):
                r["why"] = "stop_not_in_net"
            elif (a, b) not in netseg or (b, c) not in netseg:
                r["why"] = "segment_not_in_net"
            else:
                r["why"] = "no_turn_record"
    jd = [r for r in rows if r["judged"]]
    fl = [r for r in jd if r["uturn"]]
    return {"triples": len(rows), "judged": len(jd), "uturn": len(fl), "uturn_rate": round(len(fl) / max(1, len(jd)), 4),
            "unjudged": len(rows) - len(jd), "unjudged_share": round((len(rows) - len(jd)) / max(1, len(rows)), 4),
            "unjudged_why": dict(sorted(Counter(r["why"] for r in rows if not r["judged"]).items())),
            "uturn_by_angle": dict(sorted(Counter(abin(r["angle"]) for r in fl).items())),
            "judged_by_angle": dict(sorted(Counter(abin(r["angle"]) for r in jd).items())),
            "rows": rows}


def v2_reason(turns: dict, a: str, b: str, c: str) -> str:
    e1, e2 = turns[f"{a}>{b}"], turns[f"{b}>{c}"]
    if e1["e"] != e2["s"]:
        return "side_switch"
    return "retrace"


def retrace_info(rn: RoadNet, a: str, b: str, c: str) -> dict:
    """(edge 망) a→b→c의 되짚기 진단: b→c가 a→b의 도로를 반대로 지나는 첫 지점(b에서 b→c를 따라 m, a→b 도착 전 m)."""
    p1, p2 = rn.seg_path.get((a, b)), rn.seg_path.get((b, c))
    if not p1 or not p2:
        return {"path": "missing"}

    def prof(p):
        out, acc = [], 0.0
        for u, v in zip(p, p[1:]):
            out.append((acc, rn.base(u, v)))
            acc += next(L for y, L in rn.adj[u] if y == v)
        return out, acc
    e1, L1 = prof(p1)
    e2, L2 = prof(p2)
    before = {}
    for s0, e in e1:
        before[e] = round(L1 - s0)
    hit = None
    for s0, e in e2:
        r = (e[1], e[0])
        if r in before and (hit is None or s0 < hit[0]):
            hit = (round(s0), before[r])
    return {"side_switch": p1[-1] != p2[0], "ab_m": round(L1), "bc_m": round(L2),
            "retrace_after_b_m": hit[0] if hit else None, "retrace_before_b_m": hit[1] if hit else None,
            "ab_uturn_fallback": (a, b) in getattr(rn, "uturn_segments", set()),
            "bc_uturn_fallback": (b, c) in getattr(rn, "uturn_segments", set())}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--osm", required=True, type=Path)
    ap.add_argument("--snap-mode", default="edge", choices=("edge", "node"))
    ap.add_argument("--net", type=Path, default=None, help="판정에 쓸 망 폴더(road.json, turns.json)")
    ap.add_argument("--params", type=Path, default=HERE.parent / "gu_params.json")
    ap.add_argument("--gu", default=None, help="구 slug(주면 안 붙은 정류장의 구 안 수요를 계산)")
    ap.add_argument("--windows", default="", help="edge 망 tail:head 길이(m) 민감도, 예 150:600,300:inf,inf:inf")
    ap.add_argument("--walk-radius", type=float, default=400.0)
    ap.add_argument("--compare", type=Path, default=None)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    rn = RoadNet(args.osm, args.root, snap_mode=args.snap_mode)
    stats = rn.build()
    turns = rn.turn_edges()
    doc = rn.road_doc
    rec: dict = {"args": {k: str(v) for k, v in vars(args).items()},
                 "code_commit": subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True,
                                               text=True, check=False).stdout.strip(),
                 "roadnet": stats}
    if args.net:
        saved_doc = json.loads((args.net / "road.json").read_text(encoding="utf-8"))
        saved_turns = json.loads((args.net / "turns.json").read_text(encoding="utf-8"))
        rec["rebuild_equals_net"] = {"road.json": json.loads(json.dumps(doc)) == saved_doc,
                                     "turns.json": json.loads(json.dumps(turns)) == saved_turns}
        doc, turns = saved_doc, saved_turns
    base = D.load_network(args.root)
    net = D.Network(TravelTimeTable.from_dict(doc), base.stops, base.stations, turns)
    g = Graph(net, 10, 3600, "network")
    rec["turns_format"] = g.turns_format

    n_trips, tri = gtfs_triples(args.root)
    j = judge(g, turns, rn.stops, tri)
    rows = j.pop("rows")
    rec["gtfs"] = {"trips": n_trips, **j}
    if g.turns_format == "v2":
        rec["gtfs"]["uturn_reason"] = dict(Counter(v2_reason(turns, *r["t"]) for r in rows if r["uturn"]))

    # 구간
    segs = [(s["from_stop_id"], s["to_stop_id"]) for s in doc["segments"]]
    bus = {(s["from_stop_id"], s["to_stop_id"]) for s in rn.tt["segments"]}
    tk = {k for k in turns if k != "_meta"}
    rec["segments"] = {"net_segments": len(segs), "no_turn_record": sum(1 for a, b in segs if f"{a}>{b}" not in tk),
                       "bus_segments": len(bus), "bus_matched": stats["bus_segments_matched"],
                       "bus_unmatched": stats["bus_segments_unmatched"], "added": stats["added_segments"],
                       "added_share": round(stats["added_segments"] / max(1, len(segs)), 4),
                       "unmatched_by_reason": stats.get("unmatched_by_reason")}
    if hasattr(rn, "unmatched_segments"):
        rec["segments"]["unmatched_list"] = [
            dict(u, from_name=base.stops[u["from"]].name if u["from"] in base.stops else None,
                 to_name=base.stops[u["to"]].name if u["to"] in base.stops else None,
                 crow_m=round(net.stop_dist_m(u["from"], u["to"])) if u["from"] in base.stops and u["to"] in base.stops else None)
            for u in rn.unmatched_segments]

    # 정류장 붙이기
    by_node = defaultdict(list)
    for s, ns in rn.stop_nodes.items():
        for n in ns:
            by_node[n].append(s)
    shared = {s for v in by_node.values() if len(v) > 1 for s in v}
    w = home_demand(args.root, args.params, args.gu, base) if args.gu else {}
    rec["snap"] = {"stops": len(rn.stops), "snapped": len(rn.stop_nodes), "unsnapped_n": len(rn.unsnapped),
                   "shared_node_stops": len(shared), "shared_node_groups": sum(1 for v in by_node.values() if len(v) > 1),
                   "stats": rn.snap_stats,
                   "unsnapped": [{"stop_id": s, "name": base.stops[s].name if s in base.stops else None,
                                  "gu": base.stops[s].gu if s in base.stops else None,
                                  "home_demand_per_day": round(w.get(s, 0.0), 1)} for s in rn.unsnapped],
                   "unsnapped_home_demand": round(sum(w.get(s, 0.0) for s in rn.unsnapped), 1),
                   "home_demand_total": round(sum(w.values()), 1) if w else None}
    if rn.proj:
        offs = sorted(p["offset_m"] for p in rn.proj.values())
        rec["snap"]["offset_m_median"] = offs[len(offs) // 2]
        rec["snap"]["offset_m_over_30"] = sum(1 for x in offs if x > 30)
        rec["snap"]["highway"] = dict(Counter(p["highway"] for p in rn.proj.values()).most_common())
        rec["snap"]["per_stop"] = {s: {k: p[k] for k in ("edge", "t", "offset_m", "how", "dirs", "highway", "nearest",
                                                         "how0", "moved_m", "flipped") if k in p}
                                   for s, p in sorted(rn.proj.items())}
        ref = [p for p in rn.proj.values() if "moved_m" in p]
        rec["snap"]["refined_detail"] = {
            "stops": len(ref), "flipped": sum(1 for p in ref if p.get("flipped")),
            "two_to_one": sum(1 for p in ref if p["how"] == "c_resolved"),
            "moved_le_5m": sum(1 for p in ref if p["moved_m"] <= 5), "moved_5_15m": sum(1 for p in ref if 5 < p["moved_m"] <= 15),
            "moved_gt_15m": sum(1 for p in ref if p["moved_m"] > 15),
            "from_how": dict(Counter(p.get("how0") for p in ref)),
            "final_direction_basis": dict(Counter(
                ("a" if p.get("how0") == "a" else p.get("how0")) if not p.get("flipped") and p["how"] != "c_resolved"
                else p["how"] + ("_flip" if p.get("flipped") else "") for p in ref))}
        # 진행 방향 근거별 최종 수: 다듬기에서 바뀐 것은 refined(이동·반전)·c_resolved로 따로
        rec["snap"]["direction_basis"] = dict(Counter(
            p["how"] if p["how"] not in ("refined",) else ("refined_flip" if p.get("flipped") else "refined_move")
            for p in rn.proj.values()))
    else:
        from roadnet import hav
        offs = sorted(hav(*rn.stops[s], *rn.node[n]) for s, n in rn.snap.items())
        rec["snap"]["offset_m_median"] = round(offs[len(offs) // 2], 2)
        rec["snap"]["per_stop_node"] = dict(sorted(rn.snap.items()))
    nb = walk_neighbors(rn, args.walk_radius)
    rec["walk"] = {"radius_m": args.walk_radius, "pairs": sum(len(v) - 1 for v in nb.values()),
                   "stops_with_neighbor": sum(1 for v in nb.values() if len(v) > 1)}

    # 창 길이 민감도(edge 망, 다시 만든 경로로)
    if args.windows and rn.snap_mode == "edge":
        rec["windows"] = {}
        keep = (rn.tail_m, rn.head_m)
        for wspec in args.windows.split(","):
            t, h = (None if x == "inf" else float(x) for x in wspec.split(":"))
            rn.tail_m, rn.head_m = (t if t is not None else 1e12), h
            tw = rn.turn_edges()
            gw = Graph(D.Network(TravelTimeTable.from_dict(rn.road_doc), base.stops, base.stations, tw), 10, 3600, "network")
            jw = judge(gw, tw, rn.stops, tri)
            jw.pop("rows")
            rec["windows"][wspec] = {k: jw[k] for k in ("judged", "uturn", "uturn_by_angle", "judged_by_angle")}
        rn.tail_m, rn.head_m = keep

    # 다른 결과와 맞대기
    if args.compare:
        old = json.loads(args.compare.read_text(encoding="utf-8"))
        orows = {tuple(r["t"]): r for r in old["triples"]}
        cross = Counter()
        cases = defaultdict(list)
        for r in rows:
            o = orows.get(tuple(r["t"]))
            if not o:
                continue
            ok = ("U" if o["uturn"] else "-" if o["judged"] else "?") + ">" + ("U" if r["uturn"] else "-" if r["judged"] else "?")
            cross[(abin(r["angle"]), ok)] += 1
            if o["uturn"] and abin(r["angle"]) == ">=135" and not r["uturn"]:
                cases["old_uturn_ge135_not_new"].append(r["t"])
            if r["uturn"] and abin(r["angle"]) == "<45":
                cases["new_uturn_lt45"].append(r["t"])
        byt = {tuple(r["t"]): r for r in rows}

        def case(t):
            r = byt[tuple(t)]
            out = {"t": t, "names": [base.stops[x].name if x in base.stops else x for x in t], "angle": r["angle"],
                   "judged": r["judged"]}
            if not r["judged"]:
                out["why"] = r["why"]
            elif rn.snap_mode == "edge":
                out.update(retrace_info(rn, *t))
                out["b_how"] = rn.proj[t[1]]["how"]
            return out
        rec["compare"] = {"with": str(args.compare),
                          "cross": {f"{a} {k}": v for (a, k), v in sorted(cross.items())},
                          "cases": {k: [case(t) for t in v] for k, v in cases.items()}}
    rec["triples"] = rows
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    show = {k: rec[k] for k in ("rebuild_equals_net", "turns_format", "segments", "walk") if k in rec}
    show["gtfs"] = {k: v for k, v in rec["gtfs"].items()}
    show["snap"] = {k: v for k, v in rec["snap"].items() if k not in ("per_stop", "per_stop_node", "unsnapped")}
    if "windows" in rec:
        show["windows"] = rec["windows"]
    if "compare" in rec:
        show["compare"] = {"cross": rec["compare"]["cross"], "cases_n": {k: len(v) for k, v in rec["compare"]["cases"].items()}}
    if isinstance(show["segments"], dict):
        show["segments"] = {k: v for k, v in show["segments"].items() if k != "unmatched_list"}
    print(json.dumps(show, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
