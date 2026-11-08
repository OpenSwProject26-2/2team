"""Exploratory route bundles from shared morning OD and stop-order tables."""

import argparse
from collections import Counter, defaultdict
import csv
from datetime import date
from itertools import combinations
import json
from pathlib import Path
import time

from data.demand_analysis import AnalysisError, csv_rows, error, od_rows, read_labels, route_display, stop_display


# Exact IDs from the existing morning boarding/alighting top 20. No place merge.
SEEDS = (("north", "107000005", "미아리고개 상위 승차 허브"),
         ("southwest", "120000001", "구로디지털단지역 상위 하차 허브"),
         ("east", "122000156", "선릉역 상위 승차·OD 참고 허브"))


def visit_source_rows(path, required):
    for line, row in csv_rows(path, required):
        for field in ("ROUTE_ID", "ROUTE_NM", "BSST_ID"):
            if not row[field].strip():
                error(path, line, field)
        sequence = row["BSST_ORDER"]
        if not sequence or not sequence.isascii() or not sequence.isdecimal():
            error(path, line, "BSST_ORDER")
        try:
            positive = int(sequence) >= 1
        except ValueError:
            error(path, line, "BSST_ORDER")
        if not positive:
            error(path, line, "BSST_ORDER")
        yield line, row


def visit_groups(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row["ROUTE_ID"], row["ROUTE_NM"]].append(row)
    return dict(groups)


def slots(rows):
    result = defaultdict(set)
    for row in rows:
        result[int(row["BSST_ORDER"])].add(row["BSST_ID"])
    return dict(result)


def ordered_runs(rows):
    """Collapse identical visit evidence only; split at gaps/conflicting slots."""
    result, run, previous = [], [], None
    for sequence, stops in sorted(slots(rows).items()):
        if len(stops) != 1 or (previous is not None and sequence != previous + 1):
            if run:
                result.append(run)
            run = []
        if len(stops) == 1:
            run.append((sequence, next(iter(stops))))
        previous = sequence
    if run:
        result.append(run)
    return result


def group_quality(rows):
    positions = slots(rows)
    multiplicity = Counter((int(r["BSST_ORDER"]), r["BSST_ID"]) for r in rows)
    stop_positions = defaultdict(set)
    for sequence, stops in positions.items():
        for stop in stops:
            stop_positions[stop].add(sequence)
    order_counts = Counter(int(r["BSST_ORDER"]) for r in rows)
    return {
        "source_rows": len(rows), "unique_sequence_stop_visits": len(multiplicity),
        "sequence_positions": len(positions),
        "duplicate_sequence_groups": sum(n > 1 for n in order_counts.values()),
        "duplicate_sequence_excess_rows": sum(n - 1 for n in order_counts.values()),
        "conflicting_stop_sequence_groups": sum(len(s) > 1 for s in positions.values()),
        "same_sequence_stop_excess_rows": sum(n - 1 for n in multiplicity.values()),
        "repeated_stop_ids_at_different_sequences": sum(len(s) > 1 for s in stop_positions.values()),
        "repeated_visit_excess": sum(len(s) - 1 for s in stop_positions.values()),
        "sequence_min": min(positions), "sequence_max": max(positions),
    }


def forward_pairs(groups, route_id, origin, destination):
    """Count possible ordered visits, not established direction or accuracy."""
    count = 0
    for (rid, _), rows in groups.items():
        if rid != route_id:
            continue
        positions = slots(rows)
        starts = [i for i, stops in positions.items() if origin in stops]
        ends = [i for i, stops in positions.items() if destination in stops]
        count += sum(i < j for i in starts for j in ends)
    return count


def position_index(groups):
    result = defaultdict(list)
    for (rid, name), rows in groups.items():
        by_stop = defaultdict(list)
        for sequence, stops in slots(rows).items():
            for stop in stops:
                by_stop[stop].append(sequence)
        result[rid].append((name, dict(by_stop)))
    return result


def indexed_pairs(index, rid, origin, destination):
    return sum(i < j for _, positions in index.get(rid, [])
               for i in positions.get(origin, []) for j in positions.get(destination, []))


def shared_run(groups, route_ids, required_stop=None):
    """Longest contiguous ID sequence present in every route's source groups.

    Keep group and original sequence evidence; do not concatenate group variants.
    A conflicting slot or sequence gap never becomes an inferred edge.
    """
    runs = defaultdict(list)
    for (rid, name), rows in groups.items():
        if rid in route_ids:
            for run in ordered_runs(rows):
                runs[rid].append((name, run, tuple(stop for _, stop in run)))
    anchor = min(route_ids, key=lambda rid: (sum(len(r[1]) for r in runs[rid]), rid))
    candidates = set()
    for _, _, ids in runs[anchor]:
        for begin in range(len(ids)):
            for end in range(begin + 2, len(ids) + 1):
                candidate = ids[begin:end]
                if required_stop is None or required_stop in candidate:
                    candidates.add(candidate)
    for candidate in sorted(candidates, key=lambda value: (-len(value), value)):
        evidence = []
        for rid in route_ids:
            matches = []
            for name, run, ids in runs[rid]:
                for start in range(len(ids) - len(candidate) + 1):
                    if ids[start:start + len(candidate)] == candidate:
                        matches.append({"route_name": name,
                                        "sequences": [i for i, _ in run[start:start + len(candidate)]]})
            if not matches:
                break
            evidence.append({"route_id": rid, "matches": matches})
        else:
            return {"stop_ids": list(candidate), "edge_count": len(candidate) - 1, "evidence": evidence}
    return {"stop_ids": [], "edge_count": 0, "evidence": []}


def validity(rows, observation):
    counts = Counter()
    active_names = set()
    for row in rows:
        start, end = row["START_DE"], row["END_DE"]
        def valid_date(value):
            if not (len(value) == 8 and value.isascii() and value.isdecimal()):
                return False
            try:
                date(int(value[:4]), int(value[4:6]), int(value[6:8]))
            except ValueError:
                return False
            return True
        if not (valid_date(start) and valid_date(end) and start <= end):
            counts["unresolved_rows"] += 1
        elif start <= observation <= end:
            counts["active_rows"] += 1
            active_names.add(row["ROUTE_NM"])
        elif observation < start:
            counts["future_rows"] += 1
        else:
            counts["expired_rows"] += 1
    return {"source_rows": len(rows), "active_rows": counts["active_rows"],
            "expired_rows": counts["expired_rows"], "future_rows": counts["future_rows"],
            "unresolved_rows": counts["unresolved_rows"], "active_names": sorted(active_names)}


def region_summary(rows):
    labels = defaultdict(set)
    for row in rows:
        labels[row["BSST_ID"]].add(row["SIDO_NM"])
    outside = sorted(sid for sid, names in labels.items() if names - {"", "서울특별시"})
    unknown = sorted(sid for sid, names in labels.items() if "" in names)
    conflicting = sorted(sid for sid, names in labels.items() if len(names - {""}) > 1)
    return {"unique_stop_ids": len(labels), "outside_seoul_stop_ids": outside,
            "unknown_region_stop_ids": unknown, "conflicting_region_stop_ids": conflicting,
            "basis": "원천 SIDO_NM 기준. 행정경계 독립 검증·서울 내부 수요 필터 아님."}


def write_table(path, rows, fields):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v
                             for k, v in row.items()})


def survey(input_dir, analysis_dir, output_dir):
    started = time.perf_counter()
    input_dir, analysis_dir, output_dir = map(Path, (input_dir, analysis_dir, output_dir))
    if output_dir.exists() or input_dir.resolve() == output_dir.resolve() or input_dir.resolve() in output_dir.resolve().parents:
        raise ValueError("new output directory required outside input")
    summary = json.loads((analysis_dir / "summary.json").read_text(encoding="utf-8"))
    top_path = analysis_dir / "morning_od_top20.csv"
    top_od = []
    for line, row in csv_rows(top_path, ("RIDE_BSST_ID", "ALGH_BSST_ID", "passenger_count")):
        value = row["passenger_count"]
        if not value or not value.isascii() or not value.isdecimal() or len(value) > 19:
            error(top_path, line, "passenger_count")
        top_od.append(row)
    known = {"od_pairs": 185292, "top20_od_passengers": 8668, "morning_passengers": 883427}
    actual = {"od_pairs": summary["group_counts"]["od"],
              "top20_od_passengers": sum(int(row["passenger_count"]) for row in top_od),
              "morning_passengers": summary["morning_passengers"]}
    baseline = {"known": known, "actual": actual, "equal": known == actual,
                "top20_summary_equal_csv": summary["top20_sums_not_full_totals"]["od"] == actual["top20_od_passengers"],
                "meaning": "노선을 합친 승하차 ID 쌍이며 여정 수 아님. 상위 OD의 길이를 가정하지 않음."}
    labels, stop_labels, _ = read_labels(input_dir)
    groups = visit_groups(row for _, row in visit_source_rows(input_dir / "route_stops.csv",
                          ("ROUTE_ID", "ROUTE_NM", "BSST_ORDER", "BSST_ID", "SIDO_NM")))
    route_stops = defaultdict(set)
    for (rid, _), rows in groups.items():
        route_stops[rid].update(row["BSST_ID"] for row in rows)
    all_versions, latest = defaultdict(list), defaultdict(list)
    for filename, target in (("routes_all_versions.csv", all_versions), ("routes_latest.csv", latest)):
        for _, row in csv_rows(input_dir / filename, ("ROUTE_ID", "ROUTE_NM", "START_DE", "END_DE")):
            target[row["ROUTE_ID"]].append(row)
    totals = Counter()
    for line, key, n in od_rows(input_dir / "bus_od_0709.csv"):
        if key[3] not in {"07", "08"}:
            error(input_dir / "bus_od_0709.csv", line, "RIDE_DTM_H")
        totals[key[0]] += n
    baseline["morning_file_passengers"] = sum(totals.values())
    baseline["morning_summary_equal_input"] = sum(totals.values()) == actual["morning_passengers"]
    baseline["mismatch_note"] = None if (baseline["equal"] and baseline["top20_summary_equal_csv"]
                                         and baseline["morning_summary_equal_input"]) else (
        "기존 보고서·상위 OD CSV·현재 오전 파일 값 사이 불일치. 입력/보고서 버전 또는 갱신 여부를 확인해야 하며 원본 오류로 단정하지 않음.")
    index = position_index(groups)
    candidates, pairs, quality_rows, visit_rows = [], [], [], []
    observation = summary["observation_date_from_readme"].replace("-", "")
    for name, hub, reason in SEEDS:
        nearby = sorted((rid for rid, stops in route_stops.items() if hub in stops and totals[rid] > 0),
                        key=lambda rid: (-totals[rid], rid))
        chosen = nearby[:5]
        if len(chosen) < 3:
            continue
        common_stops = sorted(set.intersection(*(route_stops[rid] for rid in chosen)))
        run = shared_run(groups, chosen, hub)
        candidate = {"candidate": name, "seed_reason": reason, "hub_id": hub,
                     "hub": stop_display(hub, stop_labels), "route_ids": chosen,
                     "route_totals": [{"route_id": rid, "morning_passengers": totals[rid], **route_display(rid, labels)} for rid in chosen],
                     "bundle_morning_passengers": sum(totals[rid] for rid in chosen),
                     "selection": "상위 허브의 정확한 ID를 방문하는 노선 중 오전 전체 인원 상위 최대 5개. 공식 범위 아님.",
                     "all_routes_shared_stop_ids": common_stops,
                     "all_routes_shared_stop_count": len(common_stops),
                     "all_routes_shared_source_run_through_hub": run,
                     "direction_status": "같은 정류장 ID의 원천 순서상 동일 방향 증거만 확인. 실제 방향·버전·운행 가능한 연결은 미확정.",
                     "validity": {}, "groups": [], "order_candidates": {key: {"rows": 0, "passengers": 0} for key in ("zero", "one", "multiple")},
                     "same_stop_passengers": 0, "hub_endpoint_passengers": 0, "shared_run_endpoint_passengers": 0,
                     "endpoint_scope": "선택 노선 중 승차 또는 하차 ID가 정확한 허브 ID / 공통 원천순서 구간 ID 집합에 속하는 기록. 구간을 실제 통과한 전체 수요나 지역 전체 수요가 아님.",
                     "reference_top20_od": [],
                     "missing_inputs": ["방향·시간대별 인접 구간 주행시간 관측/출처", "허용 회전·회차·공차 복귀 근거", "정상 운행편 시간표 및 유효 노선·방향 매핑"],
                     "integration_limit": "현재 C의 재방문·동일 거점 제약은 연결상의 한계. 실제 방문·출발지·방향을 변경하지 않음."}
        source_rows = []
        for rid in chosen:
            candidate["validity"][rid] = {"all_versions": validity(all_versions[rid], observation),
                                          "latest_reference_only": validity(latest[rid], observation)}
            for (route_id, route_name), rows in groups.items():
                if route_id != rid:
                    continue
                source_rows.extend(rows)
                quality = {"candidate": name, "route_id": rid, "route_name": route_name, **group_quality(rows),
                           "name_matches_active_version": route_name in candidate["validity"][rid]["all_versions"]["active_names"]}
                candidate["groups"].append(quality)
                quality_rows.append(quality)
                by_visit = Counter((int(r["BSST_ORDER"]), r["BSST_ID"]) for r in rows)
                for (sequence, stop), n in sorted(by_visit.items()):
                    visit_rows.append({"candidate": name, "route_id": rid, "route_name": route_name,
                                       "source_sequence": sequence, "stop_id": stop, "source_row_count": n,
                                       "stop_name_candidates": sorted(stop_labels.get(stop, {}).get("names", []))})
        candidate["regions"] = region_summary(source_rows)
        candidate["shared_run_regions"] = region_summary(
            [row for row in source_rows if row["BSST_ID"] in run["stop_ids"]])
        candidate["shared_run_stop_details"] = [
            {"stop_id": sid, **stop_display(sid, stop_labels)} for sid in run["stop_ids"]]
        for left, right in combinations(chosen, 2):
            evidence = shared_run(groups, [left, right], hub)
            pairs.append({"candidate": name, "route_a": left, "route_b": right,
                          "shared_stop_count": len(route_stops[left] & route_stops[right]),
                          "longest_source_run_through_hub_edges": evidence["edge_count"],
                          "run_stop_ids": evidence["stop_ids"], "sequence_evidence": evidence["evidence"],
                          "actual_direction_confirmed": False})
        candidates.append(candidate)
    by_route = defaultdict(list)
    for candidate in candidates:
        for rid in candidate["route_ids"]:
            by_route[rid].append(candidate)
    for _, (rid, origin, destination, _), n in od_rows(input_dir / "bus_od_0709.csv"):
        if rid not in by_route:
            continue
        count = indexed_pairs(index, rid, origin, destination)
        bucket = "zero" if count == 0 else "one" if count == 1 else "multiple"
        for candidate in by_route[rid]:
            candidate["order_candidates"][bucket]["rows"] += 1
            candidate["order_candidates"][bucket]["passengers"] += n
            candidate["same_stop_passengers"] += n if origin == destination else 0
            candidate["hub_endpoint_passengers"] += n if candidate["hub_id"] in (origin, destination) else 0
            run_ids = candidate["all_routes_shared_source_run_through_hub"]["stop_ids"]
            candidate["shared_run_endpoint_passengers"] += n if origin in run_ids or destination in run_ids else 0
            for reference in top_od:
                if reference["RIDE_BSST_ID"] == origin and reference["ALGH_BSST_ID"] == destination:
                    # Count contributions, not the cross-route global top-OD total.
                    key = (origin, destination)
                    existing = next((r for r in candidate["reference_top20_od"] if (r["origin"], r["destination"]) == key), None)
                    if existing is None:
                        existing = {"origin": origin, "destination": destination,
                                    "global_morning_passengers": int(reference["passenger_count"]), "bundle_passengers": 0}
                        candidate["reference_top20_od"].append(existing)
                    existing["bundle_passengers"] += n
    for candidate in candidates:
        candidate["order_candidate_totals_equal_bundle"] = sum(r["passengers"] for r in candidate["order_candidates"].values()) == candidate["bundle_morning_passengers"]
        candidate["unresolved_order_candidate_passenger_share"] = (
            candidate["order_candidates"]["zero"]["passengers"] + candidate["order_candidates"]["multiple"]["passengers"]
        ) / candidate["bundle_morning_passengers"]
    # Transparent exploratory comparison: three axes, equal rank weight.
    criteria = ((lambda c: -c["bundle_morning_passengers"]),
                (lambda c: -c["all_routes_shared_source_run_through_hub"]["edge_count"]),
                (lambda c: c["unresolved_order_candidate_passenger_share"]))
    scores = Counter()
    for criterion in criteria:
        for candidate in candidates:
            scores[candidate["candidate"]] += 1 + sum(criterion(other) < criterion(candidate) for other in candidates)
    recommended = min(candidates, key=lambda c: (scores[c["candidate"]], c["unresolved_order_candidate_passenger_share"], c["candidate"])) if candidates else None
    result = {"status": "exploratory_candidate_survey_not_official_scope", "baseline_check": baseline,
              "observation_date": observation,
              "validity_assumption": f"START_DE <= {observation} <= END_DE, 양 끝 포함·99991231은 비교상 미래 종료일. 공식 경계 의미 미확정. latest는 참고만 사용.",
              "direction_caveat": "순서 후보 1개는 실제 방향 확정이나 방향 판별 정확도가 아님. 중복은 공동운행으로 단정하지 않음.",
              "overlap_warning": "후보 사이 중복 노선·수요가 있으므로 묶음 인원을 합산해 전체 효과로 해석하지 않음.",
              "bundle_overlaps": [{"a": a["candidate"], "b": b["candidate"], "route_ids": sorted(set(a["route_ids"]) & set(b["route_ids"]))} for a, b in combinations(candidates, 2)],
              "candidates": candidates, "pairwise": pairs,
              "recommendation": recommended["candidate"] if recommended else None,
              "recommendation_method": "묶음 오전 수요(내림), 허브 통과 공통 원천순서 구간 길이(내림), 0개/여러 개 순서후보 인원 비중(오름)의 동일 가중 순위합. 동률은 후보 모호성 비중. 탐색적 기준이며 실제 방향·현장 성능이 아님.",
              "recommendation_rank_sums": dict(scores),
              "recommendation_notes": {
                  "bundle_morning_passengers": recommended["bundle_morning_passengers"],
                  "shared_source_edges_through_hub": recommended["all_routes_shared_source_run_through_hub"]["edge_count"],
                  "unresolved_order_candidate_share": recommended["unresolved_order_candidate_passenger_share"],
                  "group_conflicting_sequence_count": sum(g["conflicting_stop_sequence_groups"] for g in recommended["groups"]),
                  "group_repeated_stop_count": sum(g["repeated_stop_ids_at_different_sequences"] for g in recommended["groups"]),
                  "caution": "전체 노선 방문열의 서울 외·미확인 정류장과 공통 구간의 행정구역 범위는 다름. 승인된 서울 내부 수요·운행 가능 범위를 의미하지 않음.",
              } if recommended else None,
              "next_extraction": {"route_ids": recommended["route_ids"], "hub_id": recommended["hub_id"],
                                  "shared_source_run_stop_ids": recommended["all_routes_shared_source_run_through_hub"]["stop_ids"],
                                  "preserve": "전체 원천 방문열·순번·반복·그룹 후보를 보존하고, 공통 구간만으로 임의 절단하지 않음."} if recommended else None,
              "elapsed_seconds": round(time.perf_counter() - started, 3)}
    output_dir.mkdir(parents=True)
    (output_dir / "survey.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    table = [{"candidate": c["candidate"], "hub_id": c["hub_id"], "route_ids": c["route_ids"],
              "morning_passengers": c["bundle_morning_passengers"], "shared_stop_count": c["all_routes_shared_stop_count"],
              "shared_run_edges": c["all_routes_shared_source_run_through_hub"]["edge_count"],
              "order_zero_people": c["order_candidates"]["zero"]["passengers"],
              "order_one_people": c["order_candidates"]["one"]["passengers"],
              "order_multiple_people": c["order_candidates"]["multiple"]["passengers"],
              "same_stop_people": c["same_stop_passengers"], "hub_endpoint_people": c["hub_endpoint_passengers"],
              "shared_run_endpoint_people": c["shared_run_endpoint_passengers"],
              "outside_seoul_stops": len(c["regions"]["outside_seoul_stop_ids"]),
              "unknown_region_stops": len(c["regions"]["unknown_region_stop_ids"])} for c in candidates]
    for filename, rows in (("comparison.csv", table), ("pairwise.csv", pairs), ("group_quality.csv", quality_rows), ("source_visits.csv", visit_rows)):
        if rows:
            write_table(output_dir / filename, rows, list(rows[0]))
    lines = ["# 소규모 실제 입력 추출 후보 조사", "", "공식 대상 지역·파업 노선의 확정이 아니다. 인원은 PSNG_NUM 합계이며 고유 승객·여정 수가 아니다.", "",
             "기존 보고서 대사: " + json.dumps(baseline, ensure_ascii=False), "", result["validity_assumption"], result["direction_caveat"], result["overlap_warning"], "",
             "| 후보 | 노선 번호 | 묶음 오전 인원 | 모두 공유하는 ID 수 | 허브 통과 공통 연속 구간(간선) | 순서후보 0/1/여러 개 인원 |", "| --- | --- | ---: | ---: | ---: | --- |"]
    for c in candidates:
        numbers = ["/".join(r["route_number_candidates"]) for r in c["route_totals"]]
        buckets = "/".join(str(c["order_candidates"][k]["passengers"]) for k in ("zero", "one", "multiple"))
        lines.append(f"| {c['candidate']} | {', '.join(numbers)} | {c['bundle_morning_passengers']} | {c['all_routes_shared_stop_count']} | {c['all_routes_shared_source_run_through_hub']['edge_count']} | {buckets} |")
    for c in candidates:
        lines.extend(["", "## " + c["candidate"], "", "허브: " + json.dumps({"id": c["hub_id"], **c["hub"]}, ensure_ascii=False),
                      "", "노선: " + json.dumps(c["route_totals"], ensure_ascii=False),
                      "", "공유 방문: " + json.dumps(c["all_routes_shared_source_run_through_hub"], ensure_ascii=False),
                      "", "순서 후보 행·인원: " + json.dumps(c["order_candidates"], ensure_ascii=False),
                      "", f"동일 승하차 인원: {c['same_stop_passengers']}; 허브 끝점 관련: {c['hub_endpoint_passengers']}; 공유 순서 구간 끝점 관련: {c['shared_run_endpoint_passengers']}",
                      c["endpoint_scope"], "", "방문 그룹: " + json.dumps(c["groups"], ensure_ascii=False),
                      "", "유효성: " + json.dumps(c["validity"], ensure_ascii=False),
                      "", "행정구역: " + json.dumps(c["regions"], ensure_ascii=False),
                      "공통 원천순서 구간의 행정구역: " + json.dumps(c["shared_run_regions"], ensure_ascii=False),
                      "공통 구간 정류장 표시 후보: " + json.dumps(c["shared_run_stop_details"], ensure_ascii=False),
                      "", "상위 OD 참고 기여: " + json.dumps(c["reference_top20_od"], ensure_ascii=False),
                      "", f"장점: 묶음 오전 {c['bundle_morning_passengers']}명, 허브 통과 공통 원천순서 {c['all_routes_shared_source_run_through_hub']['edge_count']}개 구간을 기준으로 추출 범위를 재현할 수 있다.",
                      f"추출 모호성: 순서후보 0개/여러 개 인원 비중 {c['unresolved_order_candidate_passenger_share']:.4%}. 실제 방향 정확도가 아니다.",
                      f"어려움: 전체 방문열 서울 외 {len(c['regions']['outside_seoul_stop_ids'])}개·행정구역 미확인 {len(c['regions']['unknown_region_stop_ids'])}개 ID. 그룹별 중복·반복의 유무와 별개로 실제 방향·허용 운행은 미확정이다.",
                      "부족 자료: " + ", ".join(c["missing_inputs"]), c["integration_limit"]])
    lines.extend(["", "## 추천과 다음 추출", "", "추천: " + str(result["recommendation"]), result["recommendation_method"],
                  json.dumps(result["recommendation_rank_sums"], ensure_ascii=False), json.dumps(result["next_extraction"], ensure_ascii=False),
                  json.dumps(result["recommendation_notes"], ensure_ascii=False),
                  "", "합의·추가 자료: 방향·버전 키, 유효기간 경계, 중복 원인, 반복 방문 식별, 행정구역 공란, 주행시간·회전·회차·복귀·정상 시간표. 값을 0초로 보완하지 않는다.",
                  "소규모 집계와 방문열은 CSV, 후보별·노선쌍별 전체 증거는 survey.json을 참고한다. 공유 정리본 기반 선별이며 원본 OD 독립 재생성이 아니다."])
    (output_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=Path("raw/from_teammate"))
    parser.add_argument("--analysis-dir", type=Path, default=Path("data/processed/taims_demand"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/processed/taims_candidate_survey"))
    args = parser.parse_args()
    try:
        result = survey(args.input_dir, args.analysis_dir, args.output_dir)
    except (OSError, ValueError, KeyError, TypeError, StopIteration, csv.Error) as exc:
        parser.exit(2, f"{exc}\n" if isinstance(exc, AnalysisError) else f"survey failed: {type(exc).__name__}\n")
    print(json.dumps({"output_dir": str(args.output_dir), "baseline_equal": result["baseline_check"]["equal"],
                      "recommendation": result["recommendation"], "elapsed_seconds": result["elapsed_seconds"]}))


if __name__ == "__main__":
    main()
