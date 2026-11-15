"""M05 실행의 독립 검증기(simulator.md §8).

입력 파일과 이벤트 로그만 읽어 불변식 V01–V15를 다시 계산하고 `validation.json`(§8.3)을 만든다.
`busstrike.sim`을 import하지 않는다(테스트가 AST로 확인한다).

CLI::

    python -m busstrike.evaluation.validator --run-dir DIR [--scenario S] [--run-inputs R] [--plan P] [--out FILE]

위반이 있으면 종료 코드 1, 입력 파일을 읽을 수 없으면 2.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Sequence

from ._checks import CHECKS, SEMANTIC_CHECKS, Sink, build_ctx, parse_log
from ._inputs import ValidatorInputError, json_bytes, load_run

VALIDATOR_VERSION = "busstrike-validator/0.1.0"
VALIDATION_FILE = "validation.json"
MAX_VIOLATIONS_PER_CHECK = 100


def _violation_key(v: dict) -> tuple:
    return (v["check_id"], v.get("seq", -1), v.get("passenger_id", ""), v.get("vehicle_id", ""),
            v["message"], json_bytes([v.get("expected"), v.get("actual"), v.get("trip_id"), v.get("t")]))


def validate_run(run_dir: Path, *, scenario_path: Path | None = None, run_inputs_path: Path | None = None,
                 plan_path: Path | None = None) -> dict:
    """한 실행을 검증해 `validation.json` 내용을 돌려준다(파일은 쓰지 않는다).

    입력 파일이 없거나 형식이 깨졌으면 `ValidatorInputError`.
    """
    f = load_run(Path(run_dir), scenario_path=scenario_path, run_inputs_path=run_inputs_path, plan_path=plan_path)
    sink = Sink()
    status: dict[str, str] = {}
    events = parse_log(f, sink)
    if sink.items["V01"]:
        status["V01"] = "fail"
        for cid, _ in CHECKS[1:]:
            status[cid] = "skipped"
    else:
        status["V01"] = "pass"
        ctx = build_ctx(f, events)
        for cid, fn in SEMANTIC_CHECKS:
            try:
                fn(ctx, sink)
            except Exception as exc:  # 깨진 입력 조합으로 검사가 끝까지 못 가도 결론은 "실패"다
                sink.add(cid, f"check aborted: {type(exc).__name__}: {exc}")
            status[cid] = "fail" if sink.items[cid] else "pass"

    checks: list[dict[str, Any]] = []
    violations: list[dict] = []
    truncated = False
    for cid, name in CHECKS:
        items = sorted(sink.items[cid], key=_violation_key)
        checks.append({"id": cid, "name": name, "status": status[cid], "violations": len(items)})
        if len(items) > MAX_VIOLATIONS_PER_CHECK:
            truncated = True
        violations.extend(items[:MAX_VIOLATIONS_PER_CHECK])
    return {
        "validator_version": VALIDATOR_VERSION,
        "run_id": f.run.get("run_id"),
        "plan_id": f.plan.plan_id,
        "plan_revision": f.plan.revision,
        "input_bundle_id": f.run_inputs.get("input_bundle_id"),
        "verified": all(c["status"] == "pass" for c in checks),
        "checks": checks,
        "violations": violations,
        "truncated": truncated,
    }


def write_report(report: dict, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json_bytes(report))


def failed_checks(report: dict) -> list[str]:
    """C `Assessment.violations`에 넣을 실패 검사 ID 목록(§8.3)."""
    return [c["id"] for c in report["checks"] if c["status"] != "pass"]


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m busstrike.evaluation.validator",
                                 description="M05 실행 결과를 입력·로그만으로 독립 검증한다.")
    ap.add_argument("--run-dir", required=True, type=Path,
                    help="run.json·timetable.json·events.jsonl·metrics.json이 있는 디렉터리")
    ap.add_argument("--scenario", type=Path, help="scenario.json (기본: RUN_DIR/scenario.json)")
    ap.add_argument("--run-inputs", type=Path, help="run_inputs.json (기본: RUN_DIR/run_inputs.json)")
    ap.add_argument("--plan", type=Path, help="plan.json (기본: RUN_DIR/plan.json)")
    ap.add_argument("--out", type=Path, help="리포트 경로 (기본: RUN_DIR/validation.json)")
    args = ap.parse_args(argv)
    try:
        report = validate_run(args.run_dir, scenario_path=args.scenario, run_inputs_path=args.run_inputs,
                              plan_path=args.plan)
    except ValidatorInputError as exc:
        print(f"validator: cannot read inputs: {exc}", file=sys.stderr)
        return 2
    out = args.out or (args.run_dir / VALIDATION_FILE)
    write_report(report, out)
    failed = failed_checks(report)
    if failed:
        print(f"validator: NOT verified ({', '.join(failed)})", file=sys.stderr)
        return 1
    print("validator: verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
