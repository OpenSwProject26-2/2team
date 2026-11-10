"""Link aggregate boarding-hour OD to preserved source visits, without arrivals."""

import argparse
from collections import Counter, defaultdict
import csv
from datetime import date
from itertools import zip_longest
import json
from pathlib import Path
import re
import time

from .clean_network import canonical, stable_id, load_visits, validate_network, settings
from .demand_analysis import AnalysisError, csv_rows, error, OD_FIELDS
from .extract_guro_draft import digest, dump_json


RULE_VERSION = "source-order-demand-linking-v2"
EXTRACTION_VERSION = "guro-real-input-extraction-draft-v1"
SOURCE_FIELDS = ("source_csv_line", *OD_FIELDS, "endpoint_class",
                 "internal_source_order", "route_stop_reference")
STATUSES = ("unique_connection", "multiple_candidates", "no_connection", "same_stop")
DISPOSITIONS = ("included", "excluded", "held")


def read_json(path):
    with Path(path).open(encoding="utf-8") as stream:
        return json.load(stream)


def extraction_report(document, config):
    """Check the existing extractor's schema; never manufacture expected totals."""
    def require(condition, field):
        if not condition:
            error("validation_report.json", 0, field)

    require(isinstance(document, dict), "object")
    require(document.get("schema_version") == EXTRACTION_VERSION, "schema_version")
    require(document.get("status") == "development_only_not_operational_input", "status")
    require(document.get("all_checks_passed") is True, "all_checks_passed")
    routes = document.get("route_ids")
    require(isinstance(routes, list) and bool(routes), "route_ids")
    require(all(isinstance(r, str) and r.strip() for r in routes), "route_ids")
    require(len(routes) == len(set(routes)), "route_ids")
    require(set(routes) == set(config["route_ids"]), "route_ids_vs_cleaning_config")
    groups = document.get("selected_source_groups")
    require(isinstance(groups, list) and bool(groups), "selected_source_groups")
    pairs = []
    for group in groups:
        require(isinstance(group, dict), "selected_source_groups")
        require(group.get("route_id") in routes, "selected_source_groups.route_id")
        name = group.get("source_route_name")
        require(isinstance(name, str) and bool(name.strip()), "selected_source_groups.source_route_name")
        pairs.append((group["route_id"], name))
    require(len(set(pairs)) == len(pairs), "selected_source_groups")
    require(set(pairs) == {(g["route_id"], g["source_route_name"]) for g in config["selected_groups"]},
            "selected_source_groups_vs_cleaning_config")
    require({p[0] for p in pairs} == set(routes), "selected_source_groups.route_coverage")
    totals = document.get("route_totals")
    require(isinstance(totals, dict) and set(totals) == set(routes), "route_totals")
    for field, value in [("total_morning", document.get("total_morning")),
                         *(("route_totals", v) for v in totals.values())]:
        require(isinstance(value, dict), field)
        for name in ("rows", "passengers"):
            require(type(value.get(name)) is int and value[name] >= 0, field + "." + name)
    for name in ("rows", "passengers"):
        require(sum(t[name] for t in totals.values()) == document["total_morning"][name],
                "route_totals_vs_total_morning." + name)
    observed = document.get("observation_date_from_survey")
    require(isinstance(observed, str) and re.fullmatch(r"[0-9]{8}", observed) is not None,
            "observation_date_from_survey")
    try:
        date(int(observed[:4]), int(observed[4:6]), int(observed[6:]))
    except ValueError:
        error("validation_report.json", 0, "observation_date_from_survey")
    sources = document.get("sources")
    require(isinstance(sources, list) and bool(sources), "sources")
    names = []
    for source in sources:
        require(isinstance(source, dict), "sources")
        path = source.get("path")
        require(isinstance(path, str) and bool(path.strip()), "sources.path")
        require(type(source.get("bytes")) is int and source["bytes"] >= 0, "sources.bytes")
        require(isinstance(source.get("sha256"), str)
                and re.fullmatch(r"[0-9a-f]{64}", source["sha256"]) is not None, "sources.sha256")
        names.append(path.replace("\\", "/").rsplit("/", 1)[-1])
    require(len(names) == len(set(names)), "sources.duplicate_filename")
    require({"survey.json", "report.md", "route_stops.csv", "bus_od_0709.csv"} <= set(names),
            "sources.required_upstream_files")
    return document


def reconcile_extraction(summary, extraction):
    """Expected values come only from the checksum-bound extraction report."""
    fields = ("rows", "passengers")
    total_expected = {f: extraction["total_morning"][f] for f in fields}
    route_comparisons = {}
    for rid in sorted(set(extraction["route_ids"]) | set(summary["routes"])):
        expected = extraction["route_totals"].get(rid)
        expected = {f: expected[f] for f in fields} if expected is not None else None
        actual = summary["routes"].get(rid, {"rows": 0, "passengers": 0})
        route_comparisons[rid] = {"expected": expected, "actual": actual, "matches": expected == actual}
    checks = {"total_rows": summary["total"]["rows"] == total_expected["rows"],
              "total_passengers": summary["total"]["passengers"] == total_expected["passengers"],
              "no_routes_outside_report": set(summary["routes"]) <= set(extraction["route_ids"]),
              "all_route_totals": all(r["matches"] for r in route_comparisons.values())}
    return {"expected_total": total_expected, "actual_total": summary["total"],
            "routes": route_comparisons, "checks": checks, "all_checks_passed": all(checks.values())}


class VisitIndex:
    """All source name groups, never a preferred group or assumed direction."""

    def __init__(self, network):
        self.visits = {}
        self.segments = {}
        self.groups = defaultdict(list)
        self.by_route = defaultdict(list)
        self.positions = defaultdict(lambda: defaultdict(list))
        self.edges = {}
        stops = {s["stop_id"] for s in network["stops"]}
        for v in network["visits"]:
            vid = v["visit_id"]
            if (not isinstance(vid, str) or not vid or vid in self.visits
                    or type(v["source_sequence"]) is not int or v["source_sequence"] < 1
                    or v["stop_id"] not in stops):
                error("visits.json", 0, "visit_reference_or_sequence")
            self.visits[vid] = v
            self.groups[v["source_group_id"]].append(v)
            self.positions[v["source_group_id"]][v["source_sequence"]].append(v)
        for gid, members in sorted(self.groups.items()):
            if len({(v["source_route_id"], v["source_route_name"]) for v in members}) != 1:
                error("visits.json", 0, "source_group_id")
            members.sort(key=lambda v: (v["source_sequence"], v["visit_id"]))
            self.by_route[members[0]["source_route_id"]].append(gid)
        for s in network["segments"]:
            sid = s["segment_id"]
            a = self.visits.get(s["origin_visit_id"])
            b = self.visits.get(s["destination_visit_id"])
            if not a or not b:
                error("segments.json", 0, "visit_reference")
            gid = a["source_group_id"]
            pair = (a["visit_id"], b["visit_id"])
            if (sid in self.segments or pair in self.edges
                    or b["source_group_id"] != gid or s["source_group_id"] != gid
                    or s["source_route_id"] != a["source_route_id"]
                    or s["origin_stop_id"] != a["stop_id"]
                    or s["destination_stop_id"] != b["stop_id"]
                    or s["origin_source_sequence"] != a["source_sequence"]
                    or s["destination_source_sequence"] != b["source_sequence"]
                    or b["source_sequence"] != a["source_sequence"] + 1
                    or len(self.positions[gid][a["source_sequence"]]) != 1
                    or len(self.positions[gid][b["source_sequence"]]) != 1):
                error("segments.json", 0, "source_adjacency")
            self.edges[pair] = s
            self.segments[sid] = s

    def path(self, a, b):
        positions = self.positions[a["source_group_id"]]
        members = []
        for seq in range(a["source_sequence"], b["source_sequence"] + 1):
            slot = positions.get(seq, [])
            if len(slot) != 1:
                return None, "sequence_gap" if not slot else "sequence_conflict"
            members.append(slot[0])
        edges = []
        for origin, destination in zip(members, members[1:]):
            edge = self.edges.get((origin["visit_id"], destination["visit_id"]))
            if edge is None:
                return None, "missing_segment"
            edges.append(edge)
        dates = sorted({variant["fields"]["STDR_DE"] for v in members
                        for variant in v["source_variants"]})
        refs = sorted({row["source_row_id"] for v in members
                       for variant in v["source_variants"] for row in variant["source_rows"]})
        return {"source_group_id": a["source_group_id"],
                "source_route_id": a["source_route_id"], "source_route_name": a["source_route_name"],
                "group_role": a["group_role"], "origin_visit_id": a["visit_id"],
                "destination_visit_id": b["visit_id"],
                "origin_source_sequence": a["source_sequence"],
                "destination_source_sequence": b["source_sequence"],
                "visit_ids": [v["visit_id"] for v in members],
                "segment_ids": [s["segment_id"] for s in edges], "source_row_ids": refs,
                "source_date_candidates": dates, "effective_version_status": "unverified",
                "actual_group_assignment_status": "unverified",
                "operating_direction_status": "unverified_source_order_only"}, None

    def match(self, route, origin, destination):
        candidates, rejected, examined = [], [], []
        for gid in self.by_route.get(route, []):
            members = self.groups[gid]
            origins = [v for v in members if v["stop_id"] == origin]
            destinations = [v for v in members if v["stop_id"] == destination]
            pairs = [(a, b) for a in origins for b in destinations
                     if a["source_sequence"] < b["source_sequence"]]
            examined.append({"source_group_id": gid, "origin_visits": len(origins),
                             "destination_visits": len(destinations), "ordered_pairs": len(pairs)})
            if origin == destination:
                continue
            for a, b in pairs:
                path, reason = self.path(a, b)
                if path is not None:
                    candidates.append(path)
                else:
                    rejected.append({"source_group_id": gid, "origin_visit_id": a["visit_id"],
                                     "destination_visit_id": b["visit_id"], "reason": reason})
        status = ("same_stop" if origin == destination else "no_connection" if not candidates
                  else "unique_connection" if len(candidates) == 1 else "multiple_candidates")
        return status, candidates, rejected, examined


def source_rows(path):
    seen = set()
    for line, row in csv_rows(path, SOURCE_FIELDS):
        for field in ("ROUTE_ID", "RIDE_BSST_ID", "ALGH_BSST_ID"):
            if not row[field].strip():
                error(path, line, field)
        for field in ("source_csv_line", "PSNG_NUM"):
            text = row[field]
            if not text or not text.isascii() or not text.isdecimal():
                error(path, line, field)
            try:
                number = int(text)
            except ValueError:
                error(path, line, field)
            if field == "source_csv_line" and (number < 2 or number in seen):
                error(path, line, field)
        seen.add(int(row["source_csv_line"]))
        if row["RIDE_DTM_H"] not in ("07", "08"):
            error(path, line, "RIDE_DTM_H")
        yield line, {f: row[f] for f in SOURCE_FIELDS}


def link_row(line, row, checksum, index):
    route, origin, destination = (row[f] for f in OD_FIELDS[:3])
    status, candidates, rejected, examined = index.match(route, origin, destination)
    disposition = {"unique_connection": "included", "same_stop": "excluded"}.get(status, "held")
    aid = stable_id("aggregate", [checksum, line, row["source_csv_line"]])
    return {"aggregate_id": aid, "input_csv_line": line, "source": row,
            "classification": status, "disposition": disposition,
            "disposition_basis": "structural_source_order_draft_only" if disposition == "included"
            else "same_stop_unsupported_by_current_b_not_invalid_source" if disposition == "excluded"
            else "insufficient_or_ambiguous_source_connection",
            "candidate_count": len(candidates), "candidates": candidates,
            "rejected_ordered_pairs": rejected, "examined_groups": examined,
            "actual_direction_status": "unverified", "route_version_status": "unverified",
            "arrival_policy_status": "not_agreed_no_arrival_generated",
            "engine_ready": False}


def summary_empty():
    return {"total": {"rows": 0, "passengers": 0},
            "classifications": {s: {"rows": 0, "passengers": 0} for s in STATUSES},
            "dispositions": {s: {"rows": 0, "passengers": 0} for s in DISPOSITIONS},
            "hours": {}, "routes": {}}


def accumulate(summary, record):
    count = int(record["source"]["PSNG_NUM"])
    for bucket in (summary["total"], summary["classifications"][record["classification"]],
                   summary["dispositions"][record["disposition"]],
                   summary["hours"].setdefault(record["source"]["RIDE_DTM_H"], {"rows": 0, "passengers": 0}),
                   summary["routes"].setdefault(record["source"]["ROUTE_ID"], {"rows": 0, "passengers": 0})):
        bucket["rows"] += 1
        bucket["passengers"] += count


def validate_saved(source, output, checksum, index, summary):
    """Re-read source and links; compare every row, not only totals."""
    issues = Counter()
    actual = summary_empty()
    with (output / "od_links.jsonl").open(encoding="utf-8") as links:
        def records():
            for line in links:
                yield json.loads(line)
        for original, saved in zip_longest(source_rows(source), records()):
            if original is None or saved is None:
                issues["source_record_cardinality"] += 1
                continue
            expected = link_row(*original, checksum, index)
            if canonical(expected) != canonical(saved):
                issues["source_record_content"] += 1
            accumulate(actual, expected)
    if actual != summary:
        issues["summary"] += 1
    for field in ("rows", "passengers"):
        if sum(v[field] for v in summary["dispositions"].values()) != summary["total"][field]:
            issues["disposition_conservation"] += 1
    return {"all_checks_passed": not issues, "issues": dict(issues),
            "recomputed_summary": actual, "basis": "saved_rows_and_source_recomputed_not_raw_independent_od"}


def load_network(draft, directory):
    network = {key: read_json(directory / filename)[key] for filename, key in
               (("stops.json", "stops"), ("visits.json", "visits"), ("segments.json", "segments"))}
    report = read_json(directory / "quality_report.json")
    config = settings(report["config"])
    source = draft / "route_visits.csv"
    checksum = digest(source)["sha256"]
    if not isinstance(report.get("inputs"), dict):
        error("quality_report.json", 0, "inputs_missing_or_invalid")
    checksum_checks = {}
    for filename in ("route_visits.csv", "stops.json", "validation_report.json"):
        entry = report.get("inputs", {}).get(filename)
        if not isinstance(entry, dict) or not isinstance(entry.get("sha256"), str) \
                or re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) is None:
            error("quality_report.json", 0, "inputs." + filename + ".sha256_missing_or_invalid")
        actual = digest(draft / filename)["sha256"]
        if entry["sha256"] != actual:
            error(filename, 0, "cleaning_source_checksum_mismatch")
        checksum_checks[filename] = {"expected_sha256": entry["sha256"], "actual_sha256": actual,
                                     "matches": True}
    extraction_report(read_json(draft / "validation_report.json"), config)
    validation = validate_network(network, load_visits(source), config, checksum,
                                  read_json(draft / "stops.json"))
    if not validation["all_checks_passed"]:
        error("quality_report.json", 0, "network_source_validation")
    validation["extraction_provenance"] = {"input_checksums": checksum_checks,
                                           "report_schema_valid": True, "route_scope_matches": True,
                                           "selected_groups_match": True}
    return network, validation


def run(draft, network_dir, output, evidence_path=None):
    started = time.perf_counter()
    draft, network_dir, output = map(Path, (draft, network_dir, output))
    for source_dir in (draft, network_dir):
        if output.resolve().is_relative_to(source_dir.resolve()):
            error(output, 0, "output_inside_input")
    if output.exists():
        error(output, 0, "output_exists")
    network, network_validation = load_network(draft, network_dir)
    index = VisitIndex(network)
    source = draft / "morning_od.csv"
    checksum = digest(source)["sha256"]
    inputs = [source, draft / "route_visits.csv", draft / "stops.json", draft / "validation_report.json",
              *(network_dir / f for f in ("stops.json", "visits.json", "segments.json", "quality_report.json"))]
    evidence = read_json(evidence_path) if evidence_path else None
    if evidence_path:
        inputs.append(Path(evidence_path))
    inputs = [p.resolve() for p in inputs]
    snapshots = [digest(p) for p in inputs]
    summary = summary_empty()
    output.mkdir(parents=True, exist_ok=False)
    # Context managers close all streams even on failure. Partial outputs have no report success marker.
    with (output / "od_links.jsonl").open("x", encoding="utf-8") as links:
        for line, row in source_rows(source):
            record = link_row(line, row, checksum, index)
            links.write(canonical(record) + "\n")
            accumulate(summary, record)
    saved = validate_saved(source, output, checksum, index, summary)
    unchanged = snapshots == [digest(p) for p in inputs]
    extraction = read_json(draft / "validation_report.json")
    reconciliation = reconcile_extraction(summary, extraction)
    groups = []
    for gid, members in sorted(index.groups.items()):
        first = members[0]
        groups.append({"source_group_id": gid, "source_route_id": first["source_route_id"],
                       "source_route_name": first["source_route_name"], "group_role": first["group_role"],
                       "visits": len(members), "source_rows": sum(v["source_row_count"] for v in members),
                       "source_date_candidates": sorted({x["fields"]["STDR_DE"] for v in members for x in v["source_variants"]}),
                       "actual_direction_status": "unverified", "effective_version_status": "unverified"})
    report = {"rule_version": RULE_VERSION, "status": "development_links_not_execution_input",
              "scope": "entire_morning_od_of_extraction", "route_count": len(extraction["route_ids"]),
              "summary": summary, "all_source_groups": groups,
              "inputs": snapshots, "upstream_sources_inherited": extraction.get("sources", []),
              "network_saved_source_validation": network_validation, "saved_validation": saved,
              "extraction_reconciliation": reconciliation,
              "inputs_unchanged": unchanged, "arrival_s_generated": False,
              "observation_reference_date": extraction.get("observation_date_from_survey"),
              "service_date_assignment": "unverified_including_next_date_records",
              "time_reference": {"timezone": "Asia/Seoul", "unit": "s", "origin": "service_date_midnight",
                                 "semantics": "boarding_tag_hour_not_passenger_arrival", "bin_width_s": 3600},
              "contract_review": evidence,
              "all_checks_passed": saved["all_checks_passed"] and unchanged and reconciliation["all_checks_passed"],
              "elapsed_seconds": time.perf_counter() - started}
    dump_json(output / "report.json", report)
    (output / "README.md").write_text(
        "# 개발용 수요 연결 초안\n\n"
        + f"실행 범위는 개발용 {len(extraction['route_ids'])}개 노선 추출본의 오전 OD 전체다. 서울 전체 분석과 다르다.\n"
        + "공식 대상·운행 방향·서비스일·실행 입력 확정이 아니다. PSNG_NUM은 이용구간 인원이다.\n\n"
        + "- od_links.jsonl: 전체 OD와 모든 원천 그룹의 후보·차단 근거·방문/구간/원천 행 대응.\n"
        + "- B 시간 bin CSV 변환은 별도 data.demand_bins 도구로 수행한다.\n"
        + "- report.json: 포함/제외/보류, 저장 내용 재검증, 입력 체크섬·계승 출처·관측 기준일.\n\n"
        + "included=유일 연결 개발용 포함, excluded=동일 출도착 현재 B 미지원 분리, held=다중 후보 또는 연결 없음 보류.\n"
        + "모든 원본 OD는 보존한다. arrival_s는 생성하지 않으며 bin 시작을 도착으로 대체하지 않는다.\n"
        + "발생 정책, 유효 방향/버전, 주행시간·허용 전이·정차·회차·복귀·차량·운영 조건·운행편이 부족하다.\n"
        + "관측 기준일과 서비스일 귀속은 다르며 다음 날짜 기록의 귀속은 미확인이다.\n"
        + "공유 정리본 기반 저장 검증이며 원본 독립 재집계·B/C 실행·전체 여정 연결·독립 검증이 아니다.\n\n"
        + "추출 보고서 total_morning·route_totals와 현재 OD를 대조하고 정제 당시 추출 보고서 체크섬을 확인했다.\n"
        + "```json\n" + json.dumps(summary, ensure_ascii=False, indent=2) + "\n```\n",
        encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draft-dir", type=Path, required=True)
    parser.add_argument("--network-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--contract-evidence", type=Path)
    args = parser.parse_args()
    try:
        report = run(args.draft_dir, args.network_dir, args.output_dir, args.contract_evidence)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, csv.Error) as exc:
        parser.exit(2, f"{exc}\n" if isinstance(exc, AnalysisError) else f"linking failed: {type(exc).__name__}\n")
    print(json.dumps({"summary": report["summary"], "all_checks_passed": report["all_checks_passed"]}))
    if not report["all_checks_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
