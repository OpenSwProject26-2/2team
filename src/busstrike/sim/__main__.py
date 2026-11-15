"""CLI: python -m busstrike.sim run --scenario S --run-inputs R --plan P --out DIR"""

from __future__ import annotations

import argparse
import json
import sys

from .models import SimError
from .run import run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m busstrike.sim")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("run", help="시간표 생성 + 실행 + 로그·지표 기록")
    p.add_argument("--scenario", required=True)
    p.add_argument("--run-inputs", required=True)
    p.add_argument("--plan", required=True)
    p.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        result = run(args.scenario, args.run_inputs, args.plan, args.out)
    except SimError as exc:
        print(json.dumps({"code": exc.code, "message": exc.message, "details": exc.details},
                         ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
