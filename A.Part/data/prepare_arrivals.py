"""Prepare experimental arrivals through a pinned external B API, no simulation."""

import argparse
from contextlib import contextmanager
from datetime import date
import importlib
from itertools import zip_longest
import json
from pathlib import Path
import re
import subprocess
import sys

from .demand_analysis import AnalysisError, error
from .demand_bins import (BIN_RULE_VERSION, records, saved_bins, validate_bins,
                          validate_inputs)
from .extract_guro_draft import digest, dump_json
from .link_demand import canonical, read_json


RULE_VERSION = "experimental-arrival-preparation-v1"
POLICY = "uniform_bin_sha256_v1"
A_ROOT = Path(__file__).resolve().parents[2]
# B DTO instances escape the context (read_prepared returns them). Keep one
# checked module/type binding for the process instead of unloading their classes.
_B_BINDING = None


def parameters(policy, seed, service_date):
    if policy != POLICY:
        error("parameters", 0, "unsupported_policy")
    if not isinstance(seed, str) or not seed.strip():
        error("parameters", 0, "seed")
    if not isinstance(service_date, str):
        error("parameters", 0, "service_date")
    try:
        valid = date.fromisoformat(service_date).isoformat() == service_date
    except ValueError:
        valid = False
    if not valid:
        error("parameters", 0, "service_date")


def git(root, *args):
    try:
        result = subprocess.run(["git", "--no-optional-locks", "-C", str(root), *args],
                                capture_output=True, encoding="utf-8", errors="replace", check=False)
    except OSError:
        error("b_checkout", 0, "git_unavailable")
    if result.returncode:
        error("b_checkout", 0, "git_query_failed")
    return result.stdout.strip()


def inspect_b(root, expected_commit):
    root = Path(root).resolve()
    if root.is_relative_to(A_ROOT):
        error("b_checkout", 0, "must_be_outside_a_workspace")
    if not isinstance(expected_commit, str) or re.fullmatch(r"[0-9a-f]{40}", expected_commit) is None:
        error("b_checkout", 0, "full_commit_sha_required")
    if Path(git(root, "rev-parse", "--show-toplevel")).resolve() != root:
        error("b_checkout", 0, "checkout_root")
    actual = git(root, "rev-parse", "HEAD")
    if actual != expected_commit:
        error("b_checkout", 0, "commit_mismatch")
    if git(root, "status", "--porcelain", "--untracked-files=all"):
        error("b_checkout", 0, "working_tree_not_clean")
    files = []
    for filename in ("sim/__init__.py", "sim/arrivals.py", "sim/models.py", "sim/run_inputs.py"):
        git(root, "ls-files", "--error-unmatch", "--", filename)
        files.append(digest(root / filename))
    return {"root": str(root), "expected_commit": expected_commit, "actual_commit": actual,
            "working_tree_clean": True, "api_files": files}


@contextmanager
def pinned_b(root, expected_commit):
    """Reuse one pinned B module lifetime; restore search path, never reload DTOs."""
    global _B_BINDING
    metadata = inspect_b(root, expected_commit)
    root = Path(metadata["root"])
    previous_path, previous_bytecode = list(sys.path), sys.dont_write_bytecode
    previous_modules = {name for name in sys.modules if name == "sim" or name.startswith("sim.")}
    keep_modules = False
    try:
        if _B_BINDING is not None:
            if _B_BINDING["fingerprint"] != canonical(metadata):
                error("b_checkout", 0, "process_binding_mismatch_use_new_process")
            if any(sys.modules.get(name) is not module for name, module in _B_BINDING["modules"].items()):
                error("b_checkout", 0, "pinned_modules_removed_or_replaced")
        for name in previous_modules:
            filename = getattr(sys.modules[name], "__file__", None)
            if filename is None or not Path(filename).resolve().is_relative_to(root):
                error("b_checkout", 0, "conflicting_sim_import")
        sys.path.insert(0, str(root))
        sys.dont_write_bytecode = True
        try:
            api = importlib.import_module("sim.arrivals")
        except (ImportError, OSError):
            error("b_checkout", 0, "arrivals_import_failed")
        for name, module in list(sys.modules.items()):
            if name == "sim" or name.startswith("sim."):
                filename = getattr(module, "__file__", None)
                if filename is None or not Path(filename).resolve().is_relative_to(root):
                    error("b_checkout", 0, "module_outside_pinned_checkout")
                git(root, "ls-files", "--error-unmatch", "--", Path(filename).resolve().relative_to(root).as_posix())
        for name in ("BinRow", "check_rows", "generate", "write_realization", "read_realization", "validate_realization"):
            if not callable(getattr(api, name, None)):
                error("b_checkout", 0, "unsupported_arrivals_api")
        if getattr(api, "GENERATOR", None) != POLICY:
            error("b_checkout", 0, "unsupported_generator")
        types = (api.BinRow, api.ArrivalRealization, sys.modules["sim.models"].Passenger)
        if _B_BINDING is not None and any(a is not b for a, b in zip(types, _B_BINDING["types"])):
            error("b_checkout", 0, "pinned_types_reloaded")
        modules = {name: module for name, module in sys.modules.items()
                   if name == "sim" or name.startswith("sim.")}
        if _B_BINDING is None:
            _B_BINDING = {"fingerprint": canonical(metadata), "modules": modules, "types": types}
        else:
            _B_BINDING["modules"].update(modules)
        keep_modules = True
        yield api, metadata
    finally:
        sys.path[:] = previous_path
        sys.dont_write_bytecode = previous_bytecode
        # Roll back failed imports only. Successful imports must outlive any
        # returned realization so later B validation sees identical BinRow types.
        if not keep_modules:
            for name in set(sys.modules) - previous_modules:
                if name == "sim" or name.startswith("sim."):
                    del sys.modules[name]


def b_call(function, field, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except (ValueError, TypeError, KeyError, AttributeError, IndexError, OverflowError):
        error("B_arrivals", 0, field)


def bin_values(path):
    """Strict six-column CSV; preserve identifiers, explicitly convert integers."""
    for row in saved_bins(path):
        try:
            yield {**row, **{field: int(row[field]) for field in
                            ("bin_start_s", "bin_width_s", "passenger_count")}}
        except ValueError:
            error(path, 0, "integer")


def validate_a_inputs(bins, links, draft, network):
    report, paths, _, _ = validate_inputs(links, draft, network)
    bin_report = read_json(bins / "report.json")
    if bin_report.get("rule_version") != BIN_RULE_VERSION or bin_report.get("all_checks_passed") is not True:
        error("report.json", 0, "successful_bin_conversion_required")
    inherited = {str(Path(entry["path"]).resolve()): entry["sha256"] for entry in bin_report["inputs"]}
    for path in paths:
        if inherited.get(str(path.resolve())) != digest(path)["sha256"]:
            error(path, 0, "bin_input_checksum_mismatch")
    checked = validate_bins(links, bins, report["summary"])
    if not checked["all_checks_passed"] or bin_report.get("summary") != report["summary"]:
        error("report.json", 0, "bin_source_or_included_scope_mismatch")
    paths += [bins / f for f in ("report.json", "demand_bins_all.csv", "demand_bins_included.csv")]
    return report, [p.resolve() for p in paths]


def mappings(links):
    for record in records(links):
        if record["disposition"] == "included":
            yield {"aggregate_id": record["aggregate_id"], "original_route_id": record["source"]["ROUTE_ID"],
                   "source": record["source"], "input_csv_line": record["input_csv_line"],
                   "source_candidates": record["candidates"]}


def validate_mapping(path, rows, links=None):
    by_id = {r.aggregate_id: r for r in rows}
    seen = set()
    with path.open(encoding="utf-8") as stream:
        expected = mappings(links) if links is not None else None
        saved = (json.loads(line) for line in stream)
        pairs = zip_longest(expected, saved) if expected is not None else ((None, r) for r in saved)
        for original, row in pairs:
            if row is None or (expected is not None and canonical(original) != canonical(row)):
                error(path, 0, "source_mapping_mismatch")
            aid = row["aggregate_id"]
            source = row["source"]
            if aid not in by_id or aid in seen or row["original_route_id"] != source["ROUTE_ID"]:
                error(path, 0, "aggregate_route_mapping")
            b = by_id[aid]
            if (source["RIDE_BSST_ID"], source["ALGH_BSST_ID"], int(source["PSNG_NUM"])) != (
                    b.origin, b.destination, b.passenger_count):
                error(path, 0, "mapping_od_or_count")
            if source["RIDE_DTM_H"] not in ("07", "08") or (
                    b.bin_start_s, b.bin_width_s) != (int(source["RIDE_DTM_H"]) * 3600, 3600):
                error(path, 0, "mapping_bin")
            seen.add(aid)
    if seen != set(by_id):
        error(path, 0, "mapping_cardinality")


def prepare(bins, links, draft, network, output, *, b_root, b_commit, policy, seed, service_date):
    parameters(policy, seed, service_date)
    bins, links, draft, network, output = map(Path, (bins, links, draft, network, output))
    if output.exists() or any(output.resolve().is_relative_to(p.resolve())
                              for p in (bins, links, draft, network, Path(b_root))):
        error(output, 0, "output_exists_or_inside_input")
    link_report, paths = validate_a_inputs(bins, links, draft, network)
    snapshots = [digest(p) for p in paths]
    values = tuple(bin_values(bins / "demand_bins_included.csv"))
    with pinned_b(b_root, b_commit) as (api, before_b):
        rows = b_call(api.check_rows, "check_rows", tuple(api.BinRow(**row) for row in values))
        realization = b_call(api.generate, "generate", rows, seed)
        b_call(api.validate_realization, "generated_validation", realization, rows)
        output.mkdir(parents=True, exist_ok=False)
        with (output / "realization.jsonl").open("x", encoding="utf-8") as stream:
            b_call(api.write_realization, "write_realization", realization, stream)
        with (output / "aggregate_routes.jsonl").open("x", encoding="utf-8") as stream:
            for mapping in mappings(links):
                stream.write(canonical(mapping) + "\n")
        with (output / "realization.jsonl").open(encoding="utf-8") as stream:
            loaded = b_call(api.read_realization, "read_realization", stream, rows,
                            expected_checksum=realization.checksum)
        b_call(api.validate_realization, "saved_validation", loaded, rows,
               expected_checksum=realization.checksum, regenerate=False)
        validate_mapping(output / "aggregate_routes.jsonl", rows, links)
        after_b = inspect_b(b_root, b_commit)
        unchanged = snapshots == [digest(p) for p in paths]
        expected = link_report["summary"]["dispositions"]["included"]["passengers"]
        checks = {"aggregate_count_equals_generated": sum(r.passenger_count for r in rows) == loaded.total == expected,
                  "saved_checksum_matches": loaded.checksum == realization.checksum,
                  "b_code_unchanged": before_b == after_b, "a_inputs_unchanged": unchanged,
                  "b_saved_od_counts_and_bin_bounds_valid": True, "source_mapping_preserved": True}
        report = {"rule_version": RULE_VERSION, "status": "development_uniform_arrivals_not_team_adopted_policy",
                  "b_before": before_b, "b_after": after_b, "b_changes_detected": before_b != after_b,
                  "generator": loaded.generator, "policy": policy, "seed": seed, "service_date": service_date,
                  "timezone": "Asia/Seoul", "service_date_assignment": "explicit_experimental_argument_not_verified",
                  "observation_reference_date": link_report["observation_reference_date"],
                  "aggregate_rows": len(rows), "generated_passengers": loaded.total,
                  "bin_source_checksum": loaded.source_checksum, "realization_checksum": loaded.checksum,
                  "inputs": snapshots, "outputs": {name: digest(output / name) for name in
                                                      ("realization.jsonl", "aggregate_routes.jsonl")},
                  "checks": checks, "all_checks_passed": all(checks.values()),
                  "generation_called": True, "simulation_executed": False, "c_plan_created": False,
                  "generation_algorithm_reimplemented": False}
        dump_json(output / "report.json", report)
        (output / "README.md").write_text(
            "# B 고정 생성기를 사용한 개발용 발생 입력\n\n"
            "균등 발생은 실험 가정이며 팀 확정 정책이나 실제 정류장 도착 관측이 아니다.\n"
            "realization.jsonl은 B write_realization 형식이며 report.json의 서비스일과 함께 사용한다.\n"
            "B realization checksum 자체에는 서비스일이 들어 있지 않다.\n"
            "aggregate_routes.jsonl은 aggregate_id별 원래 노선·추출 행·방문/구간 후보 대응이다.\n"
            "report.json에 원천 체크섬·B SHA/변경 여부·정책·seed·서비스일·인원·realization checksum을 기록했다.\n"
            "기존 결과는 read_prepared로 재생성 없이 읽고 검증할 수 있다.\n"
            "차량 운행·C 계획·GTFS·환승·실제 운행 검증은 수행하지 않았다.\n",
            encoding="utf-8")
        return report


def read_prepared(directory, *, b_root, b_commit, expected_report_sha256=None):
    """Reuse stored output via B read/validate APIs, never call generate."""
    directory = Path(directory)
    if expected_report_sha256 is not None and digest(directory / "report.json")["sha256"] != expected_report_sha256:
        error("report.json", 0, "expected_report_checksum")
    report = read_json(directory / "report.json")
    if report.get("rule_version") != RULE_VERSION or report.get("all_checks_passed") is not True:
        error("report.json", 0, "successful_preparation_required")
    parameters(report["policy"], report["seed"], report["service_date"])
    if report["b_before"]["actual_commit"] != b_commit:
        error("report.json", 0, "b_commit_mismatch")
    for name in ("realization.jsonl", "aggregate_routes.jsonl"):
        if digest(directory / name)["sha256"] != report["outputs"][name]["sha256"]:
            error(name, 0, "saved_file_checksum")
    with pinned_b(b_root, b_commit) as (api, before):
        # An identical checkout can be relocated in a new process. A live process
        # retains its original root/commit/type binding to protect returned DTOs.
        if [f["sha256"] for f in before["api_files"]] != [f["sha256"] for f in report["b_before"]["api_files"]]:
            error("b_checkout", 0, "recorded_api_content_mismatch")
        with (directory / "realization.jsonl").open(encoding="utf-8") as stream:
            realization = b_call(api.read_realization, "read_realization", stream,
                                 expected_checksum=report["realization_checksum"])
        b_call(api.validate_realization, "saved_validation", realization,
               expected_checksum=report["realization_checksum"], regenerate=False)
        if (realization.generator, realization.seed, realization.source_checksum, realization.total,
                len(realization.rows)) != (report["generator"], report["seed"], report["bin_source_checksum"],
                                          report["generated_passengers"], report["aggregate_rows"]):
            error("report.json", 0, "realization_metadata_mismatch")
        validate_mapping(directory / "aggregate_routes.jsonl", realization.rows)
        if inspect_b(b_root, b_commit) != before:
            error("b_checkout", 0, "changed_during_read")
        return realization, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("bins-dir", "links-dir", "draft-dir", "network-dir", "output-dir", "b-root"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("b-commit", "policy", "seed", "service-date"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    try:
        report = prepare(args.bins_dir, args.links_dir, args.draft_dir, args.network_dir, args.output_dir,
                         b_root=args.b_root, b_commit=args.b_commit, policy=args.policy,
                         seed=args.seed, service_date=args.service_date)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
        parser.exit(2, f"{exc}\n" if isinstance(exc, AnalysisError) else f"arrival preparation failed: {type(exc).__name__}\n")
    print(json.dumps({"generated_passengers": report["generated_passengers"],
                      "realization_checksum": report["realization_checksum"],
                      "b_commit": report["b_before"]["actual_commit"], "all_checks_passed": report["all_checks_passed"]}))
    if not report["all_checks_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
