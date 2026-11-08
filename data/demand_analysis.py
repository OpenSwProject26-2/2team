"""Streaming descriptive analysis of shared TAIMS bus-leg OD tables."""

import argparse
from collections import Counter, defaultdict
from contextlib import closing
import csv
import json
import math
from pathlib import Path
import re
import shutil
import sqlite3
from tempfile import TemporaryDirectory
import time


OD_FIELDS = ("ROUTE_ID", "RIDE_BSST_ID", "ALGH_BSST_ID", "RIDE_DTM_H", "PSNG_NUM")
HOURS = tuple(f"{hour:02d}" for hour in range(24))
MORNING = {"07", "08"}


class AnalysisError(ValueError):
    """Only a filename, physical CSV line number, and field name are exposed."""


def error(path, line, field):
    raise AnalysisError(f"{Path(path).name}:{line}:{field}")


def csv_rows(path, required):
    """Yield rows without retaining a CSV or exposing row values on failure."""
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        try:
            header = reader.fieldnames or []
        except csv.Error:
            error(path, reader.reader.line_num, "CSV")
        for field in required:
            if field not in header or header.count(field) != 1:
                error(path, 1, field)
        while True:
            try:
                row = next(reader)
            except StopIteration:
                return
            except csv.Error:
                error(path, reader.reader.line_num, "CSV")
            for field in required:
                if row.get(field) is None:
                    error(path, reader.line_num, field)
            if None in row or any(value is None for value in row.values()):
                error(path, reader.line_num, "CSV")
            yield reader.line_num, row


def od_rows(path):
    for line, row in csv_rows(path, OD_FIELDS):
        for field in OD_FIELDS[:3]:
            if not row[field].strip():
                error(path, line, field)
        value = row["PSNG_NUM"]
        if not value or not value.isascii() or not value.isdecimal():
            error(path, line, "PSNG_NUM")
        try:
            count = int(value)
        except ValueError:
            error(path, line, "PSNG_NUM")
        if count > 2**63 - 1:
            error(path, line, "PSNG_NUM")
        if row["RIDE_DTM_H"] not in HOURS:
            error(path, line, "RIDE_DTM_H")
        yield line, tuple(row[field] for field in OD_FIELDS[:4]), count


def share(count, total):
    return count / total if total else None


def read_labels(input_dir):
    """Small dimension tables only: collect candidates, never expand demand."""
    routes = defaultdict(lambda: {"names": set(), "numbers": set()})
    stops = defaultdict(lambda: {"names": set(), "coordinates": set()})
    quality = {"files": {}, "invalid_coordinate_rows": 0,
               "blank_stop_id_rows": 0, "blank_route_id_rows": 0}
    dates, regions = set(), Counter()
    for filename in ("routes_all_versions.csv", "routes_latest.csv"):
        count = 0
        for _, row in csv_rows(input_dir / filename, ("ROUTE_ID", "ROUTE_NM", "ROUTE_NUM", "STDR_DE")):
            count += 1
            if row["STDR_DE"].strip():
                dates.add(row["STDR_DE"])
            rid = row["ROUTE_ID"]
            if not rid.strip():
                quality["blank_route_id_rows"] += 1
                continue
            for source, target in (("ROUTE_NM", "names"), ("ROUTE_NUM", "numbers")):
                if row[source].strip():
                    routes[rid][target].add(row[source])
        quality["files"][filename] = count
    count = 0
    for _, row in csv_rows(input_dir / "route_stops.csv", ("BSST_ID", "BSST_NM", "lat", "lon", "STDR_DE", "SIDO_NM")):
        count += 1
        if row["STDR_DE"].strip():
            dates.add(row["STDR_DE"])
        regions[row["SIDO_NM"]] += 1
        sid = row["BSST_ID"]
        if not sid.strip():
            quality["blank_stop_id_rows"] += 1
            continue
        entry = stops[sid]
        if row["BSST_NM"].strip():
            entry["names"].add(row["BSST_NM"])
        if not row["lat"].strip() and not row["lon"].strip():
            continue
        try:
            lat, lon = float(row["lat"]), float(row["lon"])
            if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError
        except ValueError:
            quality["invalid_coordinate_rows"] += 1
        else:
            entry["coordinates"].add((lat, lon))
    quality["files"]["route_stops.csv"] = count
    quality["reference_dates"] = sorted(dates)
    quality["sido_reference_row_counts"] = dict(sorted(regions.items()))
    return routes, stops, quality


def candidate_fields(values, field):
    candidates = sorted(values)
    return {field: candidates[0] if len(candidates) == 1 else None,
            field + "_status": "matched" if len(candidates) == 1 else "ambiguous" if candidates else "missing",
            field + "_candidates": candidates}


def route_display(route_id, labels):
    entry = labels.get(route_id, {"names": set(), "numbers": set()})
    return {**candidate_fields(entry["names"], "route_name"),
            **candidate_fields(entry["numbers"], "route_number")}


def stop_display(stop_id, labels):
    entry = labels.get(stop_id, {"names": set(), "coordinates": set()})
    coordinates = sorted(entry["coordinates"])
    return {**candidate_fields(entry["names"], "stop_name"),
            "lat": coordinates[0][0] if len(coordinates) == 1 else None,
            "lon": coordinates[0][1] if len(coordinates) == 1 else None,
            "coordinate_status": "matched" if len(coordinates) == 1 else "ambiguous" if coordinates else "missing",
            "coordinate_candidates": coordinates}


def coverage(ids, display, labels):
    counts = Counter()
    for (identifier,) in ids:
        counts["ids"] += 1
        counts["unmatched_ids"] += identifier not in labels
        for field, value in display(identifier, labels).items():
            if field.endswith("_status"):
                counts[field + "_" + value] += 1
    return dict(counts)


def write_csv(path, rows, fields):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, tuple))
                             else "null" if value is None else value for key, value in row.items()})


def analyze(input_dir, output_dir, temp_dir=None):
    """Read shared aggregates once each; keep large morning groups on disk."""
    started = time.perf_counter()
    input_dir, output_dir = Path(input_dir).resolve(), Path(output_dir).resolve()
    required = ("bus_od_by_hour.csv", "bus_od_0709.csv", "route_stops.csv",
                "routes_all_versions.csv", "routes_latest.csv", "stats.json", "README.md")
    input_paths = [input_dir / name for name in required]
    for path in input_paths:
        if not path.is_file():
            error(path, 0, "file")
    # A new run directory prevents partial results from overwriting earlier runs.
    if output_dir == input_dir or input_dir in output_dir.parents or output_dir.exists():
        raise AnalysisError("output:0:path")
    stats = json.loads((input_dir / "stats.json").read_text(encoding="utf-8-sig"))
    if not isinstance(stats, dict):
        error(input_dir / "stats.json", 0, "object")
    description = (input_dir / "README.md").read_text(encoding="utf-8-sig")
    observation = re.search(r"관측일[:：]\s*\*{0,2}(\d{4}-\d{2}-\d{2})", description)
    download = re.search(r"다운로드[:：]\s*\*{0,2}(\d{4}-\d{2}-\d{2})", description)
    routes, stops, label_quality = read_labels(input_dir)
    hours, total_rows = Counter(), 0
    for _, key, count in od_rows(input_dir / "bus_od_by_hour.csv"):
        total_rows += 1
        hours[key[3]] += count
    total = sum(hours.values())
    full_morning = sum(hours[hour] for hour in MORNING)
    temp_root = Path(temp_dir).resolve() if temp_dir else output_dir / ".tmp"
    if temp_root == input_dir or input_dir in temp_root.parents:
        raise AnalysisError("temporary:0:path")
    output_dir.mkdir(parents=True)
    temp_root.mkdir(parents=True, exist_ok=True)
    free_before = shutil.disk_usage(temp_root).free
    with TemporaryDirectory(prefix="taims-analysis-", dir=temp_root) as work:
        database_path = Path(work) / "morning.sqlite3"
        with closing(sqlite3.connect(database_path)) as db:
            db.execute("PRAGMA cache_size=-8192")
            db.execute("PRAGMA temp_store=FILE")
            db.execute("CREATE TABLE am(route TEXT, origin TEXT, destination TEXT, passengers INTEGER)")
            morning, morning_rows, batch = 0, 0, []
            for line, key, count in od_rows(input_dir / "bus_od_0709.csv"):
                if key[3] not in MORNING:
                    error(input_dir / "bus_od_0709.csv", line, "RIDE_DTM_H")
                morning += count
                morning_rows += 1
                batch.append((*key[:3], count))
                if len(batch) >= 5000:
                    db.executemany("INSERT INTO am VALUES (?,?,?,?)", batch)
                    batch.clear()
            db.executemany("INSERT INTO am VALUES (?,?,?,?)", batch)
            db.commit()
            rankings, checks, sizes = {}, {}, {}
            for name, columns, id_fields in (
                ("routes", "route", ("ROUTE_ID",)),
                ("od", "origin,destination", ("RIDE_BSST_ID", "ALGH_BSST_ID")),
                ("boarding", "origin", ("RIDE_BSST_ID",)),
                ("alighting", "destination", ("ALGH_BSST_ID",)),
            ):
                grouped = f"SELECT {columns},SUM(passengers) AS n FROM am GROUP BY {columns}"
                group_total, group_count = db.execute(f"SELECT COALESCE(SUM(n),0),COUNT(*) FROM ({grouped})").fetchone()
                checks[name] = {"group_total": group_total, "morning_total": morning, "equal": group_total == morning}
                sizes[name] = group_count
                rows = []
                for result in db.execute(f"{grouped} ORDER BY n DESC,{columns} LIMIT 20"):
                    row = dict(zip(id_fields, result[:-1]))
                    row.update(passenger_count=result[-1], share=share(result[-1], morning))
                    if name == "routes":
                        row.update(route_display(result[0], routes))
                    elif name == "od":
                        for prefix, identifier in zip(("origin_", "destination_"), result[:-1]):
                            row.update({prefix + k: v for k, v in stop_display(identifier, stops).items()})
                    else:
                        row.update(stop_display(result[0], stops))
                    rows.append(row)
                rankings[name] = rows
            joins = {
                "morning_routes": coverage(db.execute("SELECT DISTINCT route FROM am"), route_display, routes),
                "morning_boarding": coverage(db.execute("SELECT DISTINCT origin FROM am"), stop_display, stops),
                "morning_alighting": coverage(db.execute("SELECT DISTINCT destination FROM am"), stop_display, stops),
            }
            database_bytes = database_path.stat().st_size
        hourly = [{"RIDE_DTM_H": hour, "passenger_count": hours[hour], "share": share(hours[hour], total)} for hour in HOURS]
        comparisons = {}
        for key, actual in (("od_psng", total), ("am_psng", morning), ("od_pairs", total_rows), ("am_pairs", morning_rows)):
            expected = stats.get(key)
            comparisons[key] = {"actual": actual, "stats": expected,
                                "difference": actual - expected if type(expected) is int else None,
                                "equal": actual == expected if type(expected) is int else None}
        reconciliations = {
            "hourly_total": {"actual": sum(r["passenger_count"] for r in hourly), "input_total": total,
                             "equal": sum(r["passenger_count"] for r in hourly) == total},
            "morning_files": {"full_od_morning": full_morning, "morning_file": morning,
                              "difference": full_morning - morning, "equal": full_morning == morning},
            "morning_groups": checks,
        }
        all_checks = [reconciliations["hourly_total"]["equal"], reconciliations["morning_files"]["equal"]]
        all_checks.extend(check["equal"] for check in checks.values())
        all_checks.extend(check["equal"] for check in comparisons.values())
        summary = {
            "scope": "공유 자료 범위의 버스 단일 이용구간 수요; 서울 내부 필터 미적용",
            "observation_date_from_readme": observation.group(1) if observation else None,
            "download_date_from_readme": download.group(1) if download else None,
            "input_files": [{"path": str(path), "bytes": path.stat().st_size} for path in input_paths],
            "total_od_passengers": total, "total_od_rows": total_rows,
            "morning_passengers": morning, "morning_rows": morning_rows,
            "morning_share_of_od_total": share(morning, total),
            "morning_window": "승차 태그 시각 [07:00,09:00), 07·08시",
            "denominator": "시간대·오전 비중: 전체 OD 인원; 순위 비중: 오전 파일 전체 인원",
            "stats_bus_passengers_reference_only": stats.get("psng"),
            "reconciliations": reconciliations, "stats_comparisons": comparisons,
            "all_reconciliations_passed": all(value is True for value in all_checks),
            "group_counts": sizes,
            "top20_sums_not_full_totals": {name: sum(r["passenger_count"] for r in rows) for name, rows in rankings.items()},
            "top3": {name: rows[:3] for name, rows in rankings.items()},
            "label_quality": label_quality, "label_coverage": joins,
            "display_policy": "ID 집계 후 연결. 후보가 하나일 때만 대표값, 없거나 여러 개면 null과 상태·후보 목록. latest는 표시 참고이며 유효 노선 판정에 사용하지 않음.",
            "null_policy": "분모가 0이면 비중 null; CSV도 문자열 null로 기록",
            "resources": {"temporary_database_path": str(database_path), "temporary_root": str(temp_root),
                          "temporary_database_removed_after_run": True, "database_bytes": database_bytes,
                          "sqlite_cache_kib": 8192, "insert_batch_rows": 5000,
                          "sqlite_sort_temp_location": "SQLite/OS 기본 임시 디렉터리; DB는 지정 temporary_root",
                          "free_bytes_before": free_before, "free_bytes_after_aggregation": shutil.disk_usage(temp_root).free,
                          "peak_memory_measured": False,
                          "memory_limitations": "CSV 행 목록 미보관. 차원 ID별 표시 후보는 메모리 사용; SQLite 캐시는 프로세스 전체 메모리 상한이 아님."},
            "limitations": ["공유 정리본 기반 대사이며 원본부터 OD를 재생성한 독립 검증이 아님.",
                            "관측 기준일과 다음 날짜 승차 기록의 서비스일 귀속은 별개이며, 시 단위 정리본으로 다음 날짜 귀속을 검증하지 않음.",
                            "하차 정보 부족 등으로 정리본에서 제외된 수요는 복원하지 않음.",
                            "여정 최종 목적지·여정 수·파업 영향 여정 비율·여정 연결 성공률은 계산하지 않음.",
                            "환승·여정 연결, 방향·노선 버전·실제 운행 가능성, 좌표 CRS는 추가 검증 대상.",
                            "시 단위 정리본으로 서비스일 자정 이후 귀속을 재구성하지 않음.",
                            "상위 20개 합계는 전체 합계와 다름; OD 순위는 노선을 합친 정류장 쌍."],
            "contributions": {"teammate": "버스 추출·가중 OD 집계·기초 정리본 및 좌표 변환 제공",
                              "this_analysis": "시간대·오전 순위·비중, 표시 후보 연결, 합계 대사 및 로컬 보고서"},
        }
        write_csv(output_dir / "hourly.csv", hourly, ("RIDE_DTM_H", "passenger_count", "share"))
        for name, rows in rankings.items():
            # Empty cases still get a useful, stable header.
            fields = list(rows[0]) if rows else list({"routes": ("ROUTE_ID",), "od": ("RIDE_BSST_ID", "ALGH_BSST_ID"),
                                                   "boarding": ("RIDE_BSST_ID",), "alighting": ("ALGH_BSST_ID",)}[name]) + ["passenger_count", "share"]
            write_csv(output_dir / f"morning_{name}_top20.csv", rows, fields)
        summary["resources"]["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        lines = ["# TAIMS 공유 정리본 버스 기초 수요 분석", "", summary["scope"], "",
                 f"관측일(공유 README): {summary['observation_date_from_readme']}; 다운로드: {summary['download_date_from_readme']}",
                 f"전체 OD 인원: {total:,}명; 오전: {morning:,}명; 오전 비중: {summary['morning_share_of_od_total']}",
                 "전체는 원본 버스 전체가 아닌 OD 파일 인원이다. 원본 버스 합계는 stats 참고값으로만 기록한다.",
                 f"대사 모두 일치: {summary['all_reconciliations_passed']}", "",
                 "## 합계 대사", "", "```json", json.dumps({"reconciliations": reconciliations, "stats": comparisons}, ensure_ascii=False, indent=2), "```",
                 "", "## 표시 정보 품질", "", "```json", json.dumps({"quality": label_quality, "coverage": joins}, ensure_ascii=False, indent=2), "```",
                 "", summary["display_policy"], "", "## 상위 결과", ""]
        for name, rows in rankings.items():
            lines.extend([f"### {name} (상위 20개 합계: {summary['top20_sums_not_full_totals'][name]:,}명)", ""])
            for row in rows[:3]:
                lines.append("- " + json.dumps(row, ensure_ascii=False))
            lines.append("")
        lines.extend(["## 기준과 한계", "", summary["denominator"], summary["null_policy"], ""])
        lines.extend("- " + item for item in summary["limitations"])
        lines.extend(["", "## 실행 자원", "", "```json", json.dumps(summary["resources"], ensure_ascii=False, indent=2), "```",
                      "", "팀원: " + summary["contributions"]["teammate"], "이번 분석: " + summary["contributions"]["this_analysis"]])
        (output_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=Path("raw/from_teammate"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/processed/taims_demand"))
    parser.add_argument("--temp-dir", type=Path)
    args = parser.parse_args()
    try:
        result = analyze(args.input_dir, args.output_dir, args.temp_dir)
    except (AnalysisError, OSError, sqlite3.Error, ValueError, KeyError, TypeError, csv.Error) as exc:
        # Never print a CSV row or a database parameter in diagnostics.
        parser.exit(2, f"{exc}\n" if isinstance(exc, AnalysisError) else f"analysis failed: {type(exc).__name__}\n")
    print(json.dumps({"output_dir": str(args.output_dir.resolve()),
                      "total_od_passengers": result["total_od_passengers"],
                      "morning_passengers": result["morning_passengers"],
                      "all_reconciliations_passed": result["all_reconciliations_passed"],
                      "elapsed_seconds": result["resources"]["elapsed_seconds"]}))
    if not result["all_reconciliations_passed"]:
        parser.exit(1, "aggregate reconciliation mismatch; see local summary\n")


if __name__ == "__main__":
    main()
