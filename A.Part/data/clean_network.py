"""Draft stop/occurrence/adjacency cleaning; no demand or engine conversion."""

import argparse
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation, localcontext
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import re

from .demand_analysis import AnalysisError, csv_rows, error
from .extract_guro_draft import ADMIN_FIELDS, VISIT_FIELDS, digest, dump_json


RULE_VERSION = "source-visit-cleaning-v1"
CONFIG_VERSION = "network-cleaning-config-v1"
# A visit identity is separate from a source-row variant. Only processing serial
# and provenance annotations are excluded from variant equality.
VARIANT_FIELDS = tuple(f for f in VISIT_FIELDS if f != "SERIAL_NUM")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_id(kind, values):
    return kind + "-" + hashlib.sha256(canonical(values).encode("utf-8")).hexdigest()


def settings(document, filename="config.json"):
    def require(condition, field):
        if not condition:
            error(filename, 0, field)

    require(isinstance(document, dict), "object")
    require(document.get("schema_version") == CONFIG_VERSION, "schema_version")
    for field in ("input_dir", "output_dir", "source_namespace"):
        require(isinstance(document.get(field), str) and bool(document[field].strip()), field)
    routes = document.get("route_ids")
    require(isinstance(routes, list) and bool(routes), "route_ids")
    require(all(isinstance(r, str) and bool(r.strip()) for r in routes), "route_ids")
    require(len(set(routes)) == len(routes), "route_ids")
    groups = document.get("selected_groups")
    require(isinstance(groups, list) and bool(groups), "selected_groups")
    for group in groups:
        require(isinstance(group, dict), "selected_groups")
        require(group.get("route_id") in routes, "selected_groups.route_id")
        require(isinstance(group.get("source_route_name"), str)
                and bool(group["source_route_name"].strip()), "selected_groups.source_route_name")
    keys = [(g["route_id"], g["source_route_name"]) for g in groups]
    require(len(set(keys)) == len(keys), "selected_groups")
    require({g[0] for g in keys} == set(routes), "selected_groups.route_id")
    distance = document.get("distance", {})
    require(isinstance(distance, dict), "distance")
    require(distance.get("cumulative_unit") is None or isinstance(distance["cumulative_unit"], str), "distance.cumulative_unit")
    evidence = distance.get("unit_evidence")
    require(evidence is None or isinstance(evidence, dict), "distance.unit_evidence")
    return {"schema_version": CONFIG_VERSION, "input_dir": document["input_dir"],
            "output_dir": document["output_dir"], "source_namespace": document["source_namespace"],
            "route_ids": sorted(routes),
            "selected_groups": [{"route_id": rid, "source_route_name": name} for rid, name in sorted(keys)],
            "distance": {"cumulative_unit": distance.get("cumulative_unit"),
                         "unit_evidence": {k: evidence.get(k) for k in ("source", "field", "statement")} if evidence else None}}


def load_visits(path):
    required = ("source_csv_line", *VISIT_FIELDS)
    rows = []
    for line, row in csv_rows(path, required):
        for field in ("ROUTE_ID", "ROUTE_NM", "BSST_ID"):
            if not row[field].strip():
                error(path, line, field)
        for field, minimum in (("source_csv_line", 2), ("BSST_ORDER", 1)):
            value = row[field]
            if not value or not value.isascii() or not value.isdecimal():
                error(path, line, field)
            try:
                valid = int(value) >= minimum
            except ValueError:
                valid = False
            if not valid:
                error(path, line, field)
        rows.append({"input_csv_line": line, **{f: row[f] for f in required},
                     "prior_group_role": row.get("group_role"),
                     "declared_multiplicity": row.get("same_sequence_stop_source_rows")})
    return rows


def normalized_stop(sid, source, namespace):
    source = source or {}
    if not isinstance(source.get("stop_name_candidates", []), list):
        raise AnalysisError("stops.json:0:stop_name_candidates")
    if not isinstance(source.get("coordinate_candidates", []), list):
        raise AnalysisError("stops.json:0:coordinate_candidates")
    names = set()
    for name in [*source.get("stop_name_candidates", []), source.get("stop_name")]:
        if isinstance(name, str) and name.strip():
            names.add(name)
    coordinates, invalid = set(), 0
    candidates = list(source.get("coordinate_candidates", []))
    if source.get("lat") is not None or source.get("lon") is not None:
        candidates.append([source.get("lat"), source.get("lon")])
    for candidate in candidates:
        if (not isinstance(candidate, (list, tuple)) or len(candidate) != 2
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in candidate)
                or not (-90 <= candidate[0] <= 90 and -180 <= candidate[1] <= 180)):
            invalid += 1
        else:
            coordinates.add(tuple(candidate))
    names, coordinates = sorted(names), sorted(coordinates)
    admins = {canonical({f: a.get(f) for f in ADMIN_FIELDS}) for a in source.get("administrative_candidates", [])}
    return {"stop_id": sid, "source_namespace": namespace,
            "name": names[0] if len(names) == 1 else None, "name_candidates": names,
            "name_status": "unique" if len(names) == 1 else "ambiguous" if names else "missing",
            "lat": coordinates[0][0] if len(coordinates) == 1 else None,
            "lon": coordinates[0][1] if len(coordinates) == 1 else None,
            "coordinate_candidates": [list(v) for v in coordinates],
            "coordinate_status": "unique" if len(coordinates) == 1 else "ambiguous" if coordinates else "missing",
            "coordinate_unit": "degree", "crs": None,
            "administrative_candidates": [json.loads(a) for a in sorted(admins)],
            "invalid_coordinate_candidates": invalid, "metadata_present": bool(source)}


def cumulative(visit):
    raw = sorted({variant["fields"]["BSST_SUM_DISTANCE"] for variant in visit["source_variants"]})
    if any(not v.strip() for v in raw):
        return raw, None, "missing_cumulative"
    numbers = set()
    for value in raw:
        if not re.fullmatch(r"-?[0-9]+(?:\.[0-9]+)?", value):
            return raw, None, "invalid_cumulative"
        try:
            number = Decimal(value)
        except InvalidOperation:
            return raw, None, "invalid_cumulative"
        if not number.is_finite() or number < 0:
            return raw, None, "invalid_cumulative"
        numbers.add(number)
    if len(numbers) != 1:
        return raw, None, "ambiguous_cumulative"
    return raw, next(iter(numbers)), None


def segment_distance(origin, destination, configuration):
    a_raw, a, a_error = cumulative(origin)
    b_raw, b, b_error = cumulative(destination)
    result = {"distance_m": None, "distance_status": a_error or b_error,
              "origin_cumulative_raw": a_raw, "destination_cumulative_raw": b_raw,
              "source_delta_raw_units": None, "source_cumulative_unit": configuration["cumulative_unit"],
              "unit_evidence": configuration["unit_evidence"]}
    if origin["source_group_id"] != destination["source_group_id"]:
        result["distance_status"] = "source_group_mismatch"
        return result
    if a_error or b_error:
        return result
    dates_a = {v["fields"]["STDR_DE"] for v in origin["source_variants"]}
    dates_b = {v["fields"]["STDR_DE"] for v in destination["source_variants"]}
    valid_date = False
    if len(dates_a) == 1:
        text = next(iter(dates_a))
        if len(text) == 8 and text.isascii() and text.isdecimal():
            try:
                date(int(text[:4]), int(text[4:6]), int(text[6:]))
                valid_date = True
            except ValueError:
                pass
    if not valid_date or dates_a != dates_b:
        result["distance_status"] = "source_date_ambiguous_or_mismatch"
        return result
    with localcontext() as context:
        context.prec = max(len(a.as_tuple().digits), len(b.as_tuple().digits)) + abs(a.as_tuple().exponent - b.as_tuple().exponent) + 2
        delta = b - a
    result["source_delta_raw_units"] = str(delta)
    if delta <= 0:
        result["distance_status"] = "non_increasing_cumulative"
        return result
    evidence = configuration["unit_evidence"] or {}
    if not (configuration["cumulative_unit"] == "m" and evidence.get("field") == "BSST_SUM_DISTANCE"
            and isinstance(evidence.get("source"), str) and evidence["source"].strip()
            and isinstance(evidence.get("statement"), str) and evidence["statement"].strip()):
        result["distance_status"] = "unit_unverified"
        return result
    numeric_float = float(delta)
    if not math.isfinite(numeric_float) or numeric_float <= 0:
        result["distance_status"] = "invalid_distance_range"
        return result
    numeric = int(delta) if delta == delta.to_integral_value() else numeric_float
    result.update(distance_m=numeric, distance_status="derived_from_same_source_sequence_m")
    return result


def build_network(rows, stops_document, config, visit_checksum):
    namespace = config["source_namespace"]
    rows = [r for r in rows if r["ROUTE_ID"] in config["route_ids"]]
    selected = {(g["route_id"], g["source_route_name"]) for g in config["selected_groups"]}
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["ROUTE_ID"], row["ROUTE_NM"], int(row["BSST_ORDER"]), row["BSST_ID"]].append(row)
    visits = []
    for (rid, name, sequence, stop), source_rows in sorted(grouped.items()):
        group_id = stable_id("group", [namespace, rid, name])
        visit_id = stable_id("visit", [namespace, rid, name, sequence, stop])
        variants = defaultdict(list)
        for row in source_rows:
            variants[canonical({f: row[f] for f in VARIANT_FIELDS})].append(row)
        sources = []
        for fields, members in sorted(variants.items()):
            refs = [{"source_row_id": stable_id("row", [visit_checksum, r["input_csv_line"]]),
                     "input_csv_line": r["input_csv_line"], "upstream_csv_line_raw": r["source_csv_line"],
                     "serial_num_raw": r["SERIAL_NUM"], "prior_group_role": r["prior_group_role"],
                     "declared_multiplicity_raw": r["declared_multiplicity"]}
                    for r in sorted(members, key=lambda r: r["input_csv_line"])]
            sources.append({"fields": json.loads(fields), "source_row_count": len(refs), "source_rows": refs})
        visits.append({"visit_id": visit_id, "source_group_id": group_id, "source_route_id": rid,
                       "source_route_name": name, "source_sequence": sequence, "stop_id": stop,
                       "group_role": "selected_source_group" if (rid, name) in selected else "other_source_group_preserved",
                       "direction_id": None, "direction_status": "unverified_source_name_group",
                       "source_row_count": len(source_rows), "source_variant_count": len(sources), "source_variants": sources})
    stop_lookup = {}
    for source in stops_document["stops"]:
        if not isinstance(source, dict) or not isinstance(source.get("stop_id"), str) or not source["stop_id"].strip():
            raise AnalysisError("stops.json:0:stop_id")
        sid = source["stop_id"]
        if sid in stop_lookup:
            raise AnalysisError("stops.json:0:stop_id")
        stop_lookup[sid] = source
    stops = [normalized_stop(sid, stop_lookup.get(sid), namespace) for sid in sorted({v["stop_id"] for v in visits})]
    groups = defaultdict(lambda: defaultdict(list))
    for visit in visits:
        groups[visit["source_group_id"]][visit["source_sequence"]].append(visit)
    segments, blocked, group_reports = [], [], []
    for group_id, positions in sorted(groups.items()):
        ordered = sorted(positions)
        example = positions[ordered[0]][0]
        members = [v for seq in ordered for v in positions[seq]]
        repeated = Counter(v["stop_id"] for v in members)
        group_reports.append({"source_group_id": group_id, "source_route_id": example["source_route_id"],
                              "source_route_name": example["source_route_name"], "group_role": example["group_role"],
                              "source_rows": sum(v["source_row_count"] for v in members), "visits": len(members),
                              "same_visit_excess_rows": sum(v["source_row_count"] - 1 for v in members),
                              "identical_variant_excess_rows": sum(variant["source_row_count"] - 1 for v in members for variant in v["source_variants"]),
                              "conflicting_sequence_positions": sum(len(v) > 1 for v in positions.values()),
                              "revisited_stop_ids": sorted(sid for sid, count in repeated.items() if count > 1),
                              "direction_status": "unverified_source_name_group", "reverse_direction_status": "not_extracted"})
        for a_seq, b_seq in zip(ordered, ordered[1:]):
            reasons = []
            if b_seq != a_seq + 1:
                reasons.append("sequence_gap")
            if len(positions[a_seq]) != 1 or len(positions[b_seq]) != 1:
                reasons.append("conflicting_sequence")
            if reasons:
                blocked.append({"source_group_id": group_id, "from_sequence": a_seq, "to_sequence": b_seq, "reasons": reasons})
                continue
            a, b = positions[a_seq][0], positions[b_seq][0]
            refs = lambda v: sorted(r["source_row_id"] for variant in v["source_variants"] for r in variant["source_rows"])
            segments.append({"segment_id": stable_id("segment", [a["visit_id"], b["visit_id"]]),
                             "source_group_id": group_id, "source_route_id": a["source_route_id"],
                             "source_route_name": a["source_route_name"],
                             "origin_visit_id": a["visit_id"], "destination_visit_id": b["visit_id"],
                             "origin_stop_id": a["stop_id"], "destination_stop_id": b["stop_id"],
                             "origin_source_sequence": a_seq, "destination_source_sequence": b_seq,
                             "origin_source_row_ids": refs(a), "destination_source_row_ids": refs(b),
                             **segment_distance(a, b, config["distance"]),
                             "travel_time_s": None, "travel_time_status": "unknown",
                             "allowed_turns_status": "unknown", "turnaround_status": "unknown", "return_feasibility_status": "unknown",
                             "operating_direction_status": "unverified_source_order_adjacency"})
    return {"stops": stops, "visits": visits, "segments": segments,
            "groups": group_reports, "blocked_boundaries": blocked,
            "source_rows_selected": len(rows)}


def validate_network(network, source_rows, config, visit_checksum, stops_document=None):
    """Reconstruct every input source row and independently verify adjacency refs."""
    stops, visits, segments = network["stops"], network["visits"], network["segments"]
    stop_ids = {s["stop_id"] for s in stops}
    lookup = {v["visit_id"]: v for v in visits}
    expected_rows = Counter(canonical({"input_csv_line": r["input_csv_line"], "source_csv_line": r["source_csv_line"],
                                      **{f: r[f] for f in VISIT_FIELDS}, "prior_group_role": r["prior_group_role"],
                                      "declared_multiplicity": r["declared_multiplicity"]})
                            for r in source_rows if r["ROUTE_ID"] in config["route_ids"])
    restored, source_ids = Counter(), []
    source_count_ok = True
    for visit in visits:
        actual_count = 0
        source_count_ok &= visit["source_group_id"] == stable_id("group", [config["source_namespace"], visit["source_route_id"], visit["source_route_name"]])
        source_count_ok &= visit["visit_id"] == stable_id("visit", [config["source_namespace"], visit["source_route_id"], visit["source_route_name"], visit["source_sequence"], visit["stop_id"]])
        for variant in visit["source_variants"]:
            f = variant["fields"]
            source_count_ok &= (f["ROUTE_ID"], f["ROUTE_NM"], int(f["BSST_ORDER"]), f["BSST_ID"]) == (visit["source_route_id"], visit["source_route_name"], visit["source_sequence"], visit["stop_id"])
            source_count_ok &= variant["source_row_count"] == len(variant["source_rows"])
            for ref in variant["source_rows"]:
                actual_count += 1
                source_ids.append(ref["source_row_id"])
                restored[canonical({"input_csv_line": ref["input_csv_line"], "source_csv_line": ref["upstream_csv_line_raw"],
                                    **variant["fields"], "SERIAL_NUM": ref["serial_num_raw"],
                                    "prior_group_role": ref["prior_group_role"], "declared_multiplicity": ref["declared_multiplicity_raw"]})] += 1
                source_count_ok &= ref["source_row_id"] == stable_id("row", [visit_checksum, ref["input_csv_line"]])
        source_count_ok &= actual_count == visit["source_row_count"]
        source_count_ok &= visit["source_variant_count"] == len(visit["source_variants"])
    positions = defaultdict(lambda: defaultdict(list))
    for v in visits:
        positions[v["source_group_id"]][v["source_sequence"]].append(v["visit_id"])
    expected_edges = set()
    for slots in positions.values():
        for a in slots:
            if a + 1 in slots and len(slots[a]) == len(slots[a + 1]) == 1:
                expected_edges.add((slots[a][0], slots[a + 1][0]))
    segment_ok = True
    for s in segments:
        a, b = lookup.get(s["origin_visit_id"]), lookup.get(s["destination_visit_id"])
        if a is None or b is None:
            segment_ok = False
            continue
        segment_ok &= a["source_group_id"] == b["source_group_id"] == s["source_group_id"]
        segment_ok &= (s["origin_stop_id"], s["destination_stop_id"]) == (a["stop_id"], b["stop_id"])
        segment_ok &= (s["origin_source_sequence"], s["destination_source_sequence"]) == (a["source_sequence"], b["source_sequence"])
        for prefix, v in (("origin", a), ("destination", b)):
            expected = sorted(ref["source_row_id"] for variant in v["source_variants"] for ref in variant["source_rows"])
            segment_ok &= s[prefix + "_source_row_ids"] == expected
        segment_ok &= all(s[k] == value for k, value in segment_distance(a, b, config["distance"]).items())
    known_groups = {(v["source_route_id"], v["source_route_name"]) for v in visits}
    selected = {(g["route_id"], g["source_route_name"]) for g in config["selected_groups"]}
    metadata_equal = True
    if stops_document is not None:
        metadata = {s["stop_id"]: s for s in stops_document["stops"]}
        metadata_equal = all(s == normalized_stop(s["stop_id"], metadata.get(s["stop_id"]), config["source_namespace"]) for s in stops)
    checks = {"source_rows_preserved_exactly": expected_rows == restored,
              "source_counts_consistent": bool(source_count_ok), "source_row_ids_unique": len(source_ids) == len(set(source_ids)),
              "stop_ids_unique": len(stops) == len(stop_ids), "visit_ids_unique": len(visits) == len(lookup),
              "visit_stop_references": all(v["stop_id"] in stop_ids for v in visits),
              "stop_set_exact_and_candidates_preserved": stop_ids == {v["stop_id"] for v in visits} and metadata_equal,
              "segments_unique": len(segments) == len({s["segment_id"] for s in segments}),
              "all_and_only_unambiguous_adjacent_pairs": len(segments) == len(expected_edges)
                  and {(s["origin_visit_id"], s["destination_visit_id"]) for s in segments} == expected_edges,
              "segment_references_distances_and_source_links": bool(segment_ok), "selected_groups_present": selected <= known_groups,
              "unknown_conditions_not_fabricated": all(v["direction_id"] is None and v["direction_status"] == "unverified_source_name_group" for v in visits)
                  and all(s["travel_time_s"] is None and all(s[k] == "unknown" for k in ("travel_time_status", "allowed_turns_status", "turnaround_status", "return_feasibility_status")) for s in segments)}
    return {"checks": checks, "all_checks_passed": all(checks.values()),
            "missing_source_rows": sum((expected_rows - restored).values()), "unexpected_source_rows": sum((restored - expected_rows).values())}


def clean(config_path):
    config_path = Path(config_path).resolve()
    config = settings(json.loads(config_path.read_text(encoding="utf-8-sig")), config_path.name)
    input_dir, output_dir = Path(config["input_dir"]).resolve(), Path(config["output_dir"]).resolve()
    if output_dir.exists() or output_dir == input_dir or input_dir in output_dir.parents:
        raise AnalysisError("output:0:path")
    visits_path, stops_path = input_dir / "route_visits.csv", input_dir / "stops.json"
    rows = load_visits(visits_path)
    stops_doc = json.loads(stops_path.read_text(encoding="utf-8-sig"))
    if not isinstance(stops_doc, dict) or not isinstance(stops_doc.get("stops"), list):
        error(stops_path, 0, "stops")
    manifest = {"route_visits.csv": digest(visits_path), "stops.json": digest(stops_path)}
    upstream = input_dir / "validation_report.json"
    upstream_sources = None
    if upstream.exists():
        old_report = json.loads(upstream.read_text(encoding="utf-8-sig"))
        upstream_sources = [r for r in old_report.get("sources", []) if r.get("path", "").replace("\\", "/").rsplit("/", 1)[-1] == "route_stops.csv"]
        manifest["validation_report.json"] = digest(upstream)
    built = build_network(rows, stops_doc, config, manifest["route_visits.csv"]["sha256"])
    output_dir.mkdir(parents=True)
    for name in ("stops", "visits", "segments"):
        dump_json(output_dir / (name + ".json"), {"schema_version": RULE_VERSION, name: built[name]})
    # Re-read the saved payloads before declaring any source/reference check passed.
    reloaded = {name: json.loads((output_dir / (name + ".json")).read_text(encoding="utf-8"))[name]
                for name in ("stops", "visits", "segments")}
    validation = validate_network(reloaded, rows, config, manifest["route_visits.csv"]["sha256"], stops_doc)
    distance_counts = Counter(s["distance_status"] for s in reloaded["segments"])
    report = {"status": "development_cleaning_draft_not_final_network_or_engine_input", "rule_version": RULE_VERSION,
              "config": config, "config_file": digest(config_path), "inputs": manifest,
              "upstream_route_stops_sources_inherited": upstream_sources,
              "upstream_raw_rows_rechecked": False,
              "rules": {"visit_identity": ["source_namespace", "ROUTE_ID", "ROUTE_NM", "numeric BSST_ORDER", "BSST_ID"],
                        "source_variant_comparison_fields": list(VARIANT_FIELDS),
                        "excluded_from_variant_comparison_but_preserved": ["SERIAL_NUM", "source_csv_line", "input_csv_line", "group_role", "declared_multiplicity"],
                        "direction": "Source name groups are unverified; reverse direction separately not extracted.",
                        "adjacency": "Same source group, consecutive numeric sequences, exactly one stop visit at each end; never skip gaps/conflicts."},
              "counts": {"input_visit_rows": len(rows), "selected_route_source_rows": sum(v["source_row_count"] for v in built["visits"]),
                         "other_route_rows_not_selected": sum(r["ROUTE_ID"] not in config["route_ids"] for r in rows),
                         "stops": len(built["stops"]), "visits": len(built["visits"]), "segments": len(built["segments"]),
                         "source_groups": len(built["groups"]), "same_visit_excess_rows": sum(g["same_visit_excess_rows"] for g in built["groups"]),
                         "identical_variant_excess_rows": sum(g["identical_variant_excess_rows"] for g in built["groups"]),
                         "conflicting_sequence_positions": sum(g["conflicting_sequence_positions"] for g in built["groups"]),
                         "revisited_stop_ids_per_group": sum(len(g["revisited_stop_ids"]) for g in built["groups"]),
                         "ambiguous_name_stops": sum(s["name_status"] == "ambiguous" for s in built["stops"]),
                         "ambiguous_coordinate_stops": sum(s["coordinate_status"] == "ambiguous" for s in built["stops"]),
                         "missing_metadata_stops": sum(not s["metadata_present"] for s in built["stops"]),
                         "distance_status": dict(sorted(distance_counts.items()))},
              "groups": built["groups"], "blocked_boundaries": built["blocked_boundaries"],
              "reverse_direction_status": "not_extracted", "operating_direction_confirmed": False,
              "unknown_execution_conditions": ["travel_time_s", "allowed_turns", "turnaround", "return_feasibility", "vehicle/schedule/operating policy"],
              "saved_file_validation": validation, "all_checks_passed": validation["all_checks_passed"]}
    dump_json(output_dir / "quality_report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    try:
        report = clean(args.config)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        parser.exit(2, f"{exc}\n" if isinstance(exc, AnalysisError) else f"network cleaning failed: {type(exc).__name__}\n")
    print(json.dumps({"output_dir": report["config"]["output_dir"], "counts": report["counts"], "all_checks_passed": report["all_checks_passed"]}))
    if not report["all_checks_passed"]:
        parser.exit(1, "network source/reference validation failed; see local quality report\n")


if __name__ == "__main__":
    main()
