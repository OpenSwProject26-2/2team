"""Attach validated official GBIS shapes as a separate route namespace."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

from .d_geo import DataError, MapSnapshot, _point
from .d_geometry import _ordered_stop_match


def add_gbis_geometry(snapshot: MapSnapshot, path: Path) -> MapSnapshot:
    raw = path.read_bytes()
    data = json.loads(raw)
    if data.get("type") != "FeatureCollection":
        raise DataError("DATA_INCOMPLETE", "GBIS geometry must be a FeatureCollection")
    routes = list(snapshot.routes)
    stops = {str(stop["stop_id"]): stop for stop in snapshot.stops}
    seen = {route["route_id"] for route in routes}
    for feature in data.get("features", []):
        props = feature.get("properties") or {}
        source_id = str(props.get("gbis_route_id", ""))
        route_id = "gbis:" + source_id
        points = feature.get("geometry", {}).get("coordinates", [])
        entries = props.get("stops") or []
        ids = ["gbis:" + str(stop["stop_id"]) for stop in entries]
        calls = [stop.get("coordinates") for stop in entries]
        if (not source_id.isdecimal() or route_id in seen or props.get("geometry_source") != "official"
                or len(points) < 3 or len(entries) < 2 or len(ids) != len(props.get("stop_ids") or [])
                or [str(stop["stop_id"]) for stop in entries] != [str(sid) for sid in props["stop_ids"]]
                or not all(_point(point) for point in points + calls)
                or not _ordered_stop_match(points, calls)
                or float(props.get("tpss_order_overlap", 0)) < .9):
            raise DataError("DATA_VERSION_MISMATCH", "GBIS route shape or TPSS stop crosswalk failed", route_id=source_id)
        seen.add(route_id)
        for entry, stop_id in zip(entries, ids):
            stops.setdefault(stop_id, {"stop_id": stop_id, "name": entry.get("name") or stop_id,
                                       "kind": "bus", "coordinates": entry["coordinates"],
                                       "location_source": "GBIS official station list"})
        routes.append({"route_id": route_id, "short_name": props.get("route_short_name") or source_id,
                       "agency": props.get("agency") or "GBIS", "kind": "gyeonggi",
                       "coordinates": points, "geometry_status": "official",
                       "geometry_source": props.get("source_namespace"),
                       "representative_stop_ids": ids, "all_stop_ids": ids, "stop_ids": ids,
                       "source_route_id": source_id, "source_url": props.get("source_url"),
                       "tpss_order_overlap": props["tpss_order_overlap"]})
    return replace(snapshot, snapshot_id=snapshot.snapshot_id + "-gbis-" + hashlib.sha256(raw).hexdigest()[:8],
                   routes=tuple(routes), stops=tuple(stops.values()))
