"""Validate citizen requests and convert OTP itineraries to stable DTOs."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone

from .otp_client import OTPError, query

SEOUL = timezone(timedelta(hours=9))
SEOUL_BOUNDS = (37.0, 126.0, 38.0, 128.0)


class InputError(ValueError):
    pass


def coordinate(value: str, lower: float, upper: float, label: str) -> float:
    try:
        number = float(value)
    except (ValueError, TypeError) as error:
        raise InputError(f"{label} 좌표가 숫자가 아닙니다.") from error
    if not math.isfinite(number) or not lower <= number <= upper:
        raise InputError(f"{label} 좌표가 서울 권역 범위를 벗어났습니다.")
    return number


def decode_polyline(value: str | None) -> list[list[float]]:
    if not value:
        return []
    points = []
    i = lat = lon = 0
    try:
        while i < len(value):
            pair = []
            for _ in range(2):
                result = shift = 0
                while True:
                    digit = ord(value[i]) - 63
                    i += 1
                    result |= (digit & 0x1f) << shift
                    shift += 5
                    if digit < 0x20:
                        break
                pair.append((result >> 1) ^ -(result & 1))
            lat += pair[0]
            lon += pair[1]
            points.append([round(lon / 1e5, 5), round(lat / 1e5, 5)])
    except (IndexError, ValueError) as error:
        raise OTPError("OTP_RESPONSE_INVALID", "OTP 경로 형상이 잘못되었습니다.") from error
    return points


def _place(place: dict) -> dict:
    stop = place.get("stop") or {}
    return {"name": place.get("name") or "위치", "lat": place.get("lat"),
            "lon": place.get("lon"), "stop_id": stop.get("gtfsId")}


def _itinerary(raw: dict, index: int, manifest: dict) -> dict:
    legs = []
    for leg in raw.get("legs") or []:
        route = leg.get("route") or {}
        gtfs_id = route.get("gtfsId") or ""
        mode = leg.get("mode") or "UNKNOWN"
        kind = ("shuttle" if gtfs_id.startswith("SHUTTLE:") else
                "walk" if mode == "WALK" else
                "subway" if mode in {"SUBWAY", "RAIL", "TRAM"} else
                "bus" if mode == "BUS" else mode.lower())
        stats = manifest.get("shuttle_stats") if kind == "shuttle" else manifest.get("existing_stats")
        shapes_known = kind == "walk" or bool((stats or {}).get("shapes"))
        if shapes_known:
            geometry = decode_polyline((leg.get("legGeometry") or {}).get("points"))
            geometry_status = "otp" if len(geometry) > 1 else "unavailable"
        else:
            geometry = []
            for call in leg.get("stopCalls") or []:
                stop = call.get("stopLocation") or {}
                lat, lon = stop.get("lat"), stop.get("lon")
                if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
                    point = [round(lon, 5), round(lat, 5)]
                    if not geometry or geometry[-1] != point:
                        geometry.append(point)
            geometry_status = "stop_schematic" if len(geometry) > 1 else "unavailable"
        legs.append({
            "kind": kind, "mode": mode, "route_id": gtfs_id or None,
            "route_name": route.get("shortName") or route.get("longName"),
            "from": _place(leg.get("from") or {}),
            "to": _place(leg.get("to") or {}),
            "start": (leg.get("start") or {}).get("scheduledTime"),
            "end": (leg.get("end") or {}).get("scheduledTime"),
            "duration_seconds": round(leg.get("duration") or 0),
            "distance_meters": round(leg.get("distance") or 0),
            "geometry": geometry, "geometry_status": geometry_status,
        })
    if not legs:
        raise OTPError("OTP_RESPONSE_INVALID", "OTP 경로에 이동 구간이 없습니다.")
    fingerprint = hashlib.sha256(json.dumps(
        [(x["route_id"], x["start"], x["from"]["stop_id"]) for x in legs],
        ensure_ascii=False).encode("utf-8")).hexdigest()[:12]
    return {
        "id": f"route-{fingerprint}-{index}", "start": raw.get("start"),
        "end": raw.get("end"), "duration_seconds": round(raw.get("duration") or 0),
        "walk_seconds": round(raw.get("walkTime") or 0),
        "wait_seconds": round(raw.get("waitingTime") or 0),
        "transit_seconds": sum(x["duration_seconds"] for x in legs if x["kind"] not in {"walk"}),
        "transfers": raw.get("numberOfTransfers") or 0,
        "uses_shuttle": any(x["kind"] == "shuttle" for x in legs),
        "has_full_geometry": all(x["geometry_status"] == "otp" for x in legs), "legs": legs,
    }


def plan(params: dict[str, str], manifest: dict, endpoint: str) -> dict:
    lat_min, lon_min, lat_max, lon_max = SEOUL_BOUNDS
    a_lat = coordinate(params.get("origin_lat"), lat_min, lat_max, "출발지 위도")
    a_lon = coordinate(params.get("origin_lon"), lon_min, lon_max, "출발지 경도")
    b_lat = coordinate(params.get("destination_lat"), lat_min, lat_max, "목적지 위도")
    b_lon = coordinate(params.get("destination_lon"), lon_min, lon_max, "목적지 경도")
    if (a_lat, a_lon) == (b_lat, b_lon):
        raise InputError("출발지와 목적지가 같습니다.")
    try:
        departure = datetime.fromisoformat(params["departure_at"])
    except (KeyError, ValueError) as error:
        raise InputError("departure_at에 ISO 8601 출발 시각이 필요합니다.") from error
    if departure.tzinfo is None or departure.utcoffset() != timedelta(hours=9):
        raise InputError("출발 시각은 +09:00 시간대를 포함해야 합니다.")
    include = params.get("include_shuttle", "true").lower()
    if include not in {"true", "false"}:
        raise InputError("include_shuttle은 true 또는 false여야 합니다.")
    if include == "true" and not manifest.get("shuttle_route_ids"):
        raise OTPError("SHUTTLE_DATA_MISSING", "대체 셔틀 GTFS가 준비되지 않았습니다.")
    variables = {
        "origin": {"location": {"coordinate": {"latitude": a_lat, "longitude": a_lon}}},
        "destination": {"location": {"coordinate": {"latitude": b_lat, "longitude": b_lon}}},
        "dateTime": {"earliestDeparture": departure.isoformat()},
        "first": 8,
    }
    if include == "false" and manifest.get("shuttle_route_ids"):
        variables["preferences"] = {"transit": {"filters": [{
            "exclude": [{"routes": [f"SHUTTLE:{rid}" for rid in manifest["shuttle_route_ids"]]}]
        }]}}
    raw = query(endpoint, variables)
    itineraries = [_itinerary(edge["node"], i, manifest) for i, edge in enumerate(raw.get("edges") or [])
                   if edge and edge.get("node")]
    itineraries.sort(key=lambda x: (x["start"] or "", x["duration_seconds"], x["transfers"]))
    return {"version": manifest["version"], "source_kind": manifest.get("source_kind"),
            "excluded_route_ids": manifest.get("excluded_route_ids", []),
            "routes": itineraries[:8], "count": len(itineraries)}
