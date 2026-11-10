"""합성 GTFS의 직행·동일 정류장 1회 환승을 검색하는 개발 미리보기.

M08 공용 API나 실제 A/B 시간표 소비 계약이 아니다.
"""

import argparse
import csv
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from itertools import pairwise
from pathlib import Path

from gtfs_header_check import check_gtfs

EXAMPLE = Path(__file__).parent / "examples" / "gtfs_preview"
SEOUL = timezone(timedelta(hours=9), name="Asia/Seoul")


class PreviewError(Exception):
    """개발 미리보기 입력·검색 오류."""

    def __init__(self, code: str, message: str, **details: object) -> None:
        super().__init__(message)
        self.code = code
        self.details = details

    def as_dict(self) -> dict[str, object]:
        return {"code": self.code, "message": str(self), "details": self.details}


@dataclass(frozen=True)
class Visit:
    stop_id: str
    arrival_s: int
    departure_s: int
    sequence: int


@dataclass(frozen=True)
class Trip:
    trip_id: str
    route_id: str
    kind: str
    visits: tuple[Visit, ...]


@dataclass(frozen=True)
class Preview:
    plan_id: str
    revision: int
    service_date: date
    network_snapshot_id: str
    stops: frozenset[str]
    trips: tuple[Trip, ...]
    excluded_routes: frozenset[str]
    source: str
    verification: str


def _rows(folder: Path, filename: str, required: set[str]) -> list[dict[str, str]]:
    try:
        with (folder / filename).open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None or not required <= set(reader.fieldnames):
                raise PreviewError(
                    "DATA_INCOMPLETE", "GTFS 필수 열이 부족합니다.", file=filename
                )
            rows = list(reader)
    except (OSError, UnicodeError, csv.Error) as error:
        raise PreviewError(
            "DATA_INCOMPLETE", "GTFS 파일을 읽을 수 없습니다.", file=filename
        ) from error
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise PreviewError(
            "DATA_INCOMPLETE", "GTFS 행의 열 수가 맞지 않습니다.", file=filename
        )
    return rows


def _time_s(value: str) -> int:
    parts = value.split(":")
    if len(parts) != 3 or any(
        not part.isascii() or not part.isdecimal() for part in parts
    ):
        raise PreviewError(
            "DATA_INCOMPLETE", "GTFS 시각 형식이 잘못되었습니다.", time=value
        )
    hours, minutes, seconds = map(int, parts)
    if minutes > 59 or seconds > 59:
        raise PreviewError(
            "DATA_INCOMPLETE", "GTFS 시각 범위가 잘못되었습니다.", time=value
        )
    return hours * 3600 + minutes * 60 + seconds


def _load_feed(
    folder: Path, kind: str, service_date: date
) -> tuple[dict[str, tuple[str, str, str]], list[Trip]]:
    errors = check_gtfs(folder)
    if errors:
        raise PreviewError(
            "DATA_INCOMPLETE", "GTFS 기본 구조가 부족합니다.", errors=errors
        )
    for unsupported in (
        "calendar.txt",
        "frequencies.txt",
        "transfers.txt",
        "pathways.txt",
    ):
        if (folder / unsupported).exists():
            raise PreviewError(
                "OUT_OF_SCOPE",
                "이 미리보기에서 해석하지 않는 GTFS 파일입니다.",
                file=unsupported,
            )

    agency = _rows(folder, "agency.txt", {"agency_timezone"})
    if len(agency) != 1 or agency[0]["agency_timezone"] != "Asia/Seoul":
        raise PreviewError(
            "OUT_OF_SCOPE", "합성 예제는 Asia/Seoul 기관 한 곳만 지원합니다."
        )
    stops = _rows(folder, "stops.txt", {"stop_id", "stop_name", "stop_lat", "stop_lon"})
    stop_ids = {row["stop_id"] for row in stops}
    if len(stop_ids) != len(stops) or not stop_ids or "" in stop_ids:
        raise PreviewError("DATA_INCOMPLETE", "정류장 ID가 비었거나 중복됐습니다.")
    for row in stops:
        try:
            lat, lon = float(row["stop_lat"]), float(row["stop_lon"])
        except ValueError as error:
            raise PreviewError(
                "DATA_INCOMPLETE", "정류장 좌표가 잘못되었습니다."
            ) from error
        if (
            not math.isfinite(lat)
            or not math.isfinite(lon)
            or abs(lat) > 90
            or abs(lon) > 180
        ):
            raise PreviewError("DATA_INCOMPLETE", "정류장 좌표가 잘못되었습니다.")
    stop_records = {
        row["stop_id"]: (row["stop_name"], row["stop_lat"], row["stop_lon"])
        for row in stops
    }
    routes = _rows(folder, "routes.txt", {"route_id", "route_type"})
    route_ids = {row["route_id"] for row in routes}
    if len(route_ids) != len(routes) or "" in route_ids:
        raise PreviewError("DATA_INCOMPLETE", "노선 ID가 비었거나 중복됐습니다.")
    if any(row["route_type"] != "3" for row in routes):
        raise PreviewError("OUT_OF_SCOPE", "합성 예제는 버스 노선만 지원합니다.")
    dates = _rows(
        folder, "calendar_dates.txt", {"service_id", "date", "exception_type"}
    )
    date_keys = [(row["service_id"], row["date"]) for row in dates]
    if len(date_keys) != len(set(date_keys)) or not dates:
        raise PreviewError("DATA_INCOMPLETE", "운행일 ID가 없거나 중복됐습니다.")
    if any(row["exception_type"] not in {"1", "2"} for row in dates):
        raise PreviewError("DATA_INCOMPLETE", "운행일 예외 코드가 잘못되었습니다.")
    try:
        for row in dates:
            datetime.strptime(row["date"], "%Y%m%d")
    except ValueError as error:
        raise PreviewError(
            "DATA_INCOMPLETE", "운행일 날짜가 잘못되었습니다."
        ) from error
    active = {
        row["service_id"]
        for row in dates
        if row["date"] == service_date.strftime("%Y%m%d")
        and row["exception_type"] == "1"
    }
    services = {row["service_id"] for row in dates}

    trip_rows = _rows(folder, "trips.txt", {"route_id", "service_id", "trip_id"})
    trip_ids = {row["trip_id"] for row in trip_rows}
    if len(trip_ids) != len(trip_rows) or "" in trip_ids:
        raise PreviewError("DATA_INCOMPLETE", "운행편 ID가 비었거나 중복됐습니다.")
    if any(
        row["route_id"] not in route_ids or row["service_id"] not in services
        for row in trip_rows
    ):
        raise PreviewError("DATA_INCOMPLETE", "운행편의 노선·운행일 참조가 없습니다.")
    visits: dict[str, list[Visit]] = {trip_id: [] for trip_id in trip_ids}
    for row in _rows(
        folder,
        "stop_times.txt",
        {"trip_id", "stop_id", "stop_sequence", "arrival_time", "departure_time"},
    ):
        if row["trip_id"] not in trip_ids or row["stop_id"] not in stop_ids:
            raise PreviewError(
                "DATA_INCOMPLETE", "정차시각의 운행편·정류장 참조가 없습니다."
            )
        if row.get("pickup_type", "0") not in {"", "0"} or row.get(
            "drop_off_type", "0"
        ) not in {"", "0"}:
            raise PreviewError(
                "OUT_OF_SCOPE", "승하차 제한이 있는 GTFS 운행편은 미지원입니다."
            )
        try:
            sequence = int(row["stop_sequence"])
        except ValueError as error:
            raise PreviewError(
                "DATA_INCOMPLETE", "정차 순번이 잘못되었습니다."
            ) from error
        if sequence < 1:
            raise PreviewError("DATA_INCOMPLETE", "정차 순번이 잘못되었습니다.")
        visits[row["trip_id"]].append(
            Visit(
                row["stop_id"],
                _time_s(row["arrival_time"]),
                _time_s(row["departure_time"]),
                sequence,
            )
        )

    result = []
    for row in trip_rows:
        ordered = sorted(visits[row["trip_id"]], key=lambda visit: visit.sequence)
        if (
            len(ordered) < 2
            or len({visit.sequence for visit in ordered}) != len(ordered)
            or any(visit.departure_s < visit.arrival_s for visit in ordered)
            or any(b.arrival_s < a.departure_s for a, b in pairwise(ordered))
        ):
            raise PreviewError(
                "DATA_INCOMPLETE",
                "운행편의 정차 순서·시간이 불완전합니다.",
                trip_id=row["trip_id"],
            )
        if row["service_id"] in active:
            result.append(
                Trip(
                    f"{kind}:{row['trip_id']}",
                    f"{kind}:{row['route_id']}",
                    kind,
                    tuple(ordered),
                )
            )
    return stop_records, result


def load_preview(folder: Path = EXAMPLE) -> Preview:
    """D 소유 합성 manifest와 두 GTFS feed를 로드한다."""
    try:
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        service_date = date.fromisoformat(manifest["service_date"])
        revision = manifest["revision"]
        if (
            manifest["timezone"] != "Asia/Seoul"
            or type(revision) is not int
            or revision < 1
        ):
            raise ValueError("invalid preview manifest")
        feeds = (manifest["normal_feed"], manifest["shuttle_feed"])
        if (
            any(name not in {"normal", "shuttle"} for name in feeds)
            or feeds[0] == feeds[1]
        ):
            raise ValueError("invalid feed path")
        if not all(
            isinstance(manifest[name], str) and manifest[name]
            for name in ("plan_id", "network_snapshot_id", "source", "verification")
        ):
            raise ValueError("missing plan id")
        if not isinstance(manifest["demo_excluded_route_ids"], list):
            raise TypeError("invalid excluded routes")
        excluded = frozenset(manifest["demo_excluded_route_ids"])
        if not all(isinstance(route_id, str) and route_id for route_id in excluded):
            raise ValueError("invalid excluded routes")
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        raise PreviewError(
            "DATA_INCOMPLETE", "합성 예제 manifest가 불완전합니다."
        ) from error

    normal_stops, normal_trips = _load_feed(folder / feeds[0], "normal", service_date)
    shuttle_stops, shuttle_trips = _load_feed(
        folder / feeds[1], "shuttle", service_date
    )
    if normal_stops != shuttle_stops:
        raise PreviewError("DATA_VERSION_MISMATCH", "정상·셔틀 정류장 대응이 다릅니다.")
    if not excluded <= {trip.route_id for trip in normal_trips}:
        raise PreviewError(
            "DATA_VERSION_MISMATCH", "예제 제외 노선이 정상 GTFS에 없습니다."
        )
    return Preview(
        manifest["plan_id"],
        revision,
        service_date,
        manifest["network_snapshot_id"],
        frozenset(normal_stops),
        tuple(normal_trips + shuttle_trips),
        excluded,
        manifest["source"],
        manifest["verification"],
    )


def _iso(service_date: date, seconds: int) -> str:
    return (
        datetime.combine(service_date, datetime.min.time(), SEOUL)
        + timedelta(seconds=seconds)
    ).isoformat()


def search(
    preview: Preview,
    origin: str,
    destination: str,
    departure_at: str,
    plan_id: str,
    revision: int,
    *,
    exclude_demo_routes: bool = True,
) -> dict[str, object]:
    """가장 이른 도착의 직행 또는 같은 정류장 1회 환승을 찾는다."""
    if (
        plan_id != preview.plan_id
        or type(revision) is not int
        or revision != preview.revision
    ):
        raise PreviewError(
            "DATA_VERSION_MISMATCH", "운행안 ID 또는 revision이 다릅니다."
        )
    if not origin or not destination or origin == destination:
        raise PreviewError(
            "INPUT_INVALID", "출발지와 목적지를 서로 다른 정류장으로 입력하세요."
        )
    if origin not in preview.stops or destination not in preview.stops:
        raise PreviewError("OUT_OF_SCOPE", "합성 예제에 없는 정류장입니다.")
    try:
        requested = datetime.fromisoformat(departure_at)
        if requested.utcoffset() is None or requested.microsecond:
            raise ValueError("UTC offset required")
    except (TypeError, ValueError) as error:
        raise PreviewError(
            "INPUT_INVALID", "출발 시각에는 UTC offset이 필요합니다."
        ) from error
    midnight = datetime.combine(preview.service_date, datetime.min.time(), SEOUL)
    requested_s = int((requested.astimezone(SEOUL) - midnight).total_seconds())
    max_s = max(
        (visit.arrival_s for trip in preview.trips for visit in trip.visits), default=0
    )
    if requested_s < 0 or requested_s > max(86400, max_s):
        raise PreviewError("OUT_OF_SCOPE", "합성 예제의 서비스 날짜·시간 밖입니다.")

    trips = [
        trip
        for trip in preview.trips
        if not (exclude_demo_routes and trip.route_id in preview.excluded_routes)
    ]
    rides = [
        (trip, start, end)
        for trip in trips
        for i, start in enumerate(trip.visits)
        for end in trip.visits[i + 1 :]
        if start.departure_s >= requested_s
    ]
    candidates: list[
        tuple[tuple[int, int, int], tuple[tuple[Trip, Visit, Visit], ...]]
    ] = []
    for first in rides:
        trip, start, end = first
        if start.stop_id != origin:
            continue
        if end.stop_id == destination:
            candidates.append(((end.arrival_s, 0, start.departure_s), (first,)))
        for second in rides:
            next_trip, transfer, finish = second
            if (
                next_trip.trip_id != trip.trip_id
                and end.stop_id == transfer.stop_id
                and transfer.departure_s >= end.arrival_s
                and finish.stop_id == destination
            ):
                candidates.append(
                    ((finish.arrival_s, 1, start.departure_s), (first, second))
                )
    if not candidates:
        raise PreviewError("NO_PATH", "지원하는 합성 시간표 안에 경로가 없습니다.")

    _, chosen = min(candidates, key=lambda candidate: candidate[0])
    legs = [
        {
            "mode": "shuttle" if trip.kind == "shuttle" else "bus",
            "route_id": trip.route_id,
            "trip_id": trip.trip_id,
            "from_stop_id": start.stop_id,
            "to_stop_id": end.stop_id,
            "departure_at": _iso(preview.service_date, start.departure_s),
            "arrival_at": _iso(preview.service_date, end.arrival_s),
        }
        for trip, start, end in chosen
    ]
    return {
        "plan_id": preview.plan_id,
        "plan_revision": preview.revision,
        "plan_mode": "simulation",
        "verification": preview.verification,
        "source": preview.source,
        "service_date": preview.service_date.isoformat(),
        "network_snapshot_id": preview.network_snapshot_id,
        "legs": legs,
        "arrival_at": legs[-1]["arrival_at"],
        "transfers": len(legs) - 1,
        "warnings": [
            "합성 시간표를 사용한 개발 미리보기입니다.",
            "독립 검증 전이며 실제 운행·좌석을 보장하지 않습니다.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="D 합성 GTFS 경로 개발 미리보기")
    parser.add_argument("origin")
    parser.add_argument("destination")
    parser.add_argument("departure_at", help="UTC offset을 포함한 ISO 8601 시각")
    args = parser.parse_args()
    try:
        preview = load_preview()
        result = search(
            preview,
            args.origin,
            args.destination,
            args.departure_at,
            preview.plan_id,
            preview.revision,
        )
    except PreviewError as error:
        print(json.dumps(error.as_dict(), ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
