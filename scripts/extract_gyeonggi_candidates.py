"""Extract TPSS 2xx route candidates without claiming GBIS route verification.

Outputs belong under seoul_transit_data/derived, outside the Git repository.
TPSS supplies route and stop IDs/order; the Seoul reference supplies only a
subset of stop coordinates. No straight-line geometry is fabricated.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

SOURCE_NAMES = (
    "tpss_sta_route_hpasngr_2026.10.26-11.01.zip",
    "tpss_sta_route_hpasngr_2026.11.02-11.08.zip",
)
TURN_NAMES = tuple(name.replace("hpasngr", "hturn") for name in SOURCE_NAMES)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def extract(root: Path, out: Path) -> dict:
    root = root.resolve()
    out = out.resolve()
    if not out.is_relative_to(root):
        raise ValueError("Output must stay inside the specified data root")
    stops_path = root / "reference" / "stops.csv"
    with stops_path.open(encoding="utf-8-sig", newline="") as source:
        stops = {row["BSST_ID"]: row for row in csv.DictReader(source)}
    dates: dict[str, set[str]] = defaultdict(set)
    latest: dict[str, str] = {}
    sequences: dict[str, list[tuple[int, str]]] = defaultdict(list)
    operated_dates: dict[str, set[str]] = defaultdict(set)
    source_files = []
    for name in SOURCE_NAMES:
        path = root / "sources" / "tpss" / name
        source_files.append({"path": str(path.relative_to(root)).replace("\\", "/"), "sha256": sha256(path)})
        with zipfile.ZipFile(path) as bundle, bundle.open(bundle.namelist()[0]) as raw:
            reader = csv.DictReader(io.TextIOWrapper(raw, encoding="cp949", newline=""))
            date_col, route_col, stop_col = reader.fieldnames[:3]
            order_col = reader.fieldnames[-1]
            for row in reader:
                route_id = row[route_col].strip()
                if not route_id.startswith("2") or not route_id.isdigit():
                    continue
                date = row[date_col].replace("-", "").replace(".", "")
                if len(date) == 8:
                    dates[route_id].add(date)
                stop_id = row[stop_col].strip()
                try:
                    order = int(row[order_col])
                except (TypeError, ValueError):
                    continue
                if not stop_id or order < 1 or len(date) != 8:
                    continue
                if date > latest.get(route_id, ""):
                    latest[route_id] = date
                    sequences[route_id] = []
                if date == latest[route_id]:
                    sequences[route_id].append((order, stop_id))
    for name in TURN_NAMES:
        path = root / "sources" / "tpss" / name
        source_files.append({"path": str(path.relative_to(root)).replace("\\", "/"), "sha256": sha256(path)})
        with zipfile.ZipFile(path) as bundle, bundle.open(bundle.namelist()[0]) as raw:
            reader = csv.DictReader(io.TextIOWrapper(raw, encoding="cp949", newline=""))
            date_col, route_col, _, turns_col = reader.fieldnames[:4]
            for row in reader:
                route_id = row[route_col].strip()
                if route_id not in dates:
                    continue
                try:
                    turns = float(row[turns_col] or 0)
                except ValueError:
                    continue
                if turns > 0:
                    operated_dates[route_id].add(row[date_col].replace("-", "").replace(".", ""))
    out.mkdir(parents=True, exist_ok=True)
    inventory_path = out / "gyeonggi_route_candidates.csv"
    ordered_path = out / "gyeonggi_candidate_route_stops.csv"
    route_rows = []
    stop_rows = []
    for route_id in sorted(dates):
        calls = sorted(set(sequences.get(route_id, [])), key=lambda item: (item[0], item[1]))
        mapped = 0
        adjacent = 0
        previous_known = False
        for order, stop_id in calls:
            stop = stops.get(stop_id)
            has_coord = bool(stop and stop.get("lat") and stop.get("lon"))
            mapped += has_coord
            adjacent += has_coord and previous_known
            previous_known = has_coord
            stop_rows.append({"route_id": route_id, "source_date": latest[route_id],
                              "stop_order": order, "stop_id": stop_id,
                              "stop_name": stop["BSST_NM"] if stop else "",
                              "lat": stop["lat"] if has_coord else "",
                              "lon": stop["lon"] if has_coord else "",
                              "coordinate_source": "reference/stops.csv" if has_coord else ""})
        route_rows.append({"route_id": route_id, "classification": "2xx_GBIS_candidate_unverified",
                           "latest_sequence_date": latest.get(route_id, ""),
                           "observed_date_count": len(dates[route_id]),
                           "positive_turn_date_count": len(operated_dates[route_id]),
                           "ordered_calls": len(calls), "unique_stops": len({sid for _, sid in calls}),
                           "calls_with_coordinates": mapped,
                           "coordinate_coverage_percent": round(100 * mapped / len(calls), 2) if calls else 0,
                           "adjacent_geocoded_pairs": adjacent,
                           "route_name": "", "operator": "", "direction": "",
                           "geometry_status": "not_available",
                           "stop_order_status": "available" if calls else "no_positive_order"})
    with inventory_path.open("w", encoding="utf-8-sig", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=list(route_rows[0]))
        writer.writeheader(); writer.writerows(route_rows)
    with ordered_path.open("w", encoding="utf-8-sig", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=list(stop_rows[0]))
        writer.writeheader(); writer.writerows(stop_rows)
    summary = {"generated_at_utc": datetime.now(timezone.utc).isoformat(),
               "source_files": source_files, "coordinate_source": "reference/stops.csv",
               "selection": "numeric TPSS route IDs starting with 2; candidate, not verified Gyeonggi classification",
               "processing": "latest available date per route; de-duplicate route/order/stop; preserve order; exact stop-ID coordinate join",
               "route_count": len(route_rows), "ordered_call_count": len(stop_rows),
               "routes_with_positive_turns": sum(bool(operated_dates[route_id]) for route_id in dates),
               "geocoded_call_count": sum(int(row["calls_with_coordinates"]) for row in route_rows),
               "route_name_verified": 0, "operator_verified": 0, "direction_verified": 0,
               "official_geometry_count": 0, "files": [inventory_path.name, ordered_path.name]}
    (out / "gyeonggi_candidates_manifest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(extract(args.data_root, args.out), ensure_ascii=False, indent=2))
