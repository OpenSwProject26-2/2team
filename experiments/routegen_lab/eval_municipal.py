"""지자체 셔틀안(plan.json)을 hub_sweep과 같은 묶음(도로 망, 정밀 흡수 뒤 수요)에서 돌린다.

    python3 experiments/routegen_lab/eval_municipal.py --sweep <hub_sweep2/<구>> --plan <municipal/<구>/plan.json> [--jobs 3]

- 지자체 셔틀은 중간 정류장을 건너뛰고 선다. 연속 정차 사이에 망 구간이 없으면 도로 망 최단 경로(시간대별)로
  "무정차 구간"을 더한다(거리·시간 = 경로 합). 이 구간은 지자체안 실행 묶음에만 들어간다.
- 망에 없는 정류장은 빼고, 남은 정차가 2개 미만인 노선은 뺀다(대수는 남은 노선에 비례로 다시 나눔). 모두 muni.json에 남긴다.
- 대수 합은 묶음 대수 중 하나와 같아야 한다(변환기 C2). 다르면 가장 가까운 묶음 대수로 비례 조정하고 기록한다.
- 실행: run_matrix(변환기 → M05 → M07 `--max-recompute 200000`), 결과 `<sweep>/muni/runs_{exp,test}`.
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sweep", required=True, type=Path)
    ap.add_argument("--plan", required=True, type=Path)
    ap.add_argument("--jobs", type=int, default=3)
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
    rec = {"plan": str(args.plan), "dropped_stops": {}, "dropped_routes": [], "added_segments": 0, "unreachable": []}
    added = {}
    routes = []
    for r in plan["routes"]:
        stops = [s for s in r["stops"] if s in known]
        if len(stops) < len(r["stops"]):
            rec["dropped_stops"][r["route_id"]] = [s for s in r["stops"] if s not in known]
        stops = [s for i, s in enumerate(stops) if s not in stops[:i]]          # 변환기 S2(중복 정차) 회피
        ok = True
        for a, b in zip(stops, stops[1:]):
            if (a, b) in seg or (a, b) in added:
                continue
            paths = [shortest(adj, a, b, i) for i in range(len(doc["segments"][0]["profile"]))]
            if any(p is None for p in paths):
                rec["unreachable"].append([r["route_id"], a, b])
                ok = False
                break
            prof = [[t0, sum(seg[(x, y)]["profile"][i][1] for x, y in zip(p, p[1:]))]
                    for i, ((t0, _), p) in enumerate(zip(doc["segments"][0]["profile"], paths))]
            p = paths[1]
            added[(a, b)] = {"from_stop_id": a, "to_stop_id": b, "profile": prof,
                             "distance_m": sum(seg[(x, y)]["distance_m"] for x, y in zip(p, p[1:]))}
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
    out = sw / "muni"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    doc2 = dict(doc)
    doc2["segments"] = sorted(doc["segments"] + list(added.values()), key=lambda s: (s["from_stop_id"], s["to_stop_id"]))
    tt_path = out / "road_muni.json"
    tt_path.write_text(json.dumps(doc2, separators=(",", ":")), encoding="utf-8")
    pl = {"plan_id": "plan-muni", "revision": 1, "network_snapshot_id": doc["network_snapshot_id"], "routes": routes}
    (out / "plan.json").write_text(json.dumps(pl, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "muni.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    variant = "precise_affected_maeul_gg"
    for split in ("exp", "test"):
        b = bundles_for(sw / split, out / split, tt_path, variant, [fleet])
        cmd = [sys.executable, str(RUN_MATRIX), "--bundles", str(b), "--out", str(out / f"runs_{split}"),
               "--src", str(REPO / "src"), "--jobs", str(args.jobs), "--plan", f"muni:{fleet}={out / 'plan.json'}"]
        print(split, "rc", subprocess.run(cmd, check=False).returncode, flush=True)
    print(json.dumps({k: rec[k] for k in ("plan_vehicles", "fleet", "routes", "added_segments", "dropped_routes")},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
