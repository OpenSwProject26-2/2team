"""Validate A fixtures and explicitly map them to the existing C example."""

import argparse
from datetime import date
import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
EXAMPLE = REPO_ROOT / "C.Part/opt/examples/synthetic.json"
SEGMENT_FIELDS = ("segment_id", "origin", "destination", "travel_s", "distance_m")
DEMAND_FIELDS = ("demand_id", "origin", "destination", "arrival_s",
                 "passenger_count", "original_route_id")


def require(condition, field):
    if not condition:
        raise ValueError(field)


def identifier(value):
    require(isinstance(value, str) and bool(value.strip()), "identifier")


def integer(value, field, minimum=0):
    require(type(value) is int and value >= minimum, field)


def unique_ids(values, field):
    for value in values:
        identifier(value)
    require(len(values) == len(set(values)), field)


def validate_common(network, demand, example):
    """Validate this fixture draft, not a general M00 or operational schema."""
    try:
        for document in (network, demand):
            m = document["metadata"]
            require(m["schema_version"] == "a-common-synthetic-v1", "schema_version")
            require(m["synthetic"] is True, "synthetic")
            for field in ("data_version", "dataset_version", "status"):
                identifier(m[field])
            identifier(m["source"]["path"])
            identifier(m["source"]["credit"])
            time = m["time_reference"]
            date.fromisoformat(time["service_date"])
            require(time["timezone"] == "Asia/Seoul" and time["unit"] == "s"
                    and time["origin"] == "service_date_midnight", "time_reference")
        nm, dm = network["metadata"], demand["metadata"]
        require(nm["time_reference"] == dm["time_reference"], "time_reference mismatch")
        require(nm["time_reference"]["service_date"] == example["service_date"], "service_date mismatch")
        require(nm["dataset_version"] == dm["dataset_version"] == example["dataset_version"],
                "dataset_version mismatch")
        require(nm["data_version"] == dm["network_version"], "network_version mismatch")
        require(dm["scope_mode"] == example["scope_mode"], "scope_mode mismatch")
        crs = nm["coordinate_reference"]
        require(crs == {"crs": "EPSG:4326", "axis_order": ["lat", "lon"],
                        "unit": "degree", "synthetic": True}, "coordinate_reference")
        n, d = network["data"], demand["data"]
        for field in ("stops", "segments", "allowed_turns", "turnaround_stops"):
            require(isinstance(n[field], list), field)
        require(isinstance(d["demands"], list), "demands")
        unique_ids(n["stops"], "duplicate stops")
        require(bool(n["stops"]), "stops")
        stops = set(n["stops"])
        coordinates = network["coordinates"]
        require(set(coordinates) == stops, "coordinate stop references")
        for coordinate in coordinates.values():
            for axis, bound in (("lat", 90), ("lon", 180)):
                v = coordinate[axis]
                require(type(v) in (int, float) and math.isfinite(v)
                        and -bound <= v <= bound, axis)
        unique_ids([s["segment_id"] for s in n["segments"]], "duplicate segments")
        segments = {s["segment_id"]: s for s in n["segments"]}
        for segment in segments.values():
            for field in ("origin", "destination"):
                identifier(segment[field])
                require(segment[field] in stops, "segment stop reference")
            integer(segment["travel_s"], "travel_s", 1)
            integer(segment["distance_m"], "distance_m")
        for turn in n["allowed_turns"]:
            require(isinstance(turn, list) and len(turn) == 2, "allowed_turns")
            for segment_id in turn:
                identifier(segment_id)
                require(segment_id in segments, "turn segment reference")
            require(segments[turn[0]]["destination"] == segments[turn[1]]["origin"],
                    "disconnected turn")
        unique_ids(n["turnaround_stops"], "duplicate turnaround stops")
        require(set(n["turnaround_stops"]) <= stops, "turnaround reference")
        unique_ids([r["demand_id"] for r in d["demands"]], "duplicate demands")
        original = {r["route_id"] for r in example["routes"] if r["original"]}
        for row in d["demands"]:
            for field in ("origin", "destination"):
                identifier(row[field])
                require(row[field] in stops, "demand stop reference")
            identifier(row["original_route_id"])
            require(row["original_route_id"] in original, "original route reference")
            integer(row["arrival_s"], "arrival_s")
            integer(row["passenger_count"], "passenger_count")
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("missing or malformed fixture field") from exc


def compose_inputs(network_path=HERE / "network.json", demand_path=HERE / "demand.json",
                   example_path=EXAMPLE):
    """Read both new files; borrow only C-owned conditions and identity fields."""
    def read(path):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    network, demand, example = read(network_path), read(demand_path), read(example_path)
    validate_common(network, demand, example)
    n, d = network["data"], demand["data"]
    # Allowlist each calculation field. Metadata and coordinates never enter opt.
    return {
        "input_bundle_id": example["input_bundle_id"],
        "scenario_id": example["scenario_id"],
        "dataset_version": network["metadata"]["dataset_version"],
        "network_version": network["metadata"]["data_version"],
        "demand_version": demand["metadata"]["data_version"],
        "conditions_version": example["conditions_version"],
        "service_date": network["metadata"]["time_reference"]["service_date"],
        "split": example["split"],
        "scope_mode": demand["metadata"]["scope_mode"],
        "stops": list(n["stops"]),
        "segments": [{k: s[k] for k in SEGMENT_FIELDS} for s in n["segments"]],
        "allowed_turns": [list(t) for t in n["allowed_turns"]],
        "turnaround_stops": list(n["turnaround_stops"]),
        "demands": [{k: r[k] for k in DEMAND_FIELDS} for r in d["demands"]],
        "routes": [{k: r[k] for k in ("route_id", "outbound", "return_segments", "original")}
                   for r in example["routes"]],
        "fleet": [{k: v[k] for k in ("vehicle_id", "capacity", "initial_stop", "return_stop",
                                     "available_start_s", "available_end_s")}
                  for v in example["fleet"]],
        "policy": {k: example["policy"][k] for k in ("start_s", "end_s", "dwell_s", "turnaround_s",
                                                      "max_wait_s", "max_travel_s")},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", type=Path, default=HERE / "network.json")
    parser.add_argument("--demand", type=Path, default=HERE / "demand.json")
    parser.add_argument("--example", type=Path, default=EXAMPLE)
    parser.add_argument("--output", type=Path, help="Optional new calculation JSON file")
    args = parser.parse_args()
    content = json.dumps(compose_inputs(args.network, args.demand, args.example), indent=2) + "\n"
    if args.output:
        # Never overwrite an existing example or a user's existing output.
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(content)
    else:
        print(content, end="")


if __name__ == "__main__":
    main()
