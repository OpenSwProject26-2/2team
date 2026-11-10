"""Convert validated source-order links to B-shaped boarding-hour bins, no arrivals."""

import argparse
from collections import Counter
import csv
from itertools import zip_longest
import json
from pathlib import Path

from .demand_analysis import AnalysisError, csv_rows, error
from .extract_guro_draft import digest, dump_json
from .link_demand import (RULE_VERSION, VisitIndex, load_network, read_json,
                          reconcile_extraction, validate_saved)


BIN_FIELDS = ("aggregate_id", "origin", "destination", "bin_start_s", "bin_width_s", "passenger_count")
BIN_RULE_VERSION = "boarding-hour-bin-draft-v1"


def make_bin(record):
    source = record["source"]
    if source["RIDE_DTM_H"] not in ("07", "08"):
        error("od_links.jsonl", record["input_csv_line"], "RIDE_DTM_H")
    return {"aggregate_id": record["aggregate_id"], "origin": source["RIDE_BSST_ID"],
            "destination": source["ALGH_BSST_ID"],
            "bin_start_s": {"07": 25200, "08": 28800}[source["RIDE_DTM_H"]],
            "bin_width_s": 3600, "passenger_count": int(source["PSNG_NUM"])}


def records(directory):
    with (directory / "od_links.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line)


def saved_bins(path):
    for line, row in csv_rows(path, BIN_FIELDS):
        if set(row) != set(BIN_FIELDS):
            error(path, line, "fields")
        for field in BIN_FIELDS[3:]:
            if not row[field].isascii() or not row[field].isdecimal():
                error(path, line, field)
        yield row


def validate_bins(links, output, summary):
    issues, counts = Counter(), {}
    for filename, included_only in (("demand_bins_all.csv", False), ("demand_bins_included.csv", True)):
        expected = ({k: str(v) for k, v in make_bin(r).items()} for r in records(links)
                    if not included_only or r["disposition"] == "included")
        size = {"rows": 0, "passengers": 0}
        for a, b in zip_longest(expected, saved_bins(output / filename)):
            if a != b:
                issues[filename + ".content_or_cardinality"] += 1
            if b is not None:
                size["rows"] += 1
                size["passengers"] += int(b["passenger_count"])
        counts[filename] = size
        target = summary["dispositions"]["included"] if included_only else summary["total"]
        if size != target:
            issues[filename + ".summary"] += 1
    return {"all_checks_passed": not issues, "issues": dict(issues), "counts": counts}


def convert(links, draft, network_dir, output):
    links, draft, network_dir, output = map(Path, (links, draft, network_dir, output))
    if output.exists() or any(output.resolve().is_relative_to(p.resolve()) for p in (links, draft, network_dir)):
        error(output, 0, "output_exists_or_inside_input")
    report = read_json(links / "report.json")
    if report.get("rule_version") != RULE_VERSION or report.get("all_checks_passed") is not True:
        error("report.json", 0, "successful_linking_v2_required")
    # Recheck the prior report, source lineage, every link and scope. No trust in
    # an all_checks_passed marker alone, and no need to import B or run arrivals.
    network, _ = load_network(draft, network_dir)
    paths = [draft / f for f in ("morning_od.csv", "route_visits.csv", "stops.json", "validation_report.json")]
    paths += [network_dir / f for f in ("stops.json", "visits.json", "segments.json", "quality_report.json")]
    # stops.json occurs in both locations: compare full original paths, not basenames.
    inherited = {str(Path(entry["path"]).resolve()): entry["sha256"] for entry in report["inputs"]}
    for path in paths:
        if inherited.get(str(path.resolve())) != digest(path)["sha256"]:
            error(path, 0, "linking_input_checksum_mismatch")
    source = draft / "morning_od.csv"
    saved = validate_saved(source, links, digest(source)["sha256"], VisitIndex(network), report["summary"])
    reconciliation = reconcile_extraction(report["summary"], read_json(draft / "validation_report.json"))
    if not saved["all_checks_passed"] or not reconciliation["all_checks_passed"]:
        error("report.json", 0, "linking_source_or_extraction_reconciliation_failed")
    paths += [links / "report.json", links / "od_links.jsonl"]
    paths = [p.resolve() for p in paths]
    snapshots = [digest(p) for p in paths]
    output.mkdir(parents=True, exist_ok=False)
    with (output / "demand_bins_all.csv").open("x", encoding="utf-8", newline="") as all_stream, \
            (output / "demand_bins_included.csv").open("x", encoding="utf-8", newline="") as included_stream:
        writers = [csv.DictWriter(s, fieldnames=BIN_FIELDS) for s in (all_stream, included_stream)]
        for writer in writers:
            writer.writeheader()
        for record in records(links):
            row = make_bin(record)
            writers[0].writerow(row)
            if record["disposition"] == "included":
                writers[1].writerow(row)
    validation = validate_bins(links, output, report["summary"])
    unchanged = snapshots == [digest(p) for p in paths]
    result = {"rule_version": BIN_RULE_VERSION, "status": "boarding_hour_bins_not_arrival_realization",
              "scope": report["scope"], "route_count": report["route_count"],
              "inputs": snapshots, "summary": report["summary"],
              "saved_validation": validation, "linking_revalidated": saved,
              "extraction_reconciliation": reconciliation, "inputs_unchanged": unchanged,
              "arrival_s_generated": False, "engine_executed": False,
              "b_check_rows_executed_by_this_tool": False, "contract_review": report.get("contract_review"),
              "all_checks_passed": validation["all_checks_passed"] and unchanged}
    dump_json(output / "report.json", result)
    (output / "README.md").write_text(
        f"# 개발용 {report['route_count']}개 노선 추출본의 오전 OD 전체: B 시간대 초안\n\n"
        "원래 노선·원천 그룹·방문·출처 대응은 연결 입력 od_links.jsonl의 aggregate_id로 참조한다.\n"
        "07시=25200, 08시=28800, 폭=3600초. 승차 태그 시간대이며 실제 도착 시각이 아니다.\n"
        "전체 CSV는 동일 출도착도 보존하므로 B check_rows에 그대로 전달할 수 없다.\n"
        "포함 CSV는 원천 순서 유일 연결 초안일 뿐 실제 방향·유효 버전·운행 가능성 확인이 아니다.\n"
        "arrival_s·발생 정책·차량/주행/회차/복귀 조건을 생성하지 않으며 B 시뮬레이션은 실행하지 않았다.\n"
        "추출 보고서 대사·출처 체크섬·저장 행 내용 검사이며 원본 OD 독립 재집계가 아니다.\n",
        encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("links-dir", "draft-dir", "network-dir", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        report = convert(args.links_dir, args.draft_dir, args.network_dir, args.output_dir)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, csv.Error) as exc:
        parser.exit(2, f"{exc}\n" if isinstance(exc, AnalysisError) else f"bin conversion failed: {type(exc).__name__}\n")
    print(json.dumps({"summary": report["summary"], "all_checks_passed": report["all_checks_passed"]}))
    if not report["all_checks_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
