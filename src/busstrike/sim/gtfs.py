"""셔틀 GTFS 내보내기·읽기: simulator.md §6.4.

`timetable.json`의 예정 시각을 agency·routes·trips·stop_times·calendar·feed_info(.txt)로 쓴다.
영업 trip만 내보내고 공차는 `block_id`(= vehicle_id)로만 이어진다. 표준 라이브러리만 쓴다.
"""

from __future__ import annotations

import csv
import io as _io
import calendar
import re
from pathlib import Path
from typing import Any, Mapping

from . import io
from .models import SimError, Timetable

AGENCY_ID = "busstrike-sim"
AGENCY_NAME = "파업 대체 셔틀(시뮬레이션)"
AGENCY_URL = "https://example.invalid/busstrike-sim"
AGENCY_TIMEZONE = "Asia/Seoul"
ROUTE_TYPE_BUS = 3

AGENCY_COLUMNS = ("agency_id", "agency_name", "agency_url", "agency_timezone")
ROUTES_COLUMNS = ("route_id", "agency_id", "route_short_name", "route_long_name", "route_type")
TRIPS_COLUMNS = ("route_id", "service_id", "trip_id", "direction_id", "block_id")
STOP_TIMES_COLUMNS = ("trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence",
                      "pickup_type", "drop_off_type", "timepoint")
CALENDAR_COLUMNS = ("service_id", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
                    "sunday", "start_date", "end_date")
FEED_INFO_COLUMNS = ("feed_publisher_name", "feed_publisher_url", "feed_lang", "feed_version")
STOPS_COLUMNS = ("stop_id", "stop_name", "stop_lat", "stop_lon")

FILES = ("agency.txt", "routes.txt", "trips.txt", "stop_times.txt", "calendar.txt", "feed_info.txt")


def format_gtfs_time(seconds: int) -> str:
    """service_date 자정 기준 초 → `HH:MM:SS`(24시 이상 허용)."""
    if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds < 0:
        raise SimError(SimError.INPUT_INVALID, "gtfs time must be a non-negative int", {"seconds": seconds})
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def parse_gtfs_time(text: str) -> int:
    parts = text.split(":")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise SimError(SimError.INPUT_INVALID, "bad GTFS time", {"value": text})
    h, m, s = (int(p) for p in parts)
    return h * 3600 + m * 60 + s


def service_id_for(service_date: str) -> str:
    y, m, d = _parse_date(service_date)
    return f"STRIKE_{y:04d}{m:02d}{d:02d}"


def _parse_date(service_date: str) -> tuple[int, int, int]:
    """`YYYY-MM-DD` → (년, 월, 일). 시계·`datetime`을 쓰지 않는다(결정론 검사)."""
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", service_date) if isinstance(service_date, str) else None
    if match:
        y, m, d = (int(g) for g in match.groups())
        if 1 <= m <= 12 and 1 <= d <= calendar.monthrange(y, m)[1]:
            return y, m, d
    raise SimError(SimError.INPUT_INVALID, "service_date must be YYYY-MM-DD", {"service_date": service_date})


def _csv_bytes(columns: tuple[str, ...], rows: list[tuple]) -> bytes:
    buf = _io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(columns)
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


def build_gtfs_files(timetable: Timetable, service_date: str,
                     stop_coords: Mapping[str, tuple[float, float]] | None = None) -> dict[str, bytes]:
    """파일명 → 바이트. 같은 입력이면 항상 같은 바이트."""
    y, m, d = _parse_date(service_date)
    service_id = service_id_for(service_date)
    trips = sorted((t for t in timetable.trips if t.in_service), key=lambda t: t.trip_id)
    if len({t.trip_id for t in trips}) != len(trips):
        raise SimError(SimError.INPUT_INVALID, "duplicate trip_id in timetable")

    # routes: 영업 trip이 있는 노선만. 이름은 leg_index가 가장 작은 영업 trip의 첫·끝 정류장.
    first_trip: dict[str, Any] = {}
    for t in trips:
        cur = first_trip.get(t.route_id)
        if cur is None or (t.leg_index, t.trip_id) < (cur.leg_index, cur.trip_id):
            first_trip[t.route_id] = t
    route_rows = []
    for rid in sorted(first_trip):
        stops = first_trip[rid].stop_ids
        route_rows.append((rid, AGENCY_ID, rid, f"{stops[0]}–{stops[-1]}", ROUTE_TYPE_BUS))

    trip_rows = [(t.route_id, service_id, t.trip_id, t.leg_index % 2, t.vehicle_id) for t in trips]
    st_rows = []
    for t in trips:
        sts = sorted(t.stop_times, key=lambda s: s.stop_seq)
        last = len(sts) - 1
        for i, st in enumerate(sts):
            hms = format_gtfs_time(st.sched_s)
            st_rows.append((t.trip_id, hms, hms, st.stop_id, st.stop_seq,
                            1 if i == last else 0, 1 if i == 0 else 0, 1 if i == 0 else 0))

    weekday = [0] * 7
    weekday[calendar.weekday(y, m, d)] = 1
    ymd = f"{y:04d}{m:02d}{d:02d}"
    files = {
        "agency.txt": _csv_bytes(AGENCY_COLUMNS, [(AGENCY_ID, AGENCY_NAME, AGENCY_URL, AGENCY_TIMEZONE)]),
        "routes.txt": _csv_bytes(ROUTES_COLUMNS, route_rows),
        "trips.txt": _csv_bytes(TRIPS_COLUMNS, trip_rows),
        "stop_times.txt": _csv_bytes(STOP_TIMES_COLUMNS, st_rows),
        "calendar.txt": _csv_bytes(CALENDAR_COLUMNS, [(service_id, *weekday, ymd, ymd)]),
        "feed_info.txt": _csv_bytes(FEED_INFO_COLUMNS, [(
            AGENCY_NAME, AGENCY_URL, "ko", f"{timetable.plan_id}@{timetable.plan_revision}")]),
    }
    if stop_coords:
        used = sorted({s.stop_id for t in trips for s in t.stop_times} & set(stop_coords))
        if used:
            files["stops.txt"] = _csv_bytes(STOPS_COLUMNS, [(s, s, *stop_coords[s]) for s in used])
    return files


def export_gtfs(timetable: Timetable, service_date: str, out_dir: Path | str,
                stop_coords: Mapping[str, tuple[float, float]] | None = None) -> dict[str, str]:
    """out_dir에 GTFS 파일을 쓰고 파일명 → SHA-256을 돌려준다."""
    out = Path(out_dir)
    return {name: io.write_bytes(out / name, data)
            for name, data in build_gtfs_files(timetable, service_date, stop_coords).items()}


def _read_csv(path: Path, columns: tuple[str, ...]) -> list[dict[str, str]]:
    if not path.is_file():
        raise SimError(SimError.INPUT_INVALID, "GTFS file missing", {"file": path.name})
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if tuple(reader.fieldnames or ()) != columns:
            raise SimError(SimError.INPUT_INVALID, "GTFS header mismatch", {"file": path.name})
        return list(reader)


def read_gtfs(gtfs_dir: Path | str) -> dict[str, Any]:
    """내보낸 GTFS를 다시 읽어 trip 단위 구조로 돌려준다(왕복 검증·D 연결 확인용).

    `{"service_id", "service_date", "feed_version", "routes": {route_id: {...}},
      "trips": [{trip_id, route_id, block_id, direction_id, stop_ids, times_s, stop_sequences}]}`
    trips는 첫 출발 시각, trip_id 순이다.
    """
    base = Path(gtfs_dir)
    routes = {r["route_id"]: r for r in _read_csv(base / "routes.txt", ROUTES_COLUMNS)}
    cal = _read_csv(base / "calendar.txt", CALENDAR_COLUMNS)
    feed = _read_csv(base / "feed_info.txt", FEED_INFO_COLUMNS)
    by_trip: dict[str, list[dict[str, str]]] = {}
    for row in _read_csv(base / "stop_times.txt", STOP_TIMES_COLUMNS):
        by_trip.setdefault(row["trip_id"], []).append(row)
    trips = []
    for row in _read_csv(base / "trips.txt", TRIPS_COLUMNS):
        sts = sorted(by_trip.get(row["trip_id"], []), key=lambda r: int(r["stop_sequence"]))
        trips.append({
            "trip_id": row["trip_id"], "route_id": row["route_id"], "block_id": row["block_id"],
            "direction_id": int(row["direction_id"]), "service_id": row["service_id"],
            "stop_ids": [r["stop_id"] for r in sts],
            "stop_sequences": [int(r["stop_sequence"]) for r in sts],
            "times_s": [parse_gtfs_time(r["departure_time"]) for r in sts],
        })
    trips.sort(key=lambda t: (t["times_s"][0] if t["times_s"] else 0, t["trip_id"]))
    return {
        "service_id": cal[0]["service_id"] if cal else None,
        "service_date": cal[0]["start_date"] if cal else None,
        "feed_version": feed[0]["feed_version"] if feed else None,
        "routes": routes,
        "trips": trips,
    }
