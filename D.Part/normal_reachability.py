"""A와 검토할 정상 교통 도달 가능 판정의 합성 프로토타입.

GTFS 정류장 간 조회만 수행한다. 응답 형식은 공용 DTO/M08 API가 아니다.
"""

import argparse
import json
from datetime import date
from pathlib import Path

from gtfs_preview import EXAMPLE, Preview, PreviewError, _load_feed, search


def load_normal_preview(folder: Path = EXAMPLE) -> tuple[Preview, str]:
    """셔틀 feed를 읽지 않고 정상 GTFS와 버전 표기를 로드한다."""
    try:
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        service_date = date.fromisoformat(manifest["service_date"])
        version = manifest["normal_timetable_version"]
        if (
            manifest["timezone"] != "Asia/Seoul"
            or manifest["normal_feed"] != "normal"
            or not isinstance(version, str)
            or not version
            or not manifest["network_snapshot_id"]
        ):
            raise ValueError("invalid normal timetable manifest")
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        raise PreviewError(
            "DATA_INCOMPLETE", "정상 시간표 설정이 불완전합니다."
        ) from error
    stops, trips = _load_feed(folder / "normal", "normal", service_date)
    preview = Preview(
        plan_id="normal-preview-internal",
        revision=1,
        service_date=service_date,
        network_snapshot_id=manifest["network_snapshot_id"],
        stops=frozenset(stops),
        trips=tuple(trips),
        excluded_routes=frozenset(),
        source="D-authored synthetic normal GTFS",
        verification="synthetic_only",
    )
    return preview, version


def assess_normal_reachability(
    query_id: str,
    origin_stop_id: str,
    destination_stop_id: str,
    departure_at: str,
    folder: Path = EXAMPLE,
) -> dict[str, object]:
    """정상 버스만으로 GTFS 정류장 간 도달 가능 여부를 요약한다."""
    result: dict[str, object] = {
        "query_id": query_id,
        "origin_stop_id": origin_stop_id,
        "destination_stop_id": destination_stop_id,
        "departure_at": departure_at,
        "status": "indeterminate",
        "reachable": None,
        "arrival_at": None,
        "walking_time_s": None,
        "transfer_count": None,
        "normal_timetable_version": None,
        "network_snapshot_id": None,
        "service_date": None,
        "scope": "synthetic_normal_bus_stop_to_stop",
        "data_kind": "synthetic",
        "failure_code": None,
        "legs": [],
        "warnings": [
            "합성 정상 버스 시간표입니다.",
            "보행·지하철·좌석 가능 여부는 판정하지 않았습니다.",
        ],
    }
    if not isinstance(query_id, str) or not query_id.strip():
        result["failure_code"] = "INPUT_INVALID"
        return result
    try:
        preview, version = load_normal_preview(folder)
        result["normal_timetable_version"] = version
        result["network_snapshot_id"] = preview.network_snapshot_id
        result["service_date"] = preview.service_date.isoformat()
        journey = search(
            preview,
            origin_stop_id,
            destination_stop_id,
            departure_at,
            preview.plan_id,
            preview.revision,
            exclude_demo_routes=False,
        )
    except PreviewError as error:
        result["failure_code"] = error.code
        if error.code == "NO_PATH":
            result["status"] = "unreachable"
            result["reachable"] = False
        return result
    result.update(
        status="reachable",
        reachable=True,
        arrival_at=journey["arrival_at"],
        transfer_count=journey["transfers"],
        legs=journey["legs"],
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="A 전달용 합성 정상 교통 도달 가능 조회"
    )
    parser.add_argument("query_id")
    parser.add_argument("origin_stop_id")
    parser.add_argument("destination_stop_id")
    parser.add_argument("departure_at", help="UTC offset 포함 ISO 8601 시각")
    args = parser.parse_args()
    result = assess_normal_reachability(
        args.query_id, args.origin_stop_id, args.destination_stop_id, args.departure_at
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["status"] == "indeterminate" else 0


if __name__ == "__main__":
    raise SystemExit(main())
