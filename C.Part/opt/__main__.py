"""python3 -m opt --input opt/examples/synthetic.json"""

import argparse
from dataclasses import asdict
import json
import sys

from .baselines import baseline
from .evaluator import evaluate
from .models import InputError, load_inputs
from .search import exhaustive, search


def main() -> int:
    parser = argparse.ArgumentParser(description="합성 입력 기반 C 운행안 비교")
    parser.add_argument("--input", required=True)
    parser.add_argument("--max-evaluations", type=int, default=1000)
    args = parser.parse_args()
    try:
        inputs = load_inputs(args.input)
        result = search(inputs, args.max_evaluations)
        plans = [baseline(inputs), baseline(inputs, proportional=True), result.best.plan]
        rows = []
        for plan in plans:
            metric = evaluate(inputs, plan)
            rows.append({"plan": plan.to_service_plan(inputs), "estimate": {
                **asdict(metric), "fulfillment_rate": metric.fulfillment_rate}})
        benchmark = exhaustive(inputs)
        payload = {
            "mode": "simulation", "b_integration": "not_connected",
            "input_bundle_id": inputs.input_bundle_id, "input_checksum": inputs.checksum,
            "comparison": rows, "evaluations": result.evaluations,
            "rejected_candidates": result.rejected_candidates,
            "top_plans": [p.plan.to_service_plan(inputs) for p in result.top],
            "exact_comparison": {
                "scope": "fixed_candidates_same_approximate_evaluator",
                "allocations": benchmark.evaluations,
                "best_served": benchmark.best.estimate.served,
                "served_gap": benchmark.best.estimate.served - result.best.estimate.served,
                "fulfillment_gap": ((benchmark.best.estimate.served - result.best.estimate.served)
                                    / inputs.total_demand if inputs.total_demand else None),
            },
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (InputError, OSError) as exc:
        print(json.dumps({"code": getattr(exc, "code", "DATA_INCOMPLETE"),
                          "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
