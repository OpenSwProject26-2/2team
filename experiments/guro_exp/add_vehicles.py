"""비교 입력 plan.json의 노선별 vehicles에 정해진 수를 더한 사본을 만든다(정류장·노선은 그대로).

    python3 experiments/guro_exp/add_vehicles.py --plan plan_f11.json --add 1,0,0,1 \
        --suffix=-f13 --out plan_f13.json

스모크 안의 13대 조건용이다. 묶음의 scripts/run_all_bundles.py와 같은 규칙(r1·r4에 1대씩)을 쓴다.
변환기 S5가 차량 합 = 가용 대수를 요구하기 때문이다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--plan", required=True, type=Path)
    ap.add_argument("--add", required=True, help="노선 순서대로 더할 대수, 쉼표 구분")
    ap.add_argument("--suffix", default="", help="plan_id 뒤에 붙일 문자열")
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    add = [int(x) for x in args.add.split(",")]
    if len(add) != len(plan["routes"]):
        raise SystemExit(f"--add 개수 {len(add)} ≠ 노선 수 {len(plan['routes'])}")
    for route, n in zip(plan["routes"], add):
        route["vehicles"] += n
    plan["plan_id"] += args.suffix
    args.out.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{args.out}: 차량 합 {sum(r['vehicles'] for r in plan['routes'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
