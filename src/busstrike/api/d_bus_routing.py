"""One-off OSRM road estimates for selected A GTFS bus routes.

This is an explicit preprocessing step. It never reroutes when a dashboard
filter changes and never labels a car route as an official bus itinerary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from datetime import datetime, timezone
from itertools import pairwise
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

from .d_geo import DataError, _point, load_a_gtfs
from .d_geometry import _ordered_stop_match
from .d_stop_order import major_trip_patterns

PUBLIC_OSRM = "https://router.project-osrm.org"
USER_AGENT = "OpenSwProject26-2-2team-D/0.1 (academic local route preview)"


def distance_m(a: list[float], b: list[float]) -> float:
    lat = math.radians((a[1] + b[1]) / 2)
    east = math.radians(a[0] - b[0]) * math.cos(lat)
    north = math.radians(a[1] - b[1])
    return 6_371_000 * math.hypot(east, north)


def validate_osrm_result(data: dict, route: dict, calls: list[list[float]]) -> dict:
    """Validate snapping, leg detours, stop order and returned road geometry."""
    if data.get("code") != "Ok" or not data.get("routes"):
        raise DataError("ROUTING_FAILED", "OSRM did not return a road route", route_id=route["route_id"], osrm_code=data.get("code"))
    path = data["routes"][0]
    points = path.get("geometry", {}).get("coordinates")
    waypoints = data.get("waypoints", [])
    legs = path.get("legs", [])
    if (
        path.get("geometry", {}).get("type") != "LineString"
        or not isinstance(points, list) or len(points) < 3
        or not all(_point(p) for p in points)
        or len(waypoints) != len(calls)
        or len(legs) != len(calls) - 1
    ):
        raise DataError("ROUTING_FAILED", "OSRM geometry or waypoint count is invalid", route_id=route["route_id"])
    snaps = [waypoint.get("distance", math.inf) for waypoint in waypoints]
    if any(not isinstance(value, (int, float)) or not math.isfinite(value) or value > 80 for value in snaps):
        raise DataError("ROUTING_FAILED", "Bus stop is too far from routed road", route_id=route["route_id"], max_snap_m=max(snaps))
    direct = [distance_m(a, b) for a, b in pairwise(calls)]
    leg_distances = [leg.get("distance", math.inf) for leg in legs]
    for index, (straight, road) in enumerate(zip(direct, leg_distances)):
        if not isinstance(road, (int, float)) or not math.isfinite(road) or road < 0:
            raise DataError("ROUTING_FAILED", "OSRM leg distance is invalid", route_id=route["route_id"], leg=index)
        if road > max(1200, straight * 6):
            raise DataError("ROUTING_FAILED", "OSRM leg has an excessive detour", route_id=route["route_id"], leg=index, road_m=round(road), straight_m=round(straight))
    total_direct = sum(direct)
    total_road = path.get("distance", math.inf)
    if not isinstance(total_road, (int, float)) or not math.isfinite(total_road) or total_road > max(2000, total_direct * 4):
        raise DataError("ROUTING_FAILED", "OSRM route has an excessive total detour", route_id=route["route_id"])
    if not _ordered_stop_match(points, calls):
        raise DataError("ROUTING_FAILED", "OSRM path does not follow GTFS stop order", route_id=route["route_id"])
    return {"points": points, "snaps": snaps, "road_m": total_road,
            "direct_m": total_direct, "legs_m": leg_distances}


def fetch_osrm_route(route: dict, stops: dict[str, dict], base_url: str = PUBLIC_OSRM, timeout: int = 25) -> dict:
    ids = route.get("representative_stop_ids") or []
    if len(ids) < 2 or any(sid not in stops for sid in ids):
        raise DataError("ROUTING_FAILED", "Route lacks a usable representative stop sequence", route_id=route["route_id"])
    if len(ids) > 50:
        parts = []
        for start in range(0, len(ids) - 1, 24):
            if parts and base_url.rstrip("/") == PUBLIC_OSRM:
                time.sleep(1.1)
            part = route | {"representative_stop_ids": ids[start:start + 25]}
            parts.append(fetch_osrm_route(part, stops, base_url, timeout))
        points = []
        for part in parts:
            segment = part["geometry"]["coordinates"]
            points.extend(segment[1:] if points and points[-1] == segment[0] else segment)
        calls = [stops[sid]["coordinates"] for sid in ids]
        if not _ordered_stop_match(points, calls):
            raise DataError("ROUTING_FAILED", "Joined OSRM chunks do not follow the stop order", route_id=route["route_id"])
        props = parts[0]["properties"].copy()
        props.update(stop_ids=ids, chunk_count=len(parts),
                     road_distance_m=round(sum(part["properties"]["road_distance_m"] for part in parts), 1),
                     direct_distance_m=round(sum(part["properties"]["direct_distance_m"] for part in parts), 1),
                     max_snap_distance_m=max(part["properties"]["max_snap_distance_m"] for part in parts),
                     max_leg_detour_ratio=max(part["properties"]["max_leg_detour_ratio"] for part in parts))
        return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": points}, "properties": props}
    calls = [stops[sid]["coordinates"] for sid in ids]
    if not all(_point(point) for point in calls):
        raise DataError("ROUTING_FAILED", "Route stop coordinate is invalid", route_id=route["route_id"])
    coords = ";".join(f"{lon:.7f},{lat:.7f}" for lon, lat in calls)
    url = (base_url.rstrip("/") + "/route/v1/driving/" + quote(coords, safe=";,.")
           + "?overview=full&geometries=geojson&steps=false&continue_straight=false")
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        data = json.load(response)
    validated = validate_osrm_result(data, route, calls)
    return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": validated["points"]},
            "properties": {
                "a_gtfs_route_id": route["route_id"], "source_route_id": route["route_id"],
                "route_short_name": route["short_name"], "agency_id": route["agency"],
                "stop_ids": ids, "geometry_source": "osrm_estimate",
                "source_namespace": "OSRM driving / OpenStreetMap road estimate",
                "source_version": "OSRM road graph version not reported",
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "profile": "driving", "road_distance_m": round(validated["road_m"], 1),
                "direct_distance_m": round(validated["direct_m"], 1),
                "max_snap_distance_m": round(max(validated["snaps"]), 1),
                "max_leg_detour_ratio": round(max(road / max(1, direct) for road, direct in zip(validated["legs_m"], (distance_m(a,b) for a,b in pairwise(calls)))), 2),
                "bus_access_verified": False,
            }}


def roadnet_route(route: dict, stops: dict[str, dict], roadnet, source_version: str) -> dict:
    """Join ordered stop pairs with the routegen lab road graph, rejecting straight fallbacks."""
    ids = route.get("representative_stop_ids") or []
    if len(ids) < 2 or any(sid not in stops or sid not in roadnet.stops for sid in ids):
        raise DataError("ROUTING_FAILED", "Route stops are missing from the GTFS or RoadNet input", route_id=route["route_id"])
    calls = [stops[sid]["coordinates"] for sid in ids]
    if any(distance_m(call, [roadnet.stops[sid][1], roadnet.stops[sid][0]]) > 80 for sid, call in zip(ids, calls)):
        raise DataError("DATA_VERSION_MISMATCH", "RoadNet and GTFS stop coordinates differ", route_id=route["route_id"])
    points: list[list[float]] = []
    for left, right in pairwise(ids):
        if left == right or left not in roadnet.stop_nodes or right not in roadnet.stop_nodes:
            raise DataError("ROUTING_FAILED", "Stop pair cannot be snapped to the road graph", route_id=route["route_id"], stop_pair=[left, right])
        segment = roadnet.path_any(left, right)
        direct = [[round(roadnet.stops[s][1], 6), round(roadnet.stops[s][0], 6)] for s in (left, right)]
        if len(segment) < 2 or segment == direct:
            raise DataError("ROUTING_FAILED", "RoadNet returned a straight fallback", route_id=route["route_id"], stop_pair=[left, right])
        points.extend(segment[1:] if points and points[-1] == segment[0] else segment)
    if not _ordered_stop_match(points, calls):
        raise DataError("ROUTING_FAILED", "RoadNet path does not follow GTFS stop order", route_id=route["route_id"])
    return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": points},
            "properties": {"a_gtfs_route_id": route["route_id"], "source_route_id": route["route_id"],
                           "route_short_name": route["short_name"], "agency_id": route["agency"],
                           "stop_ids": ids, "geometry_source": "roadnet_estimate",
                           "source_namespace": "routegen_lab RoadNet / OpenStreetMap road graph",
                           "source_version": source_version, "retrieved_at": datetime.now(timezone.utc).isoformat(),
                           "bus_access_verified": False}}


def roadnet_partial_route(route: dict, stops: dict[str, dict], roadnet, source_version: str) -> dict:
    """Keep only validated consecutive stop pairs; never bridge missing road coverage."""
    ids = route.get("representative_stop_ids") or []
    lines: list[list[list[float]]] = []
    stop_runs: list[list[str]] = []
    for left, right in pairwise(ids):
        if left == right or left not in roadnet.stop_nodes or right not in roadnet.stop_nodes:
            continue
        if left not in stops or right not in stops:
            continue
        path = roadnet.path_any(left, right)
        direct = [[round(roadnet.stops[s][1], 6), round(roadnet.stops[s][0], 6)] for s in (left, right)]
        if len(path) < 3 or path == direct or not _ordered_stop_match(
            path, [stops[left]["coordinates"], stops[right]["coordinates"]]
        ):
            continue
        if stop_runs and stop_runs[-1][-1] == left and lines[-1][-1] == path[0]:
            lines[-1].extend(path[1:])
            stop_runs[-1].append(right)
        else:
            lines.append(path)
            stop_runs.append([left, right])
    if not lines:
        raise DataError("ROUTING_FAILED", "No validated road segments for this route", route_id=route["route_id"])
    return {"type": "Feature", "geometry": {"type": "MultiLineString", "coordinates": lines},
            "properties": {"a_gtfs_route_id": route["route_id"], "source_route_id": route["route_id"],
                           "route_short_name": route["short_name"], "agency_id": route["agency"],
                           "stop_ids": ids, "partial_stop_runs": stop_runs,
                           "geometry_source": "roadnet_partial_estimate",
                           "source_namespace": "routegen_lab RoadNet / OpenStreetMap road graph",
                           "source_version": source_version,
                           "retrieved_at": datetime.now(timezone.utc).isoformat(),
                           "bus_access_verified": False}}


def roadnet_pattern_route(route: dict, stops: dict[str, dict], roadnet, source_version: str,
                          patterns: tuple[tuple[str, ...], ...]) -> dict:
    """Trace both directions, retaining only road-verified contiguous pieces."""
    lines = []
    runs = []
    pattern_indices = []
    for pattern_index, pattern in enumerate(patterns):
        if len(pattern) < 2:
            continue
        try:
            part = roadnet_partial_route(route | {"representative_stop_ids": list(pattern)},
                                         stops, roadnet, source_version)
        except DataError:
            continue
        for line, stop_run in zip(part["geometry"]["coordinates"], part["properties"]["partial_stop_runs"]):
            if stop_run not in runs:
                lines.append(line)
                runs.append(stop_run)
                pattern_indices.append(pattern_index)
    if not lines:
        raise DataError("ROUTING_FAILED", "No validated pattern road segments", route_id=route["route_id"])
    return {"type": "Feature", "geometry": {"type": "MultiLineString", "coordinates": lines},
            "properties": {"a_gtfs_route_id": route["route_id"], "source_route_id": route["route_id"],
                           "route_short_name": route["short_name"], "agency_id": route["agency"],
                           "stop_ids": route.get("representative_stop_ids") or [],
                           "partial_stop_runs": runs, "pattern_indices": pattern_indices,
                           "geometry_source": "roadnet_pattern_estimate",
                           "source_namespace": "routegen_lab RoadNet / OpenStreetMap road graph",
                           "source_version": source_version, "retrieved_at": datetime.now(timezone.utc).isoformat(),
                           "bus_access_verified": False}}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build selected bus road estimates outside the repository")
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--a-gtfs", type=Path)
    inputs.add_argument("--reference-root", type=Path, help="서울 참조 자료 루트")
    parser.add_argument("--service-date", default="2026-11-02")
    parser.add_argument("--route-id", action="append", default=[])
    parser.add_argument("--all-matching", action="store_true",
                        help="Route every active bus route whose ordered stops exist in the RoadNet region")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--append", action="store_true", help="Keep existing validated features in --out")
    parser.add_argument("--osrm-url", default=PUBLIC_OSRM)
    parser.add_argument("--source", choices=("osrm", "official", "roadnet", "roadnet-partial", "roadnet-patterns"), default="osrm")
    parser.add_argument("--roadnet-osm", type=Path, help="routegen_lab OSM road graph JSON")
    parser.add_argument("--roadnet-root", type=Path, help="extracted routegen region root with network/stops.csv")
    parser.add_argument("--seoul-api-url", default=None, help="Official API base URL override")
    args = parser.parse_args()
    roadnet = None
    roadnet_version = None
    if args.source in {"roadnet", "roadnet-partial", "roadnet-patterns"}:
        if not args.roadnet_osm or not args.roadnet_root:
            parser.error("--source roadnet requires --roadnet-osm and --roadnet-root")
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "experiments" / "routegen_lab"))
        from roadnet import RoadNet
        roadnet = RoadNet(args.roadnet_osm, args.roadnet_root)
        roadnet.build()
        roadnet_version = hashlib.sha256(args.roadnet_osm.read_bytes()).hexdigest()[:16]
    if args.a_gtfs:
        snapshot = load_a_gtfs(args.a_gtfs, args.service_date)
    else:
        from .d_reference import load_reference
        snapshot = load_reference(args.reference_root)
    by_route = {route["route_id"]: route for route in snapshot.routes}
    stops = {stop["stop_id"]: stop for stop in snapshot.stops}
    if args.all_matching and args.source not in {"roadnet", "roadnet-partial", "roadnet-patterns"}:
        parser.error("--all-matching requires a RoadNet source")
    if not args.route_id and not args.all_matching:
        parser.error("provide --route-id or --all-matching")
    route_ids = list(args.route_id)
    if args.all_matching:
        route_ids.extend(route["route_id"] for route in snapshot.routes
                         if route["kind"] in {"village", "city", "unknown_bus"}
                         and len(route.get("representative_stop_ids") or []) >= 2
                         and all(sid in roadnet.stops for sid in route["representative_stop_ids"]))
    existing = json.loads(args.out.read_text(encoding="utf-8")) if args.append and args.out.is_file() else {}
    if existing and existing.get("metadata", {}).get("a_gtfs_snapshot_id") != snapshot.snapshot_id:
        raise DataError("DATA_VERSION_MISMATCH", "Existing road file uses a different A GTFS snapshot")
    features = list(existing.get("features", []))
    failures = list(existing.get("metadata", {}).get("failures", []))
    for index, route_id in enumerate(dict.fromkeys(route_ids)):
        if index and args.source == "osrm" and args.osrm_url.rstrip("/") == PUBLIC_OSRM:
            time.sleep(1.1)
        route = by_route.get(route_id)
        if route is None or route["kind"] not in {"village", "city", "unknown_bus"}:
            failures.append({"route_id": route_id, "reason": "A GTFS active bus route not found"})
            continue
        prior = next((old for old in features if old.get("properties", {}).get("a_gtfs_route_id") == route_id), None)
        if prior and prior.get("properties", {}).get("geometry_source") == "official" and args.source != "official":
            print(json.dumps({"route_id": route_id, "status": "kept_official"}, ensure_ascii=True))
            continue
        try:
            if args.source == "official":
                from .d_bus_official import DEFAULT_BASE, fetch_official_route
                feature = fetch_official_route(route, stops, args.seoul_api_url or DEFAULT_BASE)
            elif args.source == "roadnet":
                feature = roadnet_route(route, stops, roadnet, roadnet_version)
            elif args.source == "roadnet-partial":
                feature = roadnet_partial_route(route, stops, roadnet, roadnet_version)
            elif args.source == "roadnet-patterns":
                if not args.a_gtfs:
                    raise DataError("INPUT_INVALID", "RoadNet patterns require A GTFS", route_id=route_id)
                feature = roadnet_pattern_route(route, stops, roadnet, roadnet_version,
                                                major_trip_patterns(args.a_gtfs, args.service_date, route_id))
            else:
                feature = fetch_osrm_route(route, stops, args.osrm_url)
            features = [old for old in features if old.get("properties", {}).get("a_gtfs_route_id") != route_id]
            failures = [failure for failure in failures if failure.get("route_id") != route_id]
            features.append(feature)
            print(json.dumps({"route_id": route_id, "status": feature["properties"]["geometry_source"],
                              "road_distance_m": feature["properties"].get("road_distance_m"),
                              "max_snap_distance_m": feature["properties"].get("max_snap_distance_m")}, ensure_ascii=True))
        except (OSError, ValueError, DataError) as error:
            failures = [failure for failure in failures if failure.get("route_id") != route_id]
            failures.append({"route_id": route_id, "reason": str(error),
                             "details": error.details if isinstance(error, DataError) else {}})
            print(json.dumps(failures[-1], ensure_ascii=True))
    output = {"type": "FeatureCollection", "features": features,
              "metadata": {"source": "verified bus path import", "a_gtfs_snapshot_id": snapshot.snapshot_id,
                           "service_date": snapshot.service_date, "failures": failures}}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.out), "matched": len(features), "failed": len(failures)}, ensure_ascii=True))


if __name__ == "__main__":
    main()
