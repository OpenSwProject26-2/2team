"""GTFS Schedule 입력의 파일 존재 여부와 CSV 헤더를 가볍게 검사한다.

원시 데이터 행을 기록하지 않으며, GTFS 표준 전체를 검증하지는 않는다.
"""

import argparse
import csv
from collections.abc import Callable
from io import TextIOWrapper
from pathlib import Path, PurePosixPath
from typing import TextIO
from zipfile import BadZipFile, ZipFile

REQUIRED_COLUMNS: dict[str, set[str]] = {
    "agency.txt": {"agency_name", "agency_url", "agency_timezone"},
    "stops.txt": {"stop_id", "stop_name", "stop_lat", "stop_lon"},
    "routes.txt": {"route_id", "route_type"},
    "trips.txt": {"route_id", "service_id", "trip_id"},
    "stop_times.txt": {
        "trip_id",
        "stop_id",
        "stop_sequence",
        "arrival_time",
        "departure_time",
    },
}
CALENDAR_COLUMNS: dict[str, set[str]] = {
    "calendar.txt": {
        "service_id",
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
        "start_date",
        "end_date",
    },
    "calendar_dates.txt": {"service_id", "date", "exception_type"},
}


def _get_header(handle: TextIO) -> list[str]:
    return next(csv.reader(handle), [])


def _check_headers(
    filenames: set[str], reader: Callable[[str], list[str]]
) -> list[str]:
    errors: list[str] = []
    for filename, columns in REQUIRED_COLUMNS.items():
        if filename not in filenames:
            errors.append(f"필수 파일 없음: {filename}")
            continue
        errors.extend(_check_one(filename, columns, reader))

    calendar_files = sorted(filenames & CALENDAR_COLUMNS.keys())
    if not calendar_files:
        errors.append("운행일 파일 없음: calendar.txt 또는 calendar_dates.txt 필요")
    for filename in calendar_files:
        errors.extend(_check_one(filename, CALENDAR_COLUMNS[filename], reader))
    return errors


def _check_one(
    filename: str, required: set[str], reader: Callable[[str], list[str]]
) -> list[str]:
    headers = reader(filename)
    if not headers:
        return [f"CSV 헤더 없음: {filename}"]
    errors: list[str] = []
    if len(headers) != len(set(headers)):
        errors.append(f"중복 헤더 있음: {filename}")
    missing = sorted(required - set(headers))
    if missing:
        errors.append(f"필수 열 없음: {filename} ({', '.join(missing)})")
    if filename == "routes.txt" and not (
        {"route_short_name", "route_long_name"} & set(headers)
    ):
        errors.append("노선명 열 없음: routes.txt (short 또는 long 필요)")
    return errors


def check_gtfs(source: str | Path) -> list[str]:
    """GTFS 폴더/ZIP의 파일·헤더 문제 목록을 반환한다 (정상이면 [])."""
    path = Path(source)
    try:
        if path.is_dir():
            filenames = {p.name for p in path.iterdir() if p.is_file()}

            def read_local(filename: str) -> list[str]:
                with (path / filename).open(encoding="utf-8-sig", newline="") as file:
                    return _get_header(file)

            return _check_headers(filenames, read_local)

        if path.is_file() and path.suffix.lower() == ".zip":
            with ZipFile(path) as archive:
                members: dict[str, str] = {}
                for name in archive.namelist():
                    if name.endswith("/"):
                        continue
                    filename = PurePosixPath(name).name
                    if filename in members:
                        return [f"ZIP 내부 파일명 중복: {filename}"]
                    members[filename] = name

                def read_zipped(filename: str) -> list[str]:
                    with (
                        archive.open(members[filename]) as raw,
                        TextIOWrapper(raw, encoding="utf-8-sig", newline="") as file,
                    ):
                        return _get_header(file)

                return _check_headers(set(members), read_zipped)
    except (OSError, UnicodeError, BadZipFile, csv.Error):
        return ["파일 또는 CSV 헤더를 읽을 수 없음 (파일 형식·인코딩 확인)"]

    return ["입력 경로는 GTFS 폴더 또는 .zip 파일이어야 함"]


def main() -> int:
    parser = argparse.ArgumentParser(description="GTFS 기본 파일·헤더 검사")
    parser.add_argument("source", help="GTFS 폴더 또는 ZIP 파일 경로")
    args = parser.parse_args()
    errors = check_gtfs(args.source)
    if errors:
        for error in errors:
            print(f"[오류] {error}")
        return 1
    print("[통과] GTFS 필수 파일·헤더 기본 검사 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
