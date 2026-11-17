"""운영자 지도용 합성/GTFS 읽기 전용 스냅샷.

M08 정식 계약이 아니다. A 원본 GTFS를 보정하지 않는다.
DEMO에 한해 별도 생성한 OSRM 자동차 도로망 추정 선형을 읽는다.
"""

from __future__ import annotations

import csv
import hashlib
import io
import itertools
import json
import math
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path


class DataError(Exception):
    def __init__(self, code: str, message: str, **details: object) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


@dataclass(frozen=True)
class MapSnapshot:
    snapshot_id: str
    source: str
    service_date: str
    scenario_id: str | None
    plan_id: str | None
    revision: int | None
    routes: tuple[dict, ...]
    stops: tuple[dict, ...]
    demand: tuple[dict, ...]
    fleet: dict | None
    warnings: tuple[str, ...]
    feed_version: str | None = None

    def summary(self) -> dict:
        counts: dict[str, int] = {}
        for route in self.routes:
            kind = route["kind"]
            counts[kind] = counts.get(kind, 0) + 1
        return {
            "snapshot_id": self.snapshot_id,
            "source": self.source,
            "service_date": self.service_date,
            "scenario_id": self.scenario_id,
            "plan_id": self.plan_id,
            "revision": self.revision,
            "route_counts": counts,
            "stop_count": len(self.stops),
            "demand_status": "synthetic"
            if self.source == "synthetic"
            else "unavailable",
            "evaluation_status": "not_independently_verified"
            if self.source == "synthetic"
            else "unavailable",
            "fleet": self.fleet,
            "feed_version": self.feed_version,
            "warnings": list(self.warnings),
        }


def _point(coordinates: list | tuple) -> bool:
    return (
        isinstance(coordinates, (list, tuple))
        and len(coordinates) == 2
        and all(isinstance(x, (int, float)) and math.isfinite(x) for x in coordinates)
        and -180 <= coordinates[0] <= 180
        and -90 <= coordinates[1] <= 90
    )


def load_demo(path: Path) -> MapSnapshot:
    raw = path.read_bytes()
    data = json.loads(raw)
    stops = data["stops"]
    routes = data["routes"]
    road_file = path.with_name("road_shapes.json")
    road_raw = road_file.read_bytes() if road_file.is_file() else b""
    if road_raw:
        try:
            road_data = json.loads(road_raw)
        except ValueError as error:
            raise DataError(
                "DATA_INCOMPLETE", "DEMO 도로 선형 JSON이 잘못되었습니다."
            ) from error
        if (
            not isinstance(road_data, dict)
            or road_data.get("schema_version") != "d-road-shapes-v1"
        ):
            raise DataError("DATA_VERSION_MISMATCH", "DEMO 도로 선형 버전이 다릅니다.")
        if not isinstance(road_data.get("routes"), list) or road_data.get(
            "route_count"
        ) != len(road_data["routes"]):
            raise DataError("DATA_INCOMPLETE", "DEMO 도로 선형 목록이 잘못되었습니다.")
        by_route = {route["route_id"]: route for route in routes}
        seen_shapes = set()
        for shape in road_data.get("routes", []):
            if not isinstance(shape, dict) or not isinstance(
                shape.get("route_id"), str
            ):
                raise DataError(
                    "DATA_INCOMPLETE", "DEMO 도로 선형 항목이 잘못되었습니다."
                )
            route_id = shape.get("route_id")
            route = by_route.get(route_id)
            coordinates = shape.get("coordinates")
            snaps = shape.get("snap_distances_m")
            if (
                not route
                or route["kind"] == "metro"
                or route_id in seen_shapes
                or shape.get("profile") != "driving"
                or shape.get("source_waypoints") != route["coordinates"]
                or not isinstance(coordinates, list)
                or len(coordinates) < 2
                or not all(_point(point) for point in coordinates)
                or not isinstance(snaps, list)
                or len(snaps) != len(route["coordinates"])
                or any(
                    type(value) not in (int, float)
                    or not math.isfinite(value)
                    or value < 0
                    or value > 120
                    for value in snaps
                )
                or type(shape.get("distance_m")) not in (int, float)
                or not math.isfinite(shape["distance_m"])
                or shape["distance_m"] <= 0
            ):
                raise DataError(
                    "DATA_VERSION_MISMATCH",
                    "DEMO 도로 선형과 노선·정류장 좌표가 일치하지 않습니다.",
                    route_id=route_id,
                )
            seen_shapes.add(route_id)
            route["coordinates"] = coordinates
            route["geometry_status"] = "osm_osrm_driving_estimate"
            route["geometry_source"] = road_data.get("source")
            route["road_distance_m"] = shape.get("distance_m")
            route["max_snap_distance_m"] = max(snaps)
    if len({s["stop_id"] for s in stops}) != len(stops):
        raise DataError("DATA_INCOMPLETE", "합성 정류장 ID가 중복됩니다.")
    if len({r["route_id"] for r in routes}) != len(routes):
        raise DataError("DATA_INCOMPLETE", "합성 노선 ID가 중복됩니다.")
    for stop in stops:
        if not _point(stop["coordinates"]):
            raise DataError(
                "DATA_INCOMPLETE",
                "합성 좌표가 잘못되었습니다.",
                stop_id=stop["stop_id"],
            )
    for route in routes:
        if len(route["coordinates"]) < 2 or not all(
            _point(p) for p in route["coordinates"]
        ):
            raise DataError(
                "DATA_INCOMPLETE",
                "합성 노선 형상이 잘못되었습니다.",
                route_id=route["route_id"],
            )
    by_stop = {stop["stop_id"]: stop for stop in stops}
    demand = []
    # 수요 셀은 합성 예제에만 적용하는 결정론적 분포다. 실제 OD에는 사용하지 않는다.
    for hub in data["demand_hubs"]:
        stop = by_stop[hub["stop_id"]]
        destinations = hub.get("destination_stop_ids")
        if (
            not isinstance(destinations, list)
            or not 1 <= len(destinations) <= 10
            or any(
                not isinstance(sid, str) or sid not in by_stop or sid == hub["stop_id"]
                for sid in destinations
            )
        ):
            raise DataError(
                "DATA_INCOMPLETE",
                "합성 OD 도착 정류장이 잘못되었습니다.",
                stop_id=hub["stop_id"],
            )
        lon, lat = stop["coordinates"]
        for index in range(8):
            angle = index * math.pi / 4 + (len(demand) % 3) * 0.13
            distance = 0.0011 + (index % 3) * 0.00045
            count = max(1, round(hub["weight"] * (0.8 + index * 0.055) / 8))
            origin = round(count * hub["origin_fraction"])
            demand.append(
                {
                    "cell_id": f"{hub['stop_id']}-{index}",
                    "stop_id": hub["stop_id"],
                    # 가상 OD 도착지는 fixture에 명시한다.
                    # 실제 A 집계 OD나 정류장 ID 매핑에 사용하지 않는다.
                    "destination_stop_id": destinations[index % len(destinations)],
                    "coordinates": [
                        round(lon + math.cos(angle) * distance, 7),
                        round(lat + math.sin(angle) * distance * 0.68, 7),
                    ],
                    "base_count": count,
                    "origin_count": origin,
                    "destination_count": count - origin,
                    "source": "synthetic",
                }
            )
    return MapSnapshot(
        snapshot_id="demo-" + hashlib.sha256(raw + road_raw).hexdigest()[:16],
        source="synthetic",
        service_date=data["service_date"],
        scenario_id=data["scenario_id"],
        plan_id=data["plan_id"],
        revision=data["revision"],
        routes=tuple(routes),
        stops=tuple(stops),
        demand=tuple(demand),
        fleet=data["fleet"],
        warnings=(
            "가상 노선·정류장·수요입니다. 실제 배차나 수송 평가가 아닙니다.",
            "OSRM 자동차 도로망 추정 선형은 버스 통행·운행 가능성을 검증하지 않았습니다."
            if road_raw
            else "합성 정류장 연결선은 실제 도로 형상이 아닙니다.",
        ),
        feed_version=data["schema_version"],
    )


def _gtfs_archive(path: Path) -> zipfile.ZipFile:
    """A 배포본의 최대 3단계 중첩 ZIP 또는 직접 GTFS ZIP을 연다."""
    archive = zipfile.ZipFile(path)
    for _ in range(3):
        names = [name.lower().rsplit("/", 1)[-1] for name in archive.namelist()]
        if "stops.txt" in names and "routes.txt" in names:
            return archive
        nested = [name for name in archive.namelist() if name.lower().endswith(".zip")]
        preferred = [
            name for name in nested if "10_data_walk-links-bus-rail" in name.lower()
        ]
        if not preferred:
            preferred = [
                name
                for name in nested
                if name.lower().endswith("gtfs_bus_rail_combined.zip")
            ]
        if len(preferred) != 1:
            archive.close()
            raise DataError("DATA_INCOMPLETE", "GTFS ZIP을 찾을 수 없습니다.")
        entry = archive.getinfo(preferred[0])
        if entry.file_size > 500_000_000:
            archive.close()
            raise DataError("DATA_INCOMPLETE", "GTFS ZIP이 허용 크기를 넘었습니다.")
        payload = archive.read(entry)
        archive.close()
        archive = zipfile.ZipFile(io.BytesIO(payload))
    archive.close()
    raise DataError("DATA_INCOMPLETE", "GTFS ZIP 중첩 깊이가 너무 큽니다.")


def _rows(archive: zipfile.ZipFile, filename: str, required: set[str] | None = None):
    matches = [
        name
        for name in archive.namelist()
        if name.lower().rsplit("/", 1)[-1] == filename
    ]
    if not matches:
        return None
    with (
        archive.open(matches[0]) as stream,
        io.TextIOWrapper(stream, encoding="utf-8-sig", newline="") as text,
    ):
        reader = csv.DictReader(text)
        if required and not required <= set(reader.fieldnames or []):
            raise DataError(
                "DATA_INCOMPLETE", "GTFS 필수 열이 부족합니다.", file=filename
            )
        return list(reader)


def _representative_stop_ids(
    archive: zipfile.ZipFile,
    representative_trips: dict[str, str],
    rail_trip_ids: set[str],
    active_trip_to_route: dict[str, str],
) -> tuple[
    dict[str, list[str]], set[str], dict[str, list[str]], dict[str, list[list[str]]]
]:
    """대표 순서·정차점 집합을 읽고 철도 운행편만 인접 역 추출용으로 모은다."""
    matches = [
        name
        for name in archive.namelist()
        if name.lower().rsplit("/", 1)[-1] == "stop_times.txt"
    ]
    if not matches:
        raise DataError("DATA_INCOMPLETE", "GTFS stop_times.txt가 없습니다.")
    target_trip_to_route = {
        trip_id: route_id for route_id, trip_id in representative_trips.items()
    }
    calls: dict[str, list[tuple[int, str]]] = {
        route_id: [] for route_id in representative_trips
    }
    all_stops: dict[str, set[str]] = {
        route_id: set() for route_id in representative_trips
    }
    rail_stops: set[str] = set()
    rail_calls: dict[str, list[tuple[int, str]]] = {}
    with (
        archive.open(matches[0]) as stream,
        io.TextIOWrapper(stream, encoding="utf-8-sig", newline="") as text,
    ):
        reader = csv.DictReader(text)
        if not {"trip_id", "stop_id", "stop_sequence"} <= set(reader.fieldnames or []):
            raise DataError("DATA_INCOMPLETE", "stop_times.txt 필수 열이 부족합니다.")
        for row in reader:
            tid = row["trip_id"]
            active_route = active_trip_to_route.get(tid)
            if active_route is not None:
                all_stops[active_route].add(row["stop_id"])
            if tid in rail_trip_ids:
                rail_stops.add(row["stop_id"])
                try:
                    rail_sequence = int(row["stop_sequence"])
                except (ValueError, TypeError) as error:
                    raise DataError(
                        "DATA_INCOMPLETE",
                        "지하철 stop_sequence가 잘못되었습니다.",
                        trip_id=tid,
                    ) from error
                rail_calls.setdefault(tid, []).append((rail_sequence, row["stop_id"]))
            route_id = target_trip_to_route.get(tid)
            if route_id is not None:
                try:
                    sequence = int(row["stop_sequence"])
                except (ValueError, TypeError) as error:
                    raise DataError(
                        "DATA_INCOMPLETE",
                        "대표 운행편 stop_sequence가 잘못되었습니다.",
                        trip_id=tid,
                    ) from error
                calls[route_id].append((sequence, row["stop_id"]))
    rail_links: dict[str, set[tuple[str, str]]] = {}
    for trip_id, rows in rail_calls.items():
        route_id = active_trip_to_route[trip_id]
        ordered = [stop_id for _, stop_id in sorted(rows)]
        links = rail_links.setdefault(route_id, set())
        for left, right in itertools.pairwise(ordered):
            if left != right:
                links.add(tuple(sorted((left, right))))
    return (
        {
            route_id: [sid for _, sid in sorted(rows)]
            for route_id, rows in calls.items()
        },
        rail_stops,
        {route_id: sorted(ids) for route_id, ids in all_stops.items()},
        {
            route_id: [list(link) for link in sorted(links)]
            for route_id, links in rail_links.items()
        },
    )


def load_a_gtfs(path: Path, service_date: str) -> MapSnapshot:
    """정상 GTFS에서 지도용 정류장·노선·공식 shapes만 가져온다.

    실제 시간표/OD/파업 범위는 이 지도 어댑터가 판정하지 않는다.
    """
    if not path.is_file():
        raise DataError("DATA_INCOMPLETE", "A GTFS 파일이 없습니다.", path=str(path))
    try:
        archive = _gtfs_archive(path)
    except (OSError, zipfile.BadZipFile) as error:
        raise DataError("DATA_INCOMPLETE", "A GTFS ZIP을 열 수 없습니다.") from error
    try:
        stops_raw = _rows(
            archive, "stops.txt", {"stop_id", "stop_name", "stop_lat", "stop_lon"}
        )
        routes_raw = _rows(archive, "routes.txt", {"route_id", "route_type"})
        trips_raw = _rows(archive, "trips.txt", {"route_id", "service_id", "trip_id"})
        calendar = _rows(archive, "calendar.txt") or []
        dates = _rows(archive, "calendar_dates.txt") or []
        feed = _rows(archive, "feed_info.txt") or []
        if not stops_raw or not routes_raw or not trips_raw:
            raise DataError("DATA_INCOMPLETE", "GTFS 필수 파일 또는 행이 없습니다.")
        day = service_date.replace("-", "")
        if len(day) != 8 or not day.isdecimal():
            raise DataError("INPUT_INVALID", "서비스일은 YYYY-MM-DD여야 합니다.")
        try:
            weekday = date.fromisoformat(service_date).weekday()
        except ValueError as error:
            raise DataError(
                "INPUT_INVALID", "서비스일은 YYYY-MM-DD여야 합니다."
            ) from error
        weekdays = (
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        )
        active = {
            row["service_id"]
            for row in calendar
            if row.get("start_date", "99999999")
            <= day
            <= row.get("end_date", "00000000")
            and row.get(weekdays[weekday]) == "1"
        }
        for row in dates:
            if row.get("date") == day:
                if row.get("exception_type") == "1":
                    active.add(row["service_id"])
                elif row.get("exception_type") == "2":
                    active.discard(row["service_id"])
        if not active:
            raise DataError(
                "OUT_OF_SCOPE",
                "GTFS 서비스일 범위 밖입니다.",
                service_date=service_date,
            )
        route_ids = {
            row["route_id"] for row in trips_raw if row["service_id"] in active
        }
        rail_route_ids = {
            row["route_id"]
            for row in routes_raw
            if row["route_type"] in {"0", "1", "2"}
        }
        shape_by_route: dict[str, str] = {}
        representative_trips: dict[str, str] = {}
        rail_trip_ids: set[str] = set()
        active_trip_to_route: dict[str, str] = {}
        for trip in trips_raw:
            if trip["service_id"] in active:
                active_trip_to_route[trip["trip_id"]] = trip["route_id"]
                representative_trips.setdefault(trip["route_id"], trip["trip_id"])
                if trip["route_id"] in rail_route_ids:
                    rail_trip_ids.add(trip["trip_id"])
                if trip.get("shape_id") and trip["route_id"] not in shape_by_route:
                    shape_by_route[trip["route_id"]] = trip["shape_id"]
        representative_stops, rail_stops, all_route_stops, rail_links = (
            _representative_stop_ids(
                archive, representative_trips, rail_trip_ids, active_trip_to_route
            )
        )
        stop_ids: set[str] = set()
        stops = []
        for row in stops_raw:
            sid = row["stop_id"]
            if sid in stop_ids:
                raise DataError(
                    "DATA_INCOMPLETE", "GTFS 정류장 ID가 중복됩니다.", stop_id=sid
                )
            stop_ids.add(sid)
            try:
                coords = [float(row["stop_lon"]), float(row["stop_lat"])]
            except (ValueError, TypeError):
                continue
            if not _point(coords):
                continue
            stops.append(
                {
                    "stop_id": sid,
                    "name": row["stop_name"],
                    "kind": "metro" if sid in rail_stops else "bus",
                    "coordinates": coords,
                    "source_namespace": "a_gtfs",
                }
            )
        shapes: dict[str, list[tuple[int, list[float]]]] = {
            sid: [] for sid in shape_by_route.values()
        }
        if shapes:
            for row in (
                _rows(
                    archive,
                    "shapes.txt",
                    {"shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence"},
                )
                or []
            ):
                if row["shape_id"] in shapes:
                    try:
                        coords = [
                            float(row["shape_pt_lon"]),
                            float(row["shape_pt_lat"]),
                        ]
                        sequence = int(row["shape_pt_sequence"])
                    except (ValueError, TypeError):
                        continue
                    if _point(coords):
                        shapes[row["shape_id"]].append((sequence, coords))
        routes = []
        seen = set()
        for row in routes_raw:
            rid = row["route_id"]
            if rid in seen:
                raise DataError(
                    "DATA_INCOMPLETE", "GTFS 노선 ID가 중복됩니다.", route_id=rid
                )
            seen.add(rid)
            if rid not in route_ids:
                continue
            route_type = row["route_type"]
            route_desc = row.get("route_desc", "")
            kind = (
                "metro"
                if route_type in {"0", "1", "2"}
                else "village"
                if route_type == "3" and route_desc == "서울마을버스"
                else "city"
                if route_type == "3"
                and route_desc
                in {"서울간선버스", "서울지선버스", "서울광역버스", "서울순환버스"}
                else "unknown_bus"
                if route_type == "3"
                else "unknown"
            )
            shape = sorted(
                shapes.get(shape_by_route.get(rid, ""), []), key=lambda item: item[0]
            )
            routes.append(
                {
                    "route_id": rid,
                    "short_name": row.get("route_short_name") or rid,
                    "agency": row.get("agency_id") or "미분류",
                    "kind": kind,
                    "route_desc": route_desc or None,
                    "geometry_status": "gtfs_shape"
                    if len(shape) >= 2
                    else "unavailable",
                    "coordinates": [point for _, point in shape]
                    if len(shape) >= 2
                    else None,
                    "representative_stop_ids": representative_stops.get(rid, []),
                    "all_stop_ids": all_route_stops.get(rid, []),
                    "schematic_stop_links": rail_links.get(rid, []),
                    "source_namespace": "a_gtfs",
                }
            )
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        warnings = [
            "A 정상 GTFS 지도 자료입니다. 버스 운행시각은 근사 생성 자료일 수 있습니다.",
            "OD·파업 노선·셔틀·평가·차량 자료는 연결되지 않았습니다.",
        ]
        if not shapes:
            warnings.append(
                "shapes.txt가 없어 실제 노선 선형이 없습니다. 지도 점선은 정차 관계 개략도이며 실제 도로·철로가 아닙니다."
            )
        return MapSnapshot(
            snapshot_id="a-gtfs-" + digest[:16],
            source="a_gtfs_approximate_schedule",
            service_date=service_date,
            scenario_id=None,
            plan_id=None,
            revision=None,
            routes=tuple(routes),
            stops=tuple(stops),
            demand=(),
            fleet=None,
            warnings=tuple(warnings),
            feed_version=feed[0].get("feed_version") if feed else None,
        )
    finally:
        archive.close()


def map_geojson(
    snapshot: MapSnapshot, bbox: tuple[float, float, float, float] | None = None
) -> dict:
    features = []
    for route in snapshot.routes:
        if route["coordinates"]:
            features.append(
                {
                    "type": "Feature",
                    "id": route["route_id"],
                    "geometry": {
                        "type": "LineString",
                        "coordinates": route["coordinates"],
                    },
                    "properties": {
                        key: value
                        for key, value in route.items()
                        if key != "coordinates"
                    }
                    | {"feature_type": "route", "source": snapshot.source},
                }
            )
    for stop in snapshot.stops:
        lon, lat = stop["coordinates"]
        if bbox and not (bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]):
            continue
        features.append(
            {
                "type": "Feature",
                "id": stop["stop_id"],
                "geometry": {"type": "Point", "coordinates": stop["coordinates"]},
                "properties": {
                    key: value for key, value in stop.items() if key != "coordinates"
                }
                | {"feature_type": "stop", "source": snapshot.source},
            }
        )
    return {
        "type": "FeatureCollection",
        "features": features,
        "snapshot_id": snapshot.snapshot_id,
        "source": snapshot.source,
    }


def demand_geojson(snapshot: MapSnapshot, hour: str, mode: str) -> dict:
    if hour not in {"06", "07", "08", "09", "18"}:
        raise DataError("OUT_OF_SCOPE", "합성 예제의 지원 시간대 밖입니다.", hour=hour)
    if mode not in {"total", "origin", "dest", "unmet", "affected"}:
        raise DataError("INPUT_INVALID", "지원하지 않는 수요 모드입니다.", mode=mode)
    if snapshot.source != "synthetic":
        raise DataError("DATA_INCOMPLETE", "실제 집계 OD가 연결되지 않았습니다.")
    factor = {"06": 0.65, "07": 1.0, "08": 1.24, "09": 0.88, "18": 1.13}[hour]
    features = []
    for cell in snapshot.demand:
        values = {
            "total": cell["base_count"],
            "origin": cell["origin_count"],
            "dest": cell["destination_count"],
        }
        # 파업 영향과 미충족은 운행/평가 입력이 없어 계산할 수 없다.
        value = round(values[mode] * factor) if mode in values else None
        features.append(
            {
                "type": "Feature",
                "id": cell["cell_id"],
                "geometry": {"type": "Point", "coordinates": cell["coordinates"]},
                "properties": {
                    "feature_type": "demand",
                    "cell_id": cell["cell_id"],
                    "stop_id": cell["stop_id"],
                    "passenger_count": value,
                    "source": "synthetic",
                    "status": "available" if value is not None else "not_computed",
                },
            }
        )
    return {
        "type": "FeatureCollection",
        "features": features,
        "snapshot_id": snapshot.snapshot_id,
        "source": snapshot.source,
        "mode": mode,
        "hour": hour,
        "total": sum(
            feature["properties"]["passenger_count"] or 0 for feature in features
        ),
        "status": "available"
        if mode in {"total", "origin", "dest"}
        else "not_computed",
    }
