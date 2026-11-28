"""Optional, provenance-aware transit geometry for the D map.

The source GTFS and the external GIS response remain separate. A route receives
GIS geometry only after its GTFS stop coordinates agree with that geometry.
"""

from __future__ import annotations

import json
import hashlib
import math
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

from .d_geo import DataError, MapSnapshot, _point

ESRI_RAIL_LAYER = (
    "https://portal.esrikr.com/arcgis/rest/services/Hosted/"
    "MOIS_KR_Subway/FeatureServer/2"
)


def fetch_rail_gis(timeout: int = 15) -> dict:
    """Fetch the public rail layer once; callers decide whether to cache it."""
    query = urlencode(
        {
            "where": "1=1",
            "outFields": "sub_nm",
            "returnGeometry": "true",
            "outSR": "4326",
            "f": "geojson",
        }
    )
    with urlopen(f"{ESRI_RAIL_LAYER}/query?{query}", timeout=timeout) as response:
        if response.status != 200:
            raise DataError("DATA_INCOMPLETE", "Rail GIS request failed")
        payload = response.read(12_000_001)
    if len(payload) > 12_000_000:
        raise DataError("DATA_INCOMPLETE", "Rail GIS response exceeds size limit")
    data = json.loads(payload)
    if data.get("type") != "FeatureCollection" or not isinstance(data.get("features"), list):
        raise DataError("DATA_INCOMPLETE", "Rail GIS response is not GeoJSON")
    return data


def _segments(geometry: dict) -> list[list[list[float]]]:
    kind = geometry.get("type")
    lines = [geometry.get("coordinates")] if kind == "LineString" else geometry.get("coordinates") if kind == "MultiLineString" else None
    if not isinstance(lines, list):
        return []
    return [line for line in lines if isinstance(line, list) and len(line) > 1 and all(_point(p) for p in line)]


def _distance_to_lines(point: list[float], lines: list[list[list[float]]]) -> float:
    """Approximate shortest distance in metres in a local equirectangular plane."""
    scale_x = 111_320 * math.cos(math.radians(point[1]))
    scale_y = 111_320
    closest = math.inf
    for line in lines:
        for a, b in zip(line, line[1:]):
            ax, ay = (a[0] - point[0]) * scale_x, (a[1] - point[1]) * scale_y
            bx, by = (b[0] - point[0]) * scale_x, (b[1] - point[1]) * scale_y
            dx, dy = bx - ax, by - ay
            t = max(0.0, min(1.0, -(ax * dx + ay * dy) / (dx * dx + dy * dy))) if dx or dy else 0.0
            closest = min(closest, math.hypot(ax + t * dx, ay + t * dy))
    return closest


def rail_geometry_view(snapshot: MapSnapshot, geojson: dict) -> MapSnapshot:
    """Attach verified physical rail corridors; retain GTFS stop order separately.

    A physical corridor does not certify branch or direction-specific train paths.
    Those remain unverified in the route metadata.
    """
    features = geojson.get("features", [])
    by_name = {
        feature.get("properties", {}).get("sub_nm"): feature
        for feature in features
        if isinstance(feature, dict)
    }
    stops = {stop["stop_id"]: stop for stop in snapshot.stops}
    routes = []
    attached = 0
    for route in snapshot.routes:
        item = route.copy()
        if route["kind"] == "metro":
            name = "서울" + route["short_name"]
            feature = by_name.get(name)
            lines = _segments(feature.get("geometry", {})) if feature else []
            calls = [stops[sid]["coordinates"] for sid in route.get("representative_stop_ids", []) if sid in stops]
            distances = [_distance_to_lines(point, lines) for point in calls] if lines else []
            near = sum(distance <= 250 for distance in distances)
            # A single outdated GTFS station coordinate must not discard an
            # otherwise matching physical corridor. Expose the mismatch rather
            # than claiming the whole service path is verified.
            partial = len(calls) >= 20 and near < len(calls) and len(calls) - near <= 2 and near / len(calls) >= .95
            if len(calls) >= 3 and (near == len(calls) or partial):
                item.update(
                    coordinates=lines,
                    geometry_type="MultiLineString",
                    geometry_status="rail_gis_partial_corridor" if partial else "rail_gis_verified_corridor",
                    geometry_source="MOIS_KR_Subway/FeatureServer/2 (Esri Korea)",
                    source_version="GIS layer accessed at server start",
                    geometry_validation={
                        "gtfs_stops_checked": len(calls),
                        "stops_within_250m": near,
                        "max_stop_distance_m": round(max(distances), 1),
                        "unmatched_stop_ids": [sid for sid in route.get("representative_stop_ids", []) if sid in stops and _distance_to_lines(stops[sid]["coordinates"], lines) > 250],
                        "branch_or_direction_verified": False,
                    },
                    validation_warnings=([f"{len(calls)-near} GTFS station coordinate(s) exceed 250 m; physical corridor only."] if partial else []) + ["GTFS branch and direction not verified."],
                )
                attached += 1
            else:
                item["validation_warnings"] = [
                    f"Rail GIS mismatch: {near}/{len(calls)} GTFS stops within 250 m"
                ] if feature else ["Rail GIS line not found"]
        routes.append(item)
    return replace(
        snapshot,
        snapshot_id=snapshot.snapshot_id + "-rail-" + hashlib.sha256(
            json.dumps(geojson.get("features", []), sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:8],
        routes=tuple(routes),
        warnings=snapshot.warnings + (f"Matched {attached} A GTFS rail routes to GIS physical corridors.",),
    )


def load_rail_gis(path: Path) -> dict:
    """Read a separately shared GIS GeoJSON without modifying the source."""
    return json.loads(path.read_text(encoding="utf-8"))


def _ordered_stop_match(points: list[list[float]], calls: list[list[float]]) -> bool:
    """Require each GTFS stop to be near the path in the same travel order."""
    if len(points) < 2 or not all(_point(point) for point in points):
        return False
    last = 0
    for call in calls:
        candidates = [
            (index, _distance_to_lines(call, [[a, b]]))
            for index, (a, b) in enumerate(zip(points, points[1:]))
            if index >= last
        ]
        candidates = [(index, distance) for index, distance in candidates if distance <= 120]
        if not candidates:
            return False
        # An urban loop may pass the same stop twice. Taking the globally
        # nearest occurrence can jump to the later pass and reject a valid
        # ordered trace; the first reachable occurrence preserves order.
        last = candidates[0][0]
    return True


def bus_geometry_view(snapshot: MapSnapshot, geojson: dict) -> MapSnapshot:
    """Attach bus paths only after matching the active A GTFS or reference snapshot.

    Each feature must carry the exact representative stop order and agency from
    the active source. Matching a displayed route number alone is never sufficient.
    """
    if geojson.get("type") != "FeatureCollection" or not isinstance(geojson.get("features"), list):
        raise DataError("DATA_INCOMPLETE", "Bus geometry must be a GeoJSON FeatureCollection")
    expected_snapshot = geojson.get("metadata", {}).get("a_gtfs_snapshot_id")
    if expected_snapshot and not snapshot.snapshot_id.startswith(expected_snapshot):
        raise DataError("DATA_VERSION_MISMATCH", "Bus path crosswalk uses a different transit snapshot")
    stop_by_id = {stop["stop_id"]: stop for stop in snapshot.stops}
    by_route = {route["route_id"]: route.copy() for route in snapshot.routes}
    matched = 0
    for feature in geojson["features"]:
        props = feature.get("properties", {})
        route = by_route.get(props.get("a_gtfs_route_id"))
        source = props.get("geometry_source")
        if route is None or route["kind"] not in {"city", "village", "gyeonggi", "unknown_bus"}:
            continue
        ids = route.get("representative_stop_ids") or []
        geometry = feature.get("geometry", {})
        points = geometry.get("coordinates")
        partial = source in {"roadnet_partial_estimate", "roadnet_pattern_estimate"}
        partial_runs = props.get("partial_stop_runs") if partial else None
        pattern_indices = props.get("pattern_indices") if source == "roadnet_pattern_estimate" else None
        partial_valid = (isinstance(partial_runs, list) and isinstance(points, list)
                         and len(partial_runs) == len(points) and len(points) > 0
                         and (source != "roadnet_pattern_estimate" or
                              isinstance(pattern_indices, list) and len(pattern_indices) == len(points)
                              and all(isinstance(index, int) and index >= 0 for index in pattern_indices))
                         and all(isinstance(run, list) and len(run) >= 2
                                 and (any(ids[start:start + len(run)] == run for start in range(len(ids) - len(run) + 1))
                                      if source == "roadnet_partial_estimate" else
                                      all(sid in (route.get("all_stop_ids") or []) for sid in run))
                                 and all(sid in stop_by_id for sid in run)
                                 and _ordered_stop_match(line, [stop_by_id[sid]["coordinates"] for sid in run])
                                 for run, line in zip(partial_runs, points))) if partial else False
        if (
            geometry.get("type") != ("MultiLineString" if partial else "LineString")
            or source not in {"official", "osm_route", "osrm_estimate", "roadnet_estimate", "roadnet_partial_estimate", "roadnet_pattern_estimate"}
            or not props.get("source_route_id")
            or not props.get("source_namespace")
            or not props.get("source_version")
            or props.get("route_short_name") != route["short_name"]
            or props.get("agency_id") != route["agency"]
            or props.get("stop_ids") != ids
            or len(ids) < 2
            or any(sid not in stop_by_id for sid in ids)
            or not isinstance(points, list)
            or not (partial_valid if partial else _ordered_stop_match(points, [stop_by_id[sid]["coordinates"] for sid in ids]))
        ):
            route["validation_warnings"] = ["External bus path failed route, agency, stop order or coordinate validation"]
            continue
        route.update(
            coordinates=points,
            geometry_type=geometry["type"],
            geometry_status=source,
            geometry_source=props["source_namespace"],
            source_route_id=props["source_route_id"],
            source_version=props["source_version"],
            retrieved_at=props.get("retrieved_at"),
            road_distance_m=props.get("road_distance_m"),
            max_snap_distance_m=props.get("max_snap_distance_m"),
            max_leg_detour_ratio=props.get("max_leg_detour_ratio"),
            bus_access_verified=props.get("bus_access_verified", source == "official"),
            partial_stop_runs=partial_runs if partial else None,
            pattern_indices=pattern_indices,
            validation_warnings=[] if source == "official" else ["Only road-verified portions are shown; gaps are not connected" if partial else "Road path is not an official bus operating path"],
        )
        matched += 1
    digest = hashlib.sha256(json.dumps(geojson, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:8]
    return replace(
        snapshot,
        snapshot_id=snapshot.snapshot_id + "-bus-" + digest,
        routes=tuple(by_route[route["route_id"]] for route in snapshot.routes),
        warnings=snapshot.warnings + (f"Matched {matched} external bus paths to A GTFS.",),
    )
