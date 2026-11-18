"""구 묶음 정류장 범위(+여유)의 OSM 차도를 Overpass에서 한 번 받는다(수동 실행).

    python3 experiments/routegen_lab/fetch_osm.py --root <zip_v4/region/guro_0610_v4> --out <저장소 밖>/osm/guro_roads.json \
        [--boundaries <저장소 밖>/osm/guro_boundaries.json]

요청에는 위경도 범위와 도로 종류만 들어간다. 데이터는 ODbL(© OpenStreetMap contributors)이다.
"""

from __future__ import annotations

import argparse
import csv
import urllib.parse
import urllib.request
from pathlib import Path

HIGHWAYS = ("motorway|motorway_link|trunk|trunk_link|primary|primary_link|secondary|secondary_link|tertiary|"
            "tertiary_link|unclassified|residential|living_street|service|road|busway")
URL = "https://overpass-api.de/api/interpreter"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--pad", type=float, default=0.01, help="범위 여유(도)")
    ap.add_argument("--boundaries", type=Path, help="같은 범위에 걸치는 시·군·구 경계(admin_level 6)도 받아 저장")
    args = ap.parse_args()
    with open(args.root / "network" / "stops.csv", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    la = [float(r["stop_lat"]) for r in rows]
    lo = [float(r["stop_lon"]) for r in rows]
    s, w, n, e = min(la) - args.pad, min(lo) - args.pad, max(la) + args.pad, max(lo) + args.pad
    q = f'[out:json][timeout:180];way["highway"~"^({HIGHWAYS})$"]({s},{w},{n},{e});(._;>;);out body;'
    req = urllib.request.Request(URL, data=urllib.parse.urlencode({"data": q}).encode(),
                                 headers={"User-Agent": "busstrike-routegen-lab/0.1"})
    with urllib.request.urlopen(req, timeout=300) as r:
        body = r.read()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(body)
    print(f"bbox {s:.4f},{w:.4f},{n:.4f},{e:.4f} → {args.out} ({len(body):,} bytes)")
    if args.boundaries:
        q = f'[out:json][timeout:180];rel["boundary"="administrative"]["admin_level"="6"]({s},{w},{n},{e});out geom;'
        req = urllib.request.Request(URL, data=urllib.parse.urlencode({"data": q}).encode(),
                                     headers={"User-Agent": "busstrike-routegen-lab/0.1"})
        with urllib.request.urlopen(req, timeout=300) as r:
            body = r.read()
        args.boundaries.write_bytes(body)
        print(f"경계 → {args.boundaries} ({len(body):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
