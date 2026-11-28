"""Fetch and validate GBIS route shapes against local TPSS stop sequences.

The API key comes from DATA_GO_KR_SERVICE_KEY. Outputs belong outside Git.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from busstrike.api.d_geometry import _ordered_stop_match
from busstrike.api.d_network import contains, districts

BASE = "https://apis.data.go.kr/6410000/busrouteservice/v2/"
METHODS = {"route": "getBusRouteInfoItemv2", "stops": "getBusRouteStationListv2",
           "shape": "getBusRouteLineListv2"}


def fetch(route_id: str, kind: str, key: str) -> dict:
    url = BASE + METHODS[kind] + "?" + urlencode({"serviceKey": key, "routeId": route_id, "format": "json"})
    with urlopen(url, timeout=30) as response:
        data = json.load(response)
    result = data.get("response", {})
    if result.get("msgHeader", {}).get("resultCode") != 0:
        raise ValueError(f"GBIS {kind} response code {result.get('msgHeader', {}).get('resultCode')}")
    return data


def as_list(value):
    return value if isinstance(value, list) else [value] if isinstance(value, dict) else []


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--district", default="gangdong", choices=sorted(districts()))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    key = os.environ.get("DATA_GO_KR_SERVICE_KEY")
    if not key:
        parser.error("DATA_GO_KR_SERVICE_KEY is required")
    candidate_file = args.data_root / "derived/gyeonggi_2xx/gyeonggi_candidate_route_stops.csv"
    by_route = defaultdict(list)
    with candidate_file.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            by_route[row["route_id"]].append(row)
    polygon = districts()[args.district]["geometry"]
    selected = {}
    for route_id, rows in by_route.items():
        count = 0
        for row in rows:
            try:
                point = [float(row["lon"]), float(row["lat"])]
            except ValueError:
                continue
            count += contains(point, polygon)
        if count:
            selected[route_id] = count
    features, failures = [], []
    raw_dir = args.out.parent / "gbis_responses"
    raw_dir.mkdir(parents=True, exist_ok=True)
    for route_id in sorted(selected):
        try:
            raw = {kind: fetch(route_id, kind, key) for kind in METHODS}
            body = {kind: data["response"]["msgBody"] for kind, data in raw.items()}
            info = body["route"].get("busRouteInfoItem") or {}
            official_stops = sorted(as_list(body["stops"].get("busRouteStationList")),
                                    key=lambda row: int(row["stationSeq"]))
            shape = sorted(as_list(body["shape"].get("busRouteLineList")),
                           key=lambda row: int(row["lineSeq"]))
            if str(info.get("routeId")) != route_id or len(official_stops) < 2 or len(shape) < 3:
                raise ValueError("route ID or shape/stops missing")
            candidate = sorted(by_route[route_id], key=lambda row: int(row["stop_order"]))
            cand_ids = [row["stop_id"] for row in candidate]
            stop_ids = [str(row["stationId"]) for row in official_stops]
            matching = sum(block.size for block in SequenceMatcher(None, cand_ids, stop_ids, autojunk=False).get_matching_blocks())
            ratio = matching / max(1, min(len(cand_ids), len(stop_ids)))
            if ratio < .9:
                raise ValueError(f"TPSS/GBIS stop order overlap {ratio:.3f} below 0.9")
            points = [[float(row["x"]), float(row["y"])] for row in shape]
            calls = [[float(row["x"]), float(row["y"])] for row in official_stops]
            if not _ordered_stop_match(points, calls):
                raise ValueError("official path does not follow official stop order")
            if not all(126.0 <= lon <= 128.0 and 36.5 <= lat <= 38.5 for lon, lat in points + calls):
                raise ValueError("coordinates outside expected region")
            feature = {"type": "Feature", "geometry": {"type": "LineString", "coordinates": points},
                       "properties": {"gbis_route_id": route_id, "route_short_name": str(info.get("routeName", route_id)),
                                      "agency": str(info.get("companyName", "GBIS")), "stop_ids": stop_ids,
                                      "stops": [{"stop_id": str(row["stationId"]), "name": row.get("stationName", ""),
                                                 "coordinates": [float(row["x"]), float(row["y"])],
                                                 "order": int(row["stationSeq"])} for row in official_stops],
                                      "geometry_source": "official", "source_namespace": "GBIS getBusRouteLineListv2",
                                      "source_url": "https://www.data.go.kr/data/15080662/openapi.do",
                                      "tpss_order_overlap": round(ratio, 4), "district_candidate_stops": selected[route_id],
                                      "retrieved_at": datetime.now(timezone.utc).isoformat()}}
            for kind, data in raw.items():
                (raw_dir / f"gbis_{route_id}_{kind}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            features.append(feature)
            print(json.dumps({"route_id": route_id, "status": "official", "stop_match": round(ratio, 3)}, ensure_ascii=False), flush=True)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            failures.append({"route_id": route_id, "reason": str(error)[:150]})
            print(json.dumps({"route_id": route_id, "status": "rejected", "reason": str(error)[:100]}, ensure_ascii=False), flush=True)
    output = {"type": "FeatureCollection", "features": features,
              "metadata": {"source": "GBIS official route shapes", "district": args.district,
                           "candidate_count": len(selected), "matched_count": len(features), "failures": failures,
                           "retrieved_at": datetime.now(timezone.utc).isoformat(),
                           "candidate_file_sha256": hashlib.sha256(candidate_file.read_bytes()).hexdigest()}}
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.out), "matched": len(features), "failed": len(failures)}))


if __name__ == "__main__":
    main()
