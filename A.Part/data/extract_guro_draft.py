"""Extract a source-preserving development draft from the recommended bundle."""

import argparse
from collections import Counter, defaultdict
from contextlib import ExitStack
import csv
import hashlib
import json
import math
from pathlib import Path
import time

from .candidate_survey import group_quality, slots, visit_groups, visit_source_rows
from .demand_analysis import AnalysisError, csv_rows, error, od_rows, stop_display


ENDPOINT_CLASSES = ("both_inside", "boarding_only_inside", "alighting_only_inside", "both_outside")
ORDER_CLASSES = ("forward", "reverse", "same_stop", "undetermined")
OD_FIELDS = ("source_csv_line", "ROUTE_ID", "RIDE_BSST_ID", "ALGH_BSST_ID", "RIDE_DTM_H",
             "PSNG_NUM", "endpoint_class", "internal_source_order", "route_stop_reference")
VISIT_FIELDS = ("SERIAL_NUM", "COMBINE_TYPE", "TRCM_ID", "TRCM_NM", "ROUTE_ID", "ROUTE_NM",
                "BSST_ID", "BSST_ORDER", "BSST_SUM_DISTANCE", "STDR_DE")
ADMIN_FIELDS = ("AREA_CN_ID", "SIDO_NM", "SIGUN_NM", "DONG_NM")


def endpoint_class(origin, destination, common_stops):
    inside = set(common_stops)
    return {(True, True): "both_inside", (True, False): "boarding_only_inside",
            (False, True): "alighting_only_inside", (False, False): "both_outside"}[
                origin in inside, destination in inside]


def internal_order(origin, destination, common_order):
    if origin not in common_order or destination not in common_order:
        return "undetermined"
    if origin == destination:
        return "same_stop"
    if common_order.count(origin) != 1 or common_order.count(destination) != 1:
        return "undetermined"
    return "forward" if common_order.index(origin) < common_order.index(destination) else "reverse"


def evidence_matches(rows, stop_ids, sequences):
    if len(stop_ids) != len(sequences) or len(sequences) < 2:
        return False
    positions = slots(rows)
    return (all(b == a + 1 for a, b in zip(sequences, sequences[1:]))
            and all(positions.get(i) == {sid} for i, sid in zip(sequences, stop_ids)))


def make_corridor(groups, candidate):
    evidence = candidate["all_routes_shared_source_run_through_hub"]
    stop_ids = evidence["stop_ids"]
    selected_groups = set()
    by_route = defaultdict(list)
    for route in evidence["evidence"]:
        for match in route["matches"]:
            key = (route["route_id"], match["route_name"])
            selected_groups.add(key)
            by_route[key[0]].append({"route_name": key[1], "source_sequences": match["sequences"],
                                   "verified": evidence_matches(groups.get(key, []), stop_ids, match["sequences"])})
    edges = []
    for i, (origin, destination) in enumerate(zip(stop_ids, stop_ids[1:])):
        source_evidence = []
        for route_id in candidate["route_ids"]:
            for match in by_route[route_id]:
                sequence = match["source_sequences"]
                source_evidence.append({"route_id": route_id, "route_name": match["route_name"],
                                        "origin_source_sequence": sequence[i] if i < len(sequence) else None,
                                        "destination_source_sequence": sequence[i + 1] if i + 1 < len(sequence) else None,
                                        "verified": match["verified"]})
        edges.append({"origin_stop_id": origin, "destination_stop_id": destination,
                      "source_order_evidence": source_evidence,
                      "travel_time_s": None, "distance_m": None,
                      "turn_permission": None, "turnaround_feasibility": None, "return_feasibility": None,
                      "status": "source_adjacency_only_operational_attributes_unknown"})
    verified = {rid: bool(by_route[rid]) and all(m["verified"] for m in by_route[rid])
                for rid in candidate["route_ids"]}
    return {"stop_order": stop_ids, "route_evidence": dict(by_route), "route_evidence_verified": verified,
            "adjacent_pairs": edges, "actual_operating_direction_confirmed": False}, selected_groups


def validate_selection(candidate, path):
    """Reject incomplete selection evidence before empty all() checks can pass."""
    def require(condition, field):
        if not condition:
            error(path, 0, field)

    def identifiers(values, minimum):
        return (isinstance(values, list) and len(values) >= minimum
                and all(isinstance(value, str) and value.strip() for value in values))

    routes = candidate.get("route_ids")
    require(identifiers(routes, 1), "route_ids")
    require(len(routes) == len(set(routes)), "route_ids")
    common = candidate.get("all_routes_shared_source_run_through_hub", {})
    require(isinstance(common, dict), "common_source_run")
    stops = common.get("stop_ids")
    require(identifiers(stops, 2), "stop_ids")
    evidence = common.get("evidence")
    require(isinstance(evidence, list) and bool(evidence), "evidence")
    seen = set()
    for row in evidence:
        require(isinstance(row, dict), "evidence")
        rid = row.get("route_id")
        require(isinstance(rid, str) and rid in routes and rid not in seen, "evidence.route_id")
        seen.add(rid)
        matches = row.get("matches")
        require(isinstance(matches, list) and bool(matches), "matches")
        for match in matches:
            require(isinstance(match, dict), "matches")
            require(isinstance(match.get("route_name"), str) and bool(match["route_name"].strip()), "route_name")
            sequences = match.get("sequences")
            require(isinstance(sequences, list) and len(sequences) == len(stops)
                    and all(type(i) is int and i >= 1 for i in sequences), "sequences")
    require(seen == set(routes), "evidence.route_id")


def stop_candidates(source_path, wanted, visit_ids, common_ids):
    names = defaultdict(set)
    coords = defaultdict(set)
    admins = defaultdict(set)
    combinations = defaultdict(set)
    counts, invalid_coords = Counter(), Counter()
    fields = ("BSST_ID", "BSST_NM", "lat", "lon", *ADMIN_FIELDS, "STDR_DE")
    for _, row in csv_rows(source_path, fields):
        sid = row["BSST_ID"]
        if sid not in wanted:
            continue
        counts[sid] += 1
        if row["BSST_NM"].strip():
            names[sid].add(row["BSST_NM"])
        admins[sid].add(tuple(row[f] for f in ADMIN_FIELDS))
        combinations[sid].add(tuple(row[f] for f in fields[1:]))
        if not row["lat"].strip() and not row["lon"].strip():
            continue
        try:
            lat, lon = float(row["lat"]), float(row["lon"])
            if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError
        except ValueError:
            invalid_coords[sid] += 1
        else:
            coords[sid].add((lat, lon))
    labels = {sid: {"names": names[sid], "coordinates": coords[sid]} for sid in counts}
    return [{"stop_id": sid, "in_common_corridor": sid in common_ids, "in_candidate_visit_rows": sid in visit_ids,
             "metadata_source_rows": counts[sid], **stop_display(sid, labels),
             "administrative_candidates": [dict(zip(ADMIN_FIELDS, value)) for value in sorted(admins[sid])],
             "administrative_status": "matched" if len(admins[sid]) == 1 else "ambiguous" if admins[sid] else "missing",
             "source_metadata_candidates": [dict(zip(fields[1:], value)) for value in sorted(combinations[sid])],
             "invalid_coordinate_source_rows": invalid_coords[sid]}
            for sid in sorted(wanted)]


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return {"path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": h.hexdigest()}


def dump_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def csv_writer(stack, path, fields):
    stream = stack.enter_context(path.open("w", encoding="utf-8-sig", newline=""))
    writer = csv.DictWriter(stream, fields)
    writer.writeheader()
    return writer


def validate_saved_od(output_dir, report, corridor, route_stop_ids):
    """Re-read output files and compare exact row multisets and measured totals.

    Recompute classification from endpoint IDs, source-order evidence and route
    stop references, rather than trusting the classification columns. Retain
    only source-line counts and forward-row signatures, not the full CSV.
    """
    output_dir = Path(output_dir)
    expected_forward, actual_forward = Counter(), Counter()
    source_lines = {"morning": Counter(), "forward": Counter()}
    totals = {key: {"rows": 0, "passengers": 0} for key in ("morning", "forward", "expected_forward", "separated")}
    endpoints = {key: {"rows": 0, "passengers": 0} for key in ENDPOINT_CLASSES}
    orders = {key: {"rows": 0, "passengers": 0} for key in ORDER_CLASSES}
    invalid_classifications = Counter()

    def measured_rows(filename):
        path = output_dir / filename
        for line, row in csv_rows(path, OD_FIELDS):
            for field in ("PSNG_NUM", "source_csv_line"):
                value = row[field]
                if not value or not value.isascii() or not value.isdecimal():
                    error(path, line, field)
                try:
                    int(value)
                except ValueError:
                    error(path, line, field)
            if int(row["source_csv_line"]) < 2:
                error(path, line, "source_csv_line")
            yield row, int(row["PSNG_NUM"])

    def classifications(row):
        rid, origin, destination = (row[f] for f in ("ROUTE_ID", "RIDE_BSST_ID", "ALGH_BSST_ID"))
        boundary = endpoint_class(origin, destination, corridor["stop_order"])
        reference = origin in route_stop_ids.get(rid, set()) and destination in route_stop_ids.get(rid, set())
        direction = "not_internal"
        if boundary == "both_inside":
            direction = internal_order(origin, destination, corridor["stop_order"])
            if direction != "same_stop" and (not reference or not corridor["route_evidence_verified"].get(rid, False)):
                direction = "undetermined"
        labels_valid = (row["endpoint_class"] == boundary and row["internal_source_order"] == direction
                        and row["route_stop_reference"] == ("matched" if reference else "missing")
                        and rid in report["route_ids"] and row["RIDE_DTM_H"] in {"07", "08"})
        return boundary, direction, labels_valid

    def add(target, passengers):
        target["rows"] += 1
        target["passengers"] += passengers

    for row, passengers in measured_rows("morning_od.csv"):
        add(totals["morning"], passengers)
        source_lines["morning"][int(row["source_csv_line"])] += 1
        boundary, direction, valid = classifications(row)
        invalid_classifications["morning"] += not valid
        add(endpoints[boundary], passengers)
        if boundary == "both_inside":
            add(orders[direction], passengers)
        if direction == "forward" and row["ROUTE_ID"] in report["route_ids"] and row["RIDE_DTM_H"] in {"07", "08"}:
            add(totals["expected_forward"], passengers)
            expected_forward[tuple(row[f] for f in OD_FIELDS)] += 1
        else:
            add(totals["separated"], passengers)
    for row, passengers in measured_rows("internal_forward_od.csv"):
        add(totals["forward"], passengers)
        source_lines["forward"][int(row["source_csv_line"])] += 1
        boundary, direction, valid = classifications(row)
        invalid_classifications["forward"] += not (valid and boundary == "both_inside" and direction == "forward")
        actual_forward[tuple(row[f] for f in OD_FIELDS)] += 1

    checks = {
        "morning_totals_match_report": totals["morning"] == report["total_morning"],
        "forward_totals_match_report": totals["forward"] == report["included_forward_internal"],
        "expected_forward_totals_match_report": totals["expected_forward"] == report["included_forward_internal"],
        "separated_totals_match_report": totals["separated"] == report["separated_from_forward_internal"],
        "endpoint_totals_match_report": endpoints == report["endpoint_classes"],
        "internal_order_totals_match_report": orders == report["internal_source_order_classes"],
        "morning_classifications_valid": invalid_classifications["morning"] == 0,
        "forward_rows_satisfy_conditions": invalid_classifications["forward"] == 0,
        "forward_multiset_matches_full_subset": actual_forward == expected_forward,
        "morning_source_row_ids_unique": all(n == 1 for n in source_lines["morning"].values()),
        "forward_source_row_ids_unique": all(n == 1 for n in source_lines["forward"].values()),
    }
    return {"measured_totals": totals, "recomputed_endpoint_classes": endpoints,
            "recomputed_internal_order_classes": orders,
            "missing_forward_rows": sum((expected_forward - actual_forward).values()),
            "unexpected_forward_rows": sum((actual_forward - expected_forward).values()),
            "duplicate_source_rows": {key: sum(n - 1 for n in counts.values()) for key, counts in source_lines.items()},
            "invalid_classification_rows": {key: invalid_classifications[key] for key in ("morning", "forward")},
            "checks": checks, "all_checks_passed": all(checks.values())}


def extract(input_dir, survey_dir, output_dir, candidate_name="southwest"):
    started = time.perf_counter()
    input_dir, survey_dir, output_dir = (Path(p).resolve() for p in (input_dir, survey_dir, output_dir))
    if (output_dir.exists() or output_dir == input_dir or input_dir in output_dir.parents
            or output_dir == survey_dir or survey_dir in output_dir.parents):
        raise ValueError("new output directory required outside source directories")
    source_files = [survey_dir / "survey.json", survey_dir / "report.md",
                    input_dir / "route_stops.csv", input_dir / "bus_od_0709.csv"]
    # Read the narrative source as well as machine-readable selection evidence.
    (survey_dir / "report.md").read_text(encoding="utf-8")
    survey = json.loads((survey_dir / "survey.json").read_text(encoding="utf-8"))
    candidate = next(c for c in survey["candidates"] if c["candidate"] == candidate_name)
    validate_selection(candidate, survey_dir / "survey.json")
    route_ids = set(candidate["route_ids"])
    source_rows = []
    for line, row in visit_source_rows(input_dir / "route_stops.csv", VISIT_FIELDS):
        if row["ROUTE_ID"] in route_ids:
            source_rows.append({"source_csv_line": line, **{f: row[f] for f in VISIT_FIELDS}})
    groups = visit_groups(source_rows)
    corridor, selected_groups = make_corridor(groups, candidate)
    group_reports = [{"route_id": rid, "source_route_name": name,
                      "group_role": "selected_source_group" if (rid, name) in selected_groups else "other_source_group_preserved",
                      **group_quality(rows)} for (rid, name), rows in sorted(groups.items())]
    route_stop_ids = defaultdict(set)
    for row in source_rows:
        route_stop_ids[row["ROUTE_ID"]].add(row["BSST_ID"])
    visit_ids = set().union(*route_stop_ids.values()) if route_stop_ids else set()
    wanted = visit_ids | set(corridor["stop_order"])
    endpoint_counts = {key: {"rows": 0, "passengers": 0} for key in ENDPOINT_CLASSES}
    order_counts = {key: {"rows": 0, "passengers": 0} for key in ORDER_CLASSES}
    total_rows = total_passengers = 0
    by_route = {rid: {"rows": 0, "passengers": 0} for rid in sorted(route_ids)}
    refs = Counter()
    output_dir.mkdir(parents=True)
    with ExitStack() as stack:
        full_writer = csv_writer(stack, output_dir / "morning_od.csv", OD_FIELDS)
        forward_writer = csv_writer(stack, output_dir / "internal_forward_od.csv", OD_FIELDS)
        visits_writer = csv_writer(stack, output_dir / "route_visits.csv",
                                   ("source_csv_line", *VISIT_FIELDS, "group_role", "same_sequence_stop_source_rows"))
        multiplicities = Counter((r["ROUTE_ID"], r["ROUTE_NM"], r["BSST_ORDER"], r["BSST_ID"]) for r in source_rows)
        # Keep every source visit row, including duplicates and original order.
        for row in source_rows:
            key = (row["ROUTE_ID"], row["ROUTE_NM"])
            visits_writer.writerow({**row, "group_role": "selected_source_group" if key in selected_groups else "other_source_group_preserved",
                                    "same_sequence_stop_source_rows": multiplicities[(*key, row["BSST_ORDER"], row["BSST_ID"]) ]})
        for line, (rid, origin, destination, hour), passengers in od_rows(input_dir / "bus_od_0709.csv"):
            if hour not in {"07", "08"}:
                raise ValueError(f"bus_od_0709.csv:{line}:RIDE_DTM_H")
            if rid not in route_ids:
                continue
            total_rows += 1
            total_passengers += passengers
            by_route[rid]["rows"] += 1
            by_route[rid]["passengers"] += passengers
            wanted.update((origin, destination))
            boundary = endpoint_class(origin, destination, corridor["stop_order"])
            endpoint_counts[boundary]["rows"] += 1
            endpoint_counts[boundary]["passengers"] += passengers
            reference_ok = origin in route_stop_ids[rid] and destination in route_stop_ids[rid]
            if not reference_ok:
                refs["od_route_stop_failure_rows"] += 1
                refs["od_route_stop_failure_passengers"] += passengers
            direction = "not_internal"
            if boundary == "both_inside":
                direction = internal_order(origin, destination, corridor["stop_order"])
                if direction != "same_stop" and (not corridor["route_evidence_verified"].get(rid, False) or not reference_ok):
                    direction = "undetermined"
                order_counts[direction]["rows"] += 1
                order_counts[direction]["passengers"] += passengers
            record = dict(zip(OD_FIELDS, (line, rid, origin, destination, hour, passengers,
                                         boundary, direction, "matched" if reference_ok else "missing")))
            full_writer.writerow(record)
            if direction == "forward":
                forward_writer.writerow(record)
    stops = stop_candidates(input_dir / "route_stops.csv", wanted, visit_ids, set(corridor["stop_order"]))
    expected = candidate["bundle_morning_passengers"]
    included = order_counts["forward"]
    report = {
        "schema_version": "guro-real-input-extraction-draft-v1", "status": "development_only_not_operational_input",
        "candidate": candidate_name, "survey_recommendation": survey["recommendation"],
        "sources": [digest(path) for path in source_files],
        "observation_date_from_survey": survey["observation_date"],
        "source_validity_assumption": survey["validity_assumption"],
        "route_ids": candidate["route_ids"], "route_totals": by_route,
        "visit_source_rows": len(source_rows), "visit_stop_ids": len(visit_ids), "stop_metadata_ids": len(stops),
        "selected_source_groups": [{"route_id": rid, "source_route_name": name} for rid, name in sorted(selected_groups)],
        "other_source_groups_preserved": sum(r["group_role"] == "other_source_group_preserved" for r in group_reports),
        "common_stop_order": corridor["stop_order"], "adjacent_pair_count": len(corridor["adjacent_pairs"]),
        "total_morning": {"rows": total_rows, "passengers": total_passengers},
        "endpoint_classes": endpoint_counts, "internal_source_order_classes": order_counts,
        "included_forward_internal": included,
        "separated_from_forward_internal": {"rows": total_rows - included["rows"], "passengers": total_passengers - included["passengers"]},
        "reference_failures": {"od_route_stop_failure_rows": refs["od_route_stop_failure_rows"],
                               "od_route_stop_failure_passengers": refs["od_route_stop_failure_passengers"],
                               "stop_metadata_missing_ids": [s["stop_id"] for s in stops if not s["metadata_source_rows"]],
                               "selected_groups_missing": [{"route_id": rid, "source_route_name": name} for rid, name in sorted(selected_groups - set(groups))]},
        "display_quality": {"ambiguous_name_ids": sum(s["stop_name_status"] == "ambiguous" for s in stops),
                            "ambiguous_coordinate_ids": sum(s["coordinate_status"] == "ambiguous" for s in stops),
                            "invalid_coordinate_source_rows": sum(s["invalid_coordinate_source_rows"] for s in stops)},
        "checks": {
            "endpoint_rows_preserved": sum(v["rows"] for v in endpoint_counts.values()) == total_rows,
            "endpoint_passengers_preserved": sum(v["passengers"] for v in endpoint_counts.values()) == total_passengers,
            "internal_rows_preserved": sum(v["rows"] for v in order_counts.values()) == endpoint_counts["both_inside"]["rows"],
            "internal_passengers_preserved": sum(v["passengers"] for v in order_counts.values()) == endpoint_counts["both_inside"]["passengers"],
            "candidate_total_equal_survey": total_passengers == expected,
            "candidate_routes_present": all(route_stop_ids[rid] for rid in route_ids),
            "source_corridor_evidence_verified": all(corridor["route_evidence_verified"].values()),
            "od_route_stop_references": refs["od_route_stop_failure_rows"] == 0,
            "all_stop_metadata_present": all(s["metadata_source_rows"] > 0 for s in stops),
            "selected_groups_present": selected_groups <= set(groups),
        },
        "candidate_passengers_from_survey": expected,
        "arrival_s_generated": False, "actual_direction_confirmed": False,
        "interpretation_limits": [
            "역방향 인원은 선택한 공통 정류장 ID 집합 내부의 분류 결과다. 반대 방향 정류장 ID로 구성한 구간은 미추출이다.",
            "정방향 내부 인원은 후보 노선 전체 오전 인원의 개발용 부분집합이며 전체 영향 수요가 아니다.",
            "관측 기준일과 다음 날짜 기록의 서비스일 귀속은 별개이며 다음 날짜 귀속은 미확인이다.",
            "공유 정리본 검증이며 원본부터의 독립 재집계가 아니다. B·C에 바로 넣는 최종 실행 입력이 아니다.",
        ],
        "missing_execution_inputs": ["방향·시간대별 구간 주행시간 및 근거",
                                     "허용 회전·회차·공차 복귀 검증", "차량·가용시간·운영 창·정차 정책·운행편 시간표",
                                     "승객 발생 시각 생성 정책 및 고정 발생 입력", "방향·버전·정상 교통 연계 계약"],
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    report["saved_csv_validation"] = validate_saved_od(output_dir, report, corridor, route_stop_ids)
    report["checks"]["included_separated_preserved"] = report["saved_csv_validation"]["all_checks_passed"]
    report["all_checks_passed"] = all(report["checks"].values())
    dump_json(output_dir / "stops.json", {"coordinate_basis": "공유 route_stops lat/lon. CRS·위치 정확성 독립 검증 아님.", "stops": stops})
    dump_json(output_dir / "route_groups.json", group_reports)
    dump_json(output_dir / "corridor.json", corridor)
    dump_json(output_dir / "validation_report.json", report)
    with ExitStack() as stack:
        for filename, counts in (("endpoint_classes.csv", endpoint_counts), ("internal_order_classes.csv", order_counts)):
            writer = csv_writer(stack, output_dir / filename, ("class", "rows", "passengers"))
            writer.writerows({"class": key, **value} for key, value in counts.items())
    text = ["# 구로디지털단지역 묶음 실제 입력 추출 초안", "", "공식 지역·파업 노선 확정이 아닌 개발용 추출이다. 실행 가능한 시뮬레이션 입력이 아니다.",
            "원본 ZIP은 읽지 않았으며 공유 정리본·후보 보고서를 재사용했다. 입력 파일 경로·SHA-256은 validation_report.json에 기록했다.",
            "", "## 추출 조건", "", "노선: " + ", ".join(candidate["route_ids"]),
            "공통 원천 순서: " + " → ".join(corridor["stop_order"]),
            "관측일: " + survey["observation_date"], survey["validity_assumption"],
            "전체 후보 노선의 07·08시 승차 태그 OD를 보존한다. 시간대는 정류장 도착 시각이 아니며 arrival_s를 만들지 않았다.",
            "인원은 PSNG_NUM 합계이며 고유 승객·여정 수가 아니다. 같은 정류장 ID끼리의 OD도 보존한다.",
            "공통 구간 밖 수요도 전체 OD에 남기며 외부 정류장·반대 방향을 다른 ID로 바꾸지 않는다.",
            *report["interpretation_limits"],
            "", "## 파일", "", "- stops.json: 정류장 ID별 한 객체. 이름·좌표·행정구역 후보와 원천 값 조합을 보존한다. 모호하면 대표값을 확정하지 않는다.",
            "- route_visits.csv: 5개 노선의 모든 원천 방문 행. source_csv_line, 일련번호·원천 노선명·순번·회사·누적거리·기준일을 보존한다. 다른 그룹도 별도 표시한다.",
            "- route_groups.json: 그룹별 방문·중복·반복 수. 중복을 공동운행으로 단정하지 않는다.",
            "- morning_od.csv: 후보 노선의 전체 오전 OD, 원래 노선·시간대와 배타적 분류. OD에는 노선명 그룹이 없으므로 행별 실제 방문 그룹 배정을 주장하지 않는다.",
            "- corridor.json: 인접 정류장 쌍과 노선명·원천 순번 근거. 주행시간·거리·회전·회차·복귀는 null(미확인)이며 직선 거리·0초로 채우지 않는다.",
            "- endpoint_classes.csv: 양끝 안 / 승차만 안 / 하차만 안 / 양끝 밖의 행 수·인원.",
            "- internal_order_classes.csv: 양끝 안 수요의 정방향·역방향·동일 정류장·판정 불가.",
            "- internal_forward_od.csv: 공통 원천 순서상 정방향인 내부 OD만 분리. 원래 노선·시간대를 유지한다.",
            "- validation_report.json: 포함·분리 인원, 참조·근거·합계 검사와 부족 입력.",
            "", "## 분류와 검증", "", "끝점 분류는 정확한 정류장 ID의 집합 포함 여부로 배타적으로 결정한다. 같은 장소처럼 보여도 ID를 합치지 않는다.",
            "내부 정·역방향은 공통 목록의 위치 비교이며 실제 운행 방향이 아니다. 목록에서 위치가 여러 개이거나 해당 노선의 원천 순번 근거를 검증하지 못하면 판정 불가로 분리한다.",
            "원천 방문 행·순번·반복·중복은 유지한다. 동일 근거 중복으로 OD 행이나 인원을 증식시키지 않는다. 기존 C의 제약에 맞춘 변경도 하지 않았다.",
            "저장한 두 OD CSV를 다시 읽어 실제 행 수·인원·분류와 보고서를 대조했다. 정방향 파일은 전체 파일에서 조건을 다시 계산한 부분집합과 모든 필드·중복 횟수까지 비교한다.",
            "```json", json.dumps({"total": report["total_morning"], "endpoint": endpoint_counts, "internal": order_counts,
                                     "included": included, "separated": report["separated_from_forward_internal"],
                                     "checks": report["checks"]}, ensure_ascii=False, indent=2), "```",
            "", "## 실행 부족 항목", ""]
    text.extend("- " + value for value in report["missing_execution_inputs"])
    text.extend(["", "팀원의 추출·집계·좌표 변환과 기존 후보 조사를 근거로 한 부분 추출이다. 원본 OD 독립 재생성이나 실제 수송 검증이 아니다.",
                 "실제 결과는 Git 제외 경로에만 보관한다. 처리 코드 실행법은 A.Part/data/docs/guro_input_extraction.md를 참고한다."])
    (output_dir / "README.md").write_text("\n".join(text) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=Path("raw/from_teammate"))
    parser.add_argument("--survey-dir", type=Path, default=Path("data/processed/taims_candidate_survey_final"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/processed/guro_real_input_draft"))
    args = parser.parse_args()
    try:
        report = extract(args.input_dir, args.survey_dir, args.output_dir)
    except (OSError, ValueError, KeyError, TypeError, StopIteration, csv.Error) as exc:
        parser.exit(2, f"{exc}\n" if isinstance(exc, AnalysisError) else f"extraction failed: {type(exc).__name__}\n")
    print(json.dumps({"output_dir": str(args.output_dir), "total": report["total_morning"],
                      "internal_forward": report["included_forward_internal"], "all_checks_passed": report["all_checks_passed"]}))
    if not report["all_checks_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
