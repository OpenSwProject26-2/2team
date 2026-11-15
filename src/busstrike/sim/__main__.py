"""CLI: python -m busstrike.sim run --scenario S --run-inputs R --plan P --out DIR
       python -m busstrike.sim gtfs --scenario S --timetable T --out DIR"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import io
from .gtfs import export_gtfs
from .models import SimError
from .run import run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m busstrike.sim")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("run", help="시간표 생성 + 실행 + M07 폴더(scenario/, run/) 기록")
    p.add_argument("--scenario", required=True)
    p.add_argument("--run-inputs", required=True)
    p.add_argument("--plan", required=True)
    p.add_argument("--out", required=True)
    g = sub.add_parser("gtfs", help="timetable.json → 셔틀 GTFS")
    g.add_argument("--scenario", required=True)
    g.add_argument("--timetable", required=True)
    g.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "gtfs":
            scenario = io.scenario_from_dict(io.read_json(Path(args.scenario)))
            timetable = io.timetable_from_dict(io.read_json(Path(args.timetable)))
            print(json.dumps(export_gtfs(timetable, scenario.service_date, args.out), sort_keys=True))
            return 0
        result = run(args.scenario, args.run_inputs, args.plan, args.out)
    except SimError as exc:
        print(json.dumps({"code": exc.code, "message": exc.message, "details": exc.details},
                         ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
