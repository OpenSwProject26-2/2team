"""Seoul official bus path adapter (data.go.kr service 15000193).

The API key is read from the environment and is never included in diagnostics.
The route ID, route name and ordered stops must agree with the A GTFS feed.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .d_bus_routing import distance_m
from .d_geo import DataError, _point
from .d_geometry import _ordered_stop_match

DEFAULT_BASE = "https://ws.bus.go.kr/api/rest/busRouteInfo"
KEY_ENV = "SEOUL_BUS_API_KEY"


def _items(root: ET.Element) -> list[dict[str, str]]:
    return [
        {child.tag: (child.text or "").strip() for child in item}
        for item in root.findall(".//itemList")
    ]


def _request(endpoint: str, route_id: str, key: str, base_url: str, timeout: int = 15) -> list[dict[str, str]]:
    if not base_url.startswith("https://"):
        raise DataError("INPUT_INVALID", "Official API key requires HTTPS")
    url = base_url.rstrip("/") + "/" + endpoint + "?" + urlencode(
        {"serviceKey": key, "busRouteId": route_id}
    )
    try:
        request = Request(url, headers={"User-Agent": "2team-D-local-route-check/0.1"})
        with urlopen(request, timeout=timeout) as response:
            payload = response.read(2_000_001)
    except OSError as error:
        raise DataError("SOURCE_UNAVAILABLE", "Seoul bus API request failed", endpoint=endpoint) from None
    if len(payload) > 2_000_000:
        raise DataError("DATA_INCOMPLETE", "Seoul bus API response exceeds size limit", endpoint=endpoint)
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        raise DataError("DATA_INCOMPLETE", "Seoul bus API response is not XML", endpoint=endpoint) from None
    code = root.findtext(".//headerCd")
    if code not in (None, "0"):
        raise DataError("SOURCE_UNAVAILABLE", "Seoul bus API rejected the request", endpoint=endpoint, response_code=code)
    return _items(root)


def _ordered_subsequence(haystack: list[str], needle: list[str]) -> bool:
    cursor = iter(haystack)
    return all(any(value == wanted for value in cursor) for wanted in needle)


def fetch_official_route(route: dict, stops: dict[str, dict], base_url: str = DEFAULT_BASE) -> dict:
    key = os.environ.get(KEY_ENV)
    if not key:
        raise DataError("SOURCE_UNAVAILABLE", "SEOUL_BUS_API_KEY is not configured")
    route_id = route["route_id"]
    ids = route.get("representative_stop_ids") or []
    if len(ids) < 2 or any(sid not in stops for sid in ids):
        raise DataError("DATA_INCOMPLETE", "A GTFS representative stop order is unavailable", route_id=route_id)
    path_rows = _request("getRoutePath", route_id, key, base_url)
    station_rows = _request("getStaionByRoute", route_id, key, base_url)
    if not path_rows or not station_rows:
        raise DataError("DATA_INCOMPLETE", "Official bus path or stop list is empty", route_id=route_id)
    if any(row.get("busRouteId") not in (None, "", route_id) for row in station_rows):
        raise DataError("DATA_VERSION_MISMATCH", "Official bus route ID differs from A GTFS", route_id=route_id)
    names = {row.get("busRouteNm") for row in station_rows if row.get("busRouteNm")}
    if names != {route["short_name"]}:
        raise DataError("DATA_VERSION_MISMATCH", "Official bus route name differs from A GTFS", route_id=route_id)
    try:
        station_rows.sort(key=lambda row: int(row["seq"]))
        path_rows.sort(key=lambda row: int(row["no"]))
        points = [[float(row["gpsX"]), float(row["gpsY"])] for row in path_rows]
        official_stops = [[float(row["gpsX"]), float(row["gpsY"])] for row in station_rows]
    except (KeyError, ValueError):
        raise DataError("DATA_INCOMPLETE", "Official bus coordinates or sequence are invalid", route_id=route_id) from None
    if len(points) < 3 or not all(_point(point) for point in points + official_stops):
        raise DataError("DATA_INCOMPLETE", "Official bus path has invalid WGS84 coordinates", route_id=route_id)
    official_ids = [row.get("station", "") for row in station_rows]
    id_match = _ordered_subsequence(official_ids, ids)
    calls = [stops[sid]["coordinates"] for sid in ids]
    if not id_match:
        # ID namespaces may differ. In that case require an ordered spatial
        # match to official stops as well as the official road path.
        if not _ordered_stop_match(official_stops, calls) or any(
            min(distance_m(call, point) for point in official_stops) > 80 for call in calls
        ):
            raise DataError("DATA_VERSION_MISMATCH", "Official stop sequence does not match A GTFS", route_id=route_id)
    if not _ordered_stop_match(points, calls):
        raise DataError("DATA_VERSION_MISMATCH", "Official path misses A GTFS stop order", route_id=route_id)
    return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": points},
            "properties": {"a_gtfs_route_id": route_id, "source_route_id": route_id,
                           "route_short_name": route["short_name"], "agency_id": route["agency"],
                           "stop_ids": ids, "geometry_source": "official",
                           "source_namespace": "Seoul bus API 15000193 / getRoutePath",
                           "source_version": "Seoul bus API source version not reported",
                           "retrieved_at": datetime.now(timezone.utc).isoformat(),
                           "official_stop_id_match": id_match,
                           "official_stop_count": len(official_stops)}}
