"""Export an M05 timetable as citizen shuttle GTFS with checked stop coordinates."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from busstrike.sim import io as sim_io  # noqa: E402
from busstrike.sim.gtfs import export_gtfs, read_gtfs  # noqa: E402

from .gtfs_adapter import FeedError, read_feed, validate  # noqa: E402


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stop_data(path: Path) -> tuple[dict[str, tuple[float, float]], dict[str, str]]:
    coords, names = {}, {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            stop_id = row.get("stop_id")
            if not stop_id:
                continue
            try:
                lat, lon = float(row["stop_lat"]), float(row["stop_lon"])
            except (KeyError, TypeError, ValueError) as error:
                raise FeedError(f"정류장 {stop_id} 좌표가 없습니다.") from error
            if not 37 <= lat <= 38 or not 126 <= lon <= 128:
                raise FeedError(f"정류장 {stop_id} 좌표가 서울 권역 밖입니다.")
            coords[stop_id] = (lat, lon)
            names[stop_id] = row.get("stop_name") or stop_id
    return coords, names


def export_checked(scenario_path: Path, plan_path: Path, timetable_path: Path,
                   report_path: Path, stops_path: Path, output: Path,
                   demo: bool = False, service_date: str | None = None,
                   confirm: dict | None = None) -> dict:
    """confirm이 있으면 운영자 확정 묶음으로 보고 draft 운행안도 받는다(--demo 아님).

    service_date를 주면 GTFS 운행일을 그 날짜로 쓰고 원래 시뮬레이션 날짜는 메타에 남긴다.
    """
    scenario_raw = json.loads(scenario_path.read_text(encoding="utf-8-sig"))
    plan_raw = json.loads(plan_path.read_text(encoding="utf-8-sig"))
    timetable = sim_io.timetable_from_dict(json.loads(timetable_path.read_text(encoding="utf-8-sig")))
    scenario = sim_io.scenario_from_dict(scenario_raw)
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    if not report.get("pass") or report.get("failed_checks"):
        raise FeedError("M07 pass=true, 실패 검사 0건인 보고서가 필요합니다.")
    if plan_raw.get("plan_id") != timetable.plan_id or plan_raw.get("revision") != timetable.plan_revision:
        raise FeedError("C 운행안과 M05 시간표의 plan_id/revision이 다릅니다.")
    if plan_raw.get("scenario_id") != scenario.scenario_id:
        raise FeedError("C 운행안과 M05 시나리오 ID가 다릅니다.")
    if scenario_raw.get("network_snapshot_id") != plan_raw.get("network_snapshot_id", scenario_raw.get("network_snapshot_id")):
        raise FeedError("운행안과 시나리오의 network_snapshot_id가 다릅니다.")
    if plan_raw.get("status") == "draft" and not demo and not confirm:
        raise FeedError("draft 운행안은 --demo를 명시해야 합니다.")
    if demo and confirm:
        raise FeedError("운영자 확정 묶음은 --demo로 내보내지 않습니다.")
    coords, names = stop_data(stops_path)
    used = {stop.stop_id for trip in timetable.trips if trip.in_service for stop in trip.stop_times}
    missing = used - coords.keys()
    if missing:
        raise FeedError(f"M05 정차점 좌표가 없습니다: {sorted(missing)[:10]}")
    output.mkdir(parents=True, exist_ok=True)
    gtfs_date = service_date or scenario.service_date
    export_gtfs(timetable, gtfs_date, output, coords)
    stops_file = output / "stops.txt"
    with stops_file.open(encoding="utf-8", newline="") as stream:
        stop_rows = list(csv.DictReader(stream))
    with stops_file.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("stop_id", "stop_name", "stop_lat", "stop_lon"),
                                lineterminator="\n")
        writer.writeheader()
        for row in stop_rows:
            row["stop_name"] = names[row["stop_id"]]
            writer.writerow(row)
    stats = validate(read_feed(output))
    roundtrip = read_gtfs(output)
    if roundtrip["feed_version"] != f"{timetable.plan_id}@{timetable.plan_revision}":
        raise FeedError("GTFS 왕복 확인에서 운행안 버전이 달라졌습니다.")
    meta = {
        "plan_id": timetable.plan_id, "revision": timetable.plan_revision,
        "network_snapshot_id": scenario_raw["network_snapshot_id"],
        "scenario_id": scenario.scenario_id, "service_date": gtfs_date,
        "simulated_service_date": scenario.service_date,
        "source_kind": "SYNTHETIC_DEMO" if demo else "C_TEAM_CANDIDATE",
        "status": ("draft" if demo else "operator_confirmed_not_approved" if confirm
                   else "candidate_not_approved"),
        "m07": {"pass": report["pass"], "fully_verified": bool(report.get("fully_verified")),
                "not_run_checks": report.get("not_run_checks"),
                "not_verified_checks": report.get("not_verified_checks")},
        "stats": stats,
        "input_sha256": {name: sha(path) for name, path in (
            ("scenario", scenario_path), ("plan", plan_path),
            ("timetable", timetable_path), ("m07_report", report_path), ("stops", stops_path))},
    }
    if confirm:
        meta["confirm"] = {key: confirm.get(key) for key in (
            "confirm_id", "confirmed_at", "gu", "plan_sha256", "algo_name", "run_id")}
    (output / "shuttle-meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("scenario", "plan", "timetable", "m07-report", "stops", "out"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    meta = export_checked(args.scenario, args.plan, args.timetable, args.m07_report,
                          args.stops, args.out, args.demo)
    print(json.dumps({"plan_id": meta["plan_id"], "revision": meta["revision"],
                      "source_kind": meta["source_kind"], "stats": meta["stats"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()