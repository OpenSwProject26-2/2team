"""Read-only source adapters and validated OTP feed preparation."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import shutil
import zipfile
from pathlib import Path

REQUIRED = {"agency.txt", "routes.txt", "trips.txt", "stops.txt", "stop_times.txt"}
SCHEDULE = {"calendar.txt", "calendar_dates.txt"}
ID_FIELDS = {
    "agency.txt": {"agency_id": "agency_id"},
    "routes.txt": {"route_id": "route_id", "agency_id": "agency_id"},
    "trips.txt": {"trip_id": "trip_id", "route_id": "route_id", "service_id": "service_id", "shape_id": "shape_id", "block_id": "block_id"},
    "stops.txt": {"stop_id": "stop_id", "parent_station": "stop_id"},
    "stop_times.txt": {"trip_id": "trip_id", "stop_id": "stop_id"},
    "calendar.txt": {"service_id": "service_id"},
    "calendar_dates.txt": {"service_id": "service_id"},
    "shapes.txt": {"shape_id": "shape_id"},
    "transfers.txt": {"from_stop_id": "stop_id", "to_stop_id": "stop_id"},
    "frequencies.txt": {"trip_id": "trip_id"},
}
PREFIX = {"agency_id": "CR_A_", "route_id": "CR_R_", "trip_id": "CR_T_",
          "stop_id": "CR_S_", "service_id": "CR_V_", "shape_id": "CR_H_", "block_id": "CR_B_"}


class FeedError(ValueError):
    pass


def _tables_from_zip(blob: bytes, depth: int = 0) -> dict[str, bytes]:
    if depth > 3:
        raise FeedError("GTFS ZIP 중첩 깊이가 너무 큽니다.")
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        names = {Path(name).name: name for name in archive.namelist()
                 if not name.endswith("/")}
        if REQUIRED <= names.keys() and SCHEDULE & names.keys():
            return {base: archive.read(name) for base, name in names.items() if base.endswith(".txt")}
        children = [name for name in archive.namelist() if name.lower().endswith(".zip")]
        for name in children:
            try:
                return _tables_from_zip(archive.read(name), depth + 1)
            except (FeedError, zipfile.BadZipFile):
                continue
    raise FeedError("ZIP 안에 필수 GTFS 파일과 운행일 정보가 없습니다.")


def read_feed(path: Path) -> dict[str, bytes]:
    if path.is_dir():
        tables = {p.name: p.read_bytes() for p in path.glob("*.txt")}
    elif path.is_file():
        tables = _tables_from_zip(path.read_bytes())
    else:
        raise FeedError(f"GTFS 경로가 없습니다: {path}")
    missing = REQUIRED - tables.keys()
    if missing or not SCHEDULE & tables.keys():
        raise FeedError(f"GTFS 파일 누락: {sorted(missing)}; calendar 또는 calendar_dates 필요")
    return tables


def rows(tables: dict[str, bytes], name: str) -> tuple[list[str], list[dict[str, str]]]:
    try:
        reader = csv.DictReader(io.StringIO(tables[name].decode("utf-8-sig"), newline=""))
        fields = reader.fieldnames or []
        values = list(reader)
    except (KeyError, UnicodeError, csv.Error) as error:
        raise FeedError(f"{name}을 읽을 수 없습니다.") from error
    if not fields or any(None in row for row in values):
        raise FeedError(f"{name} 형식이 잘못되었습니다.")
    return fields, values


def encode(fields: list[str], values: list[dict[str, str]]) -> bytes:
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(values)
    return out.getvalue().encode("utf-8")


def validate(tables: dict[str, bytes]) -> dict:
    stop_fields, stops = rows(tables, "stops.txt")
    route_fields, routes = rows(tables, "routes.txt")
    trip_fields, trips = rows(tables, "trips.txt")
    call_fields, calls = rows(tables, "stop_times.txt")
    for name, fields, required in (
        ("stops.txt", stop_fields, {"stop_id", "stop_name", "stop_lat", "stop_lon"}),
        ("routes.txt", route_fields, {"route_id", "route_type"}),
        ("trips.txt", trip_fields, {"trip_id", "route_id", "service_id"}),
        ("stop_times.txt", call_fields, {"trip_id", "stop_id", "stop_sequence", "arrival_time", "departure_time"}),
    ):
        if not required <= set(fields):
            raise FeedError(f"{name} 필수 열 누락: {sorted(required - set(fields))}")
    stop_ids = {r["stop_id"] for r in stops}
    route_ids = {r["route_id"] for r in routes}
    trip_ids = {r["trip_id"] for r in trips}
    if len(stop_ids) != len(stops) or len(route_ids) != len(routes) or len(trip_ids) != len(trips):
        raise FeedError("정류장·노선·운행편 ID가 중복됩니다.")
    if any(r["route_id"] not in route_ids for r in trips):
        raise FeedError("운행편이 없는 노선을 참조합니다.")
    if any(r["trip_id"] not in trip_ids or r["stop_id"] not in stop_ids for r in calls):
        raise FeedError("정차 시간이 없는 운행편·정류장을 참조합니다.")
    if not routes or not trips or not calls:
        raise FeedError("빈 GTFS로는 경로를 검색할 수 없습니다.")
    import math
    for stop in stops:
        try:
            lat, lon = float(stop["stop_lat"]), float(stop["stop_lon"])
        except ValueError as error:
            raise FeedError("정류장 좌표가 잘못되었습니다.") from error
        if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
            raise FeedError("정류장 좌표가 잘못되었습니다.")
    services = set()
    for name in SCHEDULE & tables.keys():
        _, values = rows(tables, name)
        services.update(r["service_id"] for r in values)
    if "shapes.txt" in tables:
        shape_ids = {r["shape_id"] for r in rows(tables, "shapes.txt")[1]}
        if any(not r.get("shape_id") or r["shape_id"] not in shape_ids for r in trips):
            raise FeedError("Some trips have no matching shape_id.")
    if any(r["service_id"] not in services for r in trips):
        raise FeedError("운행편의 service_id가 운행일 표에 없습니다.")
    return {"stops": len(stops), "routes": len(routes), "trips": len(trips),
            "stop_times": len(calls), "shapes": "shapes.txt" in tables}


def remove_struck_routes(tables: dict[str, bytes], affected: set[str]) -> dict[str, bytes]:
    route_fields, routes = rows(tables, "routes.txt")
    available = {r["route_id"] for r in routes}
    unknown = affected - available
    if unknown:
        raise FeedError(f"파업 노선 ID가 A GTFS에 없습니다: {sorted(unknown)[:10]}")
    trip_fields, trips = rows(tables, "trips.txt")
    kept_trips = [r for r in trips if r["route_id"] not in affected]
    ids = {r["trip_id"] for r in kept_trips}
    call_fields, calls = rows(tables, "stop_times.txt")
    result = dict(tables)
    result["routes.txt"] = encode(route_fields, [r for r in routes if r["route_id"] not in affected])
    result["trips.txt"] = encode(trip_fields, kept_trips)
    result["stop_times.txt"] = encode(call_fields, [r for r in calls if r["trip_id"] in ids])
    return result


def namespace_shuttle(tables: dict[str, bytes]) -> tuple[dict[str, bytes], list[str]]:
    validate(tables)
    supported = set(ID_FIELDS) | {"feed_info.txt"}
    extra = set(tables) - supported
    if extra:
        raise FeedError(f"지원하지 않는 셔틀 GTFS 확장 파일: {sorted(extra)}")
    result = dict(tables)
    for name, mapping in ID_FIELDS.items():
        if name not in result:
            continue
        fields, values = rows(result, name)
        for row in values:
            for field, id_type in mapping.items():
                if field in row and row[field]:
                    row[field] = PREFIX[id_type] + row[field]
        result[name] = encode(fields, values)
    _, routes = rows(result, "routes.txt")
    validate(result)
    return result, [r["route_id"] for r in routes]


def coverage_dates(*feeds: dict[str, bytes] | None) -> tuple[str, str]:
    dates = []
    for feed in feeds:
        if not feed:
            continue
        if "calendar_dates.txt" in feed:
            dates.extend(r["date"] for r in rows(feed, "calendar_dates.txt")[1])
        if "calendar.txt" in feed:
            for row in rows(feed, "calendar.txt")[1]:
                dates.extend((row["start_date"], row["end_date"]))
    if not dates:
        raise FeedError("운행 날짜가 없습니다.")
    from datetime import datetime
    return (datetime.strptime(min(dates), "%Y%m%d").date().isoformat(),
            datetime.strptime(max(dates), "%Y%m%d").date().isoformat())


def service_bounds(*feeds: dict[str, bytes] | None) -> tuple[str, str]:
    from datetime import date, timedelta
    first, last = coverage_dates(*feeds)
    return ((date.fromisoformat(first) - timedelta(days=1)).isoformat(),
            (date.fromisoformat(last) + timedelta(days=2)).isoformat())


def write_feed(tables: dict[str, bytes], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as out:
        for name in sorted(tables):
            out.writestr(name, tables[name])


def prepare(existing: Path, shuttle: Path | None, strike_json: Path | None,
            osm: Path, output: Path) -> dict:
    if not osm.is_file() or osm.suffix.lower() != ".pbf":
        raise FeedError("OSM .pbf 파일 경로가 필요합니다.")
    source = read_feed(existing)
    source_stats = validate(source)
    affected: set[str] = set()
    if strike_json:
        payload = json.loads(strike_json.read_text(encoding="utf-8-sig"))
        ids = payload.get("affected_route_ids")
        if not isinstance(ids, list) or not all(isinstance(x, str) and x for x in ids):
            raise FeedError("파업 시나리오에 affected_route_ids 문자열 배열이 필요합니다.")
        affected = set(ids)
    filtered = remove_struck_routes(source, affected)
    existing_stats = validate(filtered)
    shuttle_routes: list[str] = []
    shuttle_stats = None
    shuttle_tables = None
    if shuttle:
        shuttle_tables, shuttle_routes = namespace_shuttle(read_feed(shuttle))
        shuttle_stats = validate(shuttle_tables)
    output.mkdir(parents=True, exist_ok=True)
    write_feed(filtered, output / "existing.gtfs.zip")
    if shuttle_tables:
        write_feed(shuttle_tables, output / "shuttle.gtfs.zip")
    elif (output / "shuttle.gtfs.zip").exists():
        (output / "shuttle.gtfs.zip").unlink()
    shutil.copyfile(osm, output / "streets.osm.pbf")
    config = {"transitServiceStart": service_bounds(filtered, shuttle_tables)[0],
              "transitServiceEnd": service_bounds(filtered, shuttle_tables)[1],
              "transitFeeds": [{"type": "gtfs", "feedId": "A",
                                "source": "existing.gtfs.zip"}]}
    if shuttle_tables:
        config["transitFeeds"].append({"type": "gtfs", "feedId": "SHUTTLE",
                                       "source": "shuttle.gtfs.zip"})
    (output / "build-config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    digest = hashlib.sha256()
    digest.update(existing.read_bytes() if existing.is_file() else b"".join(source.values()))
    digest.update(shuttle.read_bytes() if shuttle and shuttle.is_file() else
                  b"".join(shuttle_tables.values()) if shuttle_tables else b"")
    digest.update(json.dumps(sorted(affected)).encode())
    with osm.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    version = digest.hexdigest()[:16]
    metadata = {"version": version, "service_dates": coverage_dates(filtered, shuttle_tables), "source_stats": source_stats,
                "existing_stats": existing_stats, "shuttle_stats": shuttle_stats,
                "shuttle_route_ids": shuttle_routes, "excluded_route_ids": sorted(affected),
                "source_kind": "provided_unverified" if shuttle else "none"}
    (output / "manifest.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata
