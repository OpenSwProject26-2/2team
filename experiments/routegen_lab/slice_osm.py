"""큰 범위(서울 전체)로 받은 OSM 차도 파일에서 구 묶음 정류장 범위(+여유)만 잘라 같은 형식으로 저장한다.

    python3 experiments/routegen_lab/slice_osm.py --src <osm/seoul_roads.json> --root <region/<구>_0610_v4> \
        --out <osm/<구>_roads.json>

범위 안 노드가 하나라도 있는 길은 길 전체(노드 포함)를 남긴다. 범위·여유는 fetch_osm.py와 같다.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", required=True, type=Path)
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--pad", type=float, default=0.01)
    args = ap.parse_args()
    with open(args.root / "network" / "stops.csv", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    la = [float(r["stop_lat"]) for r in rows]
    lo = [float(r["stop_lon"]) for r in rows]
    s, w, n, e = min(la) - args.pad, min(lo) - args.pad, max(la) + args.pad, max(lo) + args.pad
    osm = json.loads(args.src.read_text(encoding="utf-8"))
    node = {x["id"]: x for x in osm["elements"] if x["type"] == "node"}
    inside = {i for i, x in node.items() if s <= x["lat"] <= n and w <= x["lon"] <= e}
    ways = [x for x in osm["elements"] if x["type"] == "way" and any(i in inside for i in x["nodes"])]
    keep = {i for x in ways for i in x["nodes"]}
    out = {k: v for k, v in osm.items() if k != "elements"}
    out["elements"] = [node[i] for i in sorted(keep) if i in node] + ways
    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.out.with_suffix(".part")
    tmp.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    tmp.replace(args.out)          # 다른 작업이 반쯤 쓴 파일을 읽지 않도록 다 쓴 뒤 바꾼다
    print(f"bbox {s:.4f},{w:.4f},{n:.4f},{e:.4f}: 길 {len(ways):,}, 노드 {len(keep):,} → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
