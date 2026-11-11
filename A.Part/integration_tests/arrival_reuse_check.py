"""Fresh-process regression: read first, then reuse actual B types and c_demands."""

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from data.prepare_arrivals import pinned_b, read_prepared


def check(directory, b_root, b_commit):
    assert not any(n == "sim" or n.startswith("sim.") for n in sys.modules), "fresh process required"
    calls = Counter()

    def guard(frame, event, arg):
        if event == "call":
            module = frame.f_globals.get("__name__")
            name = frame.f_code.co_name
            if (module, name) in (("sim.arrivals", "generate"), ("sim.engine", "simulate")):
                calls[module + "." + name] += 1
                raise AssertionError("reuse must not generate or simulate")

    sys.setprofile(guard)
    previous_path = list(sys.path)
    try:
        realization, report = read_prepared(directory, b_root=b_root, b_commit=b_commit)
        original_type = type(realization.rows[0])
        with (directory / "aggregate_routes.jsonl").open(encoding="utf-8") as stream:
            route_of = {r["aggregate_id"]: r["original_route_id"] for r in map(json.loads, stream)}
        with pinned_b(b_root, b_commit) as (api, _):
            # This raised SimulationError: expected BinRow before the lifetime fix.
            assert api.validate_realization(realization) is realization
            assert original_type is api.BinRow
            sys.path.insert(0, str(b_root / "C.Part"))
            from sim.adapters.opt_adapter import c_demands
            from opt.models import Demand

            demands = c_demands(realization, lambda row: route_of[row.aggregate_id])
            assert all(isinstance(d, Demand) for d in demands)
            rows = {r.aggregate_id: r for r in realization.rows}
            expected = Counter()
            for passenger in realization.passengers:
                aid, _ = json.loads(passenger.passenger_id)
                row = rows[aid]
                expected[row.origin, row.destination, passenger.time_s, route_of[aid]] += 1
            actual = Counter()
            for demand in demands:
                actual[demand.origin, demand.destination, demand.arrival_s, demand.original_route_id] += demand.passenger_count
            assert actual == expected
            assert sum(d.passenger_count for d in demands) == realization.total
        with pinned_b(b_root, b_commit) as (api, _):
            assert api.BinRow is original_type
            api.validate_realization(realization)
        assert not calls
        return {"fresh_process": True, "b_commit": b_commit, "b_validation_passed": True,
                "c_demands_passengers": sum(d.passenger_count for d in demands),
                "generated_passengers_in_saved_input": report["generated_passengers"],
                "generate_calls": 0, "simulation_calls": 0}
    finally:
        sys.setprofile(None)
        sys.path[:] = previous_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared-dir", type=Path, required=True)
    parser.add_argument("--b-root", type=Path, required=True)
    parser.add_argument("--b-commit", required=True)
    args = parser.parse_args()
    print(json.dumps(check(args.prepared_dir, args.b_root, args.b_commit)))


if __name__ == "__main__":
    main()
