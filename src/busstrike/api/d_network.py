"""District spatial filtering for the D local map, independent of GTFS IDs."""

from __future__ import annotations

import json
from dataclasses import replace
from functools import lru_cache
from itertools import pairwise
from pathlib import Path

from .d_geo import DataError, MapSnapshot, map_geojson

ROOT = Path(__file__).resolve().parents[3]
BOUNDARIES = ROOT / "web" / "operator" / "data" / "districts_all.geojson"
DISTRICT_CODES = {
    "guro": "11530",
    "dongjak": "11590",
    "gangseo": "11500",
    "gangdong": "11740",
}


@lru_cache(maxsize=1)
def _boundary_collection() -> dict:
    return json.loads(BOUNDARIES.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def districts() -> dict[str, dict]:
    source = _boundary_collection()
    by_code = {str(f["properties"]["sgg"]): f for f in source["features"]}
    if not all(code in by_code for code in DISTRICT_CODES.values()):
        raise DataError("DATA_INCOMPLETE", "자치구 경계 파일이 불완전합니다.")
    return {name: by_code[code] for name, code in DISTRICT_CODES.items()}


def district_options() -> dict:
    return {
        "source": _boundary_collection().get("source", "unknown"),
        "districts": [
            {
                "id": key,
                "name": feature["properties"]["sggnm"],
                "code": feature["properties"]["sgg"],
                "bbox": bbox(feature["geometry"]),
            }
            for key, feature in districts().items()
        ],
    }


def rings(geometry: dict) -> list[list[list[list[float]]]]:
    if geometry["type"] == "Polygon":
        return [geometry["coordinates"]]
    if geometry["type"] == "MultiPolygon":
        return geometry["coordinates"]
    raise DataError("DATA_INCOMPLETE", "자치구 경계는 Polygon이어야 합니다.")


def bbox(geometry: dict) -> list[float]:
    points = [p for polygon in rings(geometry) for ring in polygon for p in ring]
    return [
        min(p[0] for p in points),
        min(p[1] for p in points),
        max(p[0] for p in points),
        max(p[1] for p in points),
    ]


def _inside_ring(point: list[float], ring: list[list[float]]) -> bool:
    x, y = point
    inside = False
    for a, b in zip(ring, ring[1:] + ring[:1]):
        x1, y1 = a
        x2, y2 = b
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def contains(point: list[float], geometry: dict) -> bool:
    return any(
        _inside_ring(point, polygon[0])
        and not any(_inside_ring(point, hole) for hole in polygon[1:])
        for polygon in rings(geometry)
    )


def _segment_parameters(a: list[float], b: list[float], geometry: dict) -> list[float]:
    """Return all polygon-boundary crossings along a segment, including its ends."""
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    ts = [0.0, 1.0]
    for polygon in rings(geometry):
        for ring in polygon:
            for c, d in zip(ring, ring[1:] + ring[:1]):
                ex, ey = d[0] - c[0], d[1] - c[1]
                den = dx * ey - dy * ex
                if abs(den) < 1e-14:
                    continue
                cx, cy = c[0] - ax, c[1] - ay
                t = (cx * ey - cy * ex) / den
                u = (cx * dy - cy * dx) / den
                if -1e-10 <= t <= 1 + 1e-10 and -1e-10 <= u <= 1 + 1e-10:
                    ts.append(max(0.0, min(1.0, t)))
    return sorted({round(t, 12) for t in ts})


def clip_line(
    points: list[list[float]], geometry: dict, bounds: list[float] | None = None
) -> list[list[list[float]]]:
    """Clip each segment to a district; preserve traversal order and disjoint parts."""
    west, south, east, north = bounds or bbox(geometry)
    parts: list[list[list[float]]] = []
    current: list[list[float]] = []
    for a, b in pairwise(points):
        if (max(a[0], b[0]) < west or min(a[0], b[0]) > east
                or max(a[1], b[1]) < south or min(a[1], b[1]) > north):
            if len(current) > 1:
                parts.append(current)
                current = []
            continue
        ts = _segment_parameters(a, b, geometry)
        for start, end in pairwise(ts):
            t = (start + end) / 2
            mid = [a[i] + (b[i] - a[i]) * t for i in range(2)]
            if contains(mid, geometry):
                left = [a[i] + (b[i] - a[i]) * start for i in range(2)]
                right = [a[i] + (b[i] - a[i]) * end for i in range(2)]
                if not current:
                    current = [left]
                elif current[-1] != left:
                    parts.append(current)
                    current = [left]
                if current[-1] != right:
                    current.append(right)
            elif len(current) > 1:
                parts.append(current)
                current = []
    if len(current) > 1:
        parts.append(current)
    return [part for part in parts if len(part) > 1]


def network_view(
    snapshot: MapSnapshot, district: str | None, extent: str = "inside"
) -> dict:
    if district in (None, "", "all"):
        return {
            "routes": list(snapshot.routes),
            "map": map_geojson(snapshot),
            "route_stops": map_geojson(snapshot),
            "boundary": None,
            "district": "all",
            "extent": "full",
        }
    if district not in districts() or extent not in {"inside", "full"}:
        raise DataError("INPUT_INVALID", "자치구 또는 표시 범위가 올바르지 않습니다.")
    feature = districts()[district]
    polygon = feature["geometry"]
    bounds = bbox(polygon)
    stop_by_id = {stop["stop_id"]: stop for stop in snapshot.stops}
    inside_ids = {
        sid
        for sid, stop in stop_by_id.items()
        if bounds[0] <= stop["coordinates"][0] <= bounds[2]
        and bounds[1] <= stop["coordinates"][1] <= bounds[3]
        and contains(stop["coordinates"], polygon)
    }
    routes = []
    used_ids: set[str] = set()
    for route in snapshot.routes:
        ids = route.get("representative_stop_ids") or route.get("stop_ids") or []
        links = route.get("schematic_stop_links") or list(pairwise(ids))
        relevant = any(sid in inside_ids for sid in (route.get("all_stop_ids") or ids))
        if not relevant:
            relevant = any(
                clip_line(
                    [stop_by_id[a]["coordinates"], stop_by_id[b]["coordinates"]],
                    polygon, bounds,
                )
                for a, b in links
                if a in stop_by_id and b in stop_by_id
            )
        if not relevant and route.get("coordinates"):
            geometry_lines = route["coordinates"] if route.get("geometry_type") == "MultiLineString" else [route["coordinates"]]
            relevant = any(clip_line(line, polygon, bounds) for line in geometry_lines)
        if not relevant:
            continue
        item = route.copy()
        if extent == "inside":
            if route.get("coordinates"):
                geometry_lines = route["coordinates"] if route.get("geometry_type") == "MultiLineString" else [route["coordinates"]]
                segments = [part for line in geometry_lines for part in clip_line(line, polygon, bounds)]
                item["coordinates"] = segments if segments else None
                item["geometry_type"] = "MultiLineString" if segments else None
            item["schematic_stop_links"] = [
                [a, b]
                for a, b in links
                if a in stop_by_id
                and b in stop_by_id
                and clip_line(
                    [stop_by_id[a]["coordinates"], stop_by_id[b]["coordinates"]],
                    polygon, bounds,
                )
            ]
        routes.append(item)
        used_ids.update(route.get("all_stop_ids") or ids)
    visible_stops = tuple(
        stop
        for stop in snapshot.stops
        if stop["stop_id"] in (inside_ids if extent == "inside" else used_ids)
    )
    scoped = replace(snapshot, routes=tuple(routes), stops=visible_stops)
    route_stop_snapshot = replace(
        snapshot,
        routes=(),
        stops=tuple(stop for stop in snapshot.stops if stop["stop_id"] in used_ids),
    )
    return {
        "routes": routes,
        "map": map_geojson(scoped),
        "boundary": feature,
        "route_stops": map_geojson(route_stop_snapshot),
        "district": district,
        "extent": extent,
    }
