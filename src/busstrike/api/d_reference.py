"""Read-only Seoul reference map from the explicitly supplied transit-data root.

The reference tables are not A GTFS and contain no verified road geometry.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

from .d_geo import DataError, MapSnapshot


def add_reference_metro_stations(snapshot: MapSnapshot, root: Path | None) -> MapSnapshot:
    """Add station locations when a bus-only A GTFS feed has no rail stops.

    These are reference coordinates, not A GTFS rail trips or a verified line order.
    """
    if root is None or any(stop.get("kind") == "metro" for stop in snapshot.stops):
        return snapshot
    archive_path = root / "bundles_v4/split/seoul_v4_1_network_gtfs_etc.zip"
    member = "seoul_routegen_inputs_0610_v4/network/stations.csv"
    if not archive_path.is_file():
        return snapshot
    existing = {str(stop["stop_id"]) for stop in snapshot.stops}
    stations = []
    with zipfile.ZipFile(archive_path) as archive:
        if member not in archive.namelist():
            return snapshot
        with archive.open(member) as raw:
            for row in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig")):
                try:
                    lon, lat = float(row["stop_lon"]), float(row["stop_lat"])
                    sid = row["stop_id"].zfill(4)
                except (KeyError, TypeError, ValueError):
                    continue
                if sid in existing or not (126.5 <= lon <= 127.5 and 37.2 <= lat <= 38.0):
                    continue
                existing.add(sid)
                stations.append({"stop_id": sid, "name": row["stop_name"],
                                 "kind": "metro", "coordinates": [lon, lat],
                                 "location_source": "seoul_routegen_inputs_0610_v4/network/stations.csv"})
    return replace(snapshot, stops=snapshot.stops + tuple(stations))


def load_reference(root: Path) -> MapSnapshot:
    required = [root / "manifest.json", root / "reference/stops.csv",
                root / "reference/routes.csv", root / "reference/route_stops.csv",
                root / "reference/subway_stations.csv"]
    if not all(path.is_file() for path in required):
        raise DataError("DATA_INCOMPLETE", "서울 참조 지도 파일이 지정 데이터 폴더에 없습니다.")
    manifest = json.loads(required[0].read_text(encoding="utf-8"))
    digest = hashlib.sha256()
    station_zip = root / "bundles_v4/split/seoul_v4_1_network_gtfs_etc.zip"
    for path in [*required, station_zip]:
        if not path.is_file():
            continue
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(str(path.stat().st_size).encode())
        digest.update(str(path.stat().st_mtime_ns).encode())

    stops = {}
    with required[1].open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            try:
                coordinates = [float(row["lon"]), float(row["lat"])]
            except (TypeError, ValueError):
                continue
            stops[row["BSST_ID"]] = {"stop_id": row["BSST_ID"], "name": row["BSST_NM"],
                                     "kind": "bus", "coordinates": coordinates}

    route_info = {}
    with required[2].open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            route_info[row["ROUTE_ID"]] = row
    order = defaultdict(list)
    kinds = {}
    agencies = {}
    with required[3].open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            rid, sid = row["ROUTE_ID"], row["BSST_ID"]
            if rid in route_info and sid in stops:
                order[rid].append((int(row["BSST_ORDER"] or 0), sid))
                kinds[rid] = "village" if row["COMBINE_TYPE"] == "마을" else "city"
                agencies[rid] = row.get("TRCM_NM") or "원본 미제공"
    routes = []
    for rid, row in route_info.items():
        sequence = [sid for _, sid in sorted(order[rid])]
        unique = list(dict.fromkeys(sequence))
        routes.append({"route_id": rid, "short_name": row.get("ROUTE_NUM") or rid,
                       "agency": agencies.get(rid, "원본 미제공"),
                       "kind": kinds.get(rid, "unknown_bus"), "coordinates": None,
                       "geometry_status": "stop_points_only_unverified",
                       "geometry_source": "reference/route_stops.csv · 정차 순서 개략",
                       "representative_stop_ids": unique,
                       "all_stop_ids": sequence, "stop_ids": unique})

    station_file = "seoul_routegen_inputs_0610_v4/network/stations.csv"
    if station_zip.is_file():
        with zipfile.ZipFile(station_zip) as archive:
            if station_file in archive.namelist():
                with archive.open(station_file) as raw:
                    for row in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig")):
                        try:
                            coordinates = [float(row["stop_lon"]), float(row["stop_lat"])]
                        except (TypeError, ValueError):
                            continue
                        sid = row["stop_id"].zfill(4)
                        stops[sid] = {"stop_id": sid, "name": row["stop_name"],
                                      "kind": "metro", "coordinates": coordinates}
    return MapSnapshot(snapshot_id="reference-" + digest.hexdigest()[:12],
                       source="reference", service_date=str(manifest.get("created", "unknown")),
                       scenario_id=None, plan_id=None, revision=None,
                       routes=tuple(routes), stops=tuple(stops.values()), demand=(), fleet=None,
                       warnings=("참조 CSV 지도 · A GTFS 아님 · 버스 도로 선형 미검증",
                                 "C/B 운행안 및 실제 파업 상태 미연결"),
                       feed_version="seoul_transit_data/reference")
