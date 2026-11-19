from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from opt.baselines import baseline
from opt.evaluator import evaluate
from opt.models import load_inputs
from tests.fixtures.compose import EXAMPLE, HERE, REPO_ROOT, compose_inputs, validate_common


class CommonFixtureTests(unittest.TestCase):
    def setUp(self):
        self.network = json.loads((HERE / "network.json").read_text(encoding="utf-8"))
        self.demand = json.loads((HERE / "demand.json").read_text(encoding="utf-8"))
        self.example = json.loads(EXAMPLE.read_text(encoding="utf-8"))

    def reconstruct(self, network=None, demand=None, example=None):
        # All checks go through the new files and the real reconstruction path.
        with TemporaryDirectory() as directory:
            paths = [Path(directory) / name for name in ("network.json", "demand.json", "example.json")]
            documents = (network if network is not None else self.network,
                         demand if demand is not None else self.demand,
                         example if example is not None else self.example)
            for path, document in zip(paths, documents):
                path.write_text(json.dumps(document), encoding="utf-8")
            raw = compose_inputs(*paths)
            result = Path(directory) / "inputs.json"
            result.write_text(json.dumps(raw), encoding="utf-8")
            return raw, load_inputs(result)

    def test_required_fields_types_and_references_are_valid(self):
        validate_common(self.network, self.demand, self.example)
        raw, loaded = self.reconstruct()
        self.assertEqual(set(raw), set(self.example))
        self.assertEqual(len(loaded.allowed_turns), 8)
        self.assertEqual(set(loaded.turnaround_stops), set(loaded.stops))

    def test_original_case_counts_and_values_preserved(self):
        raw, loaded = self.reconstruct()
        self.assertEqual((len(loaded.stops), len(loaded.segments), len(loaded.routes),
                          len(loaded.demands), loaded.total_demand), (4, 6, 3, 5, 9))
        self.assertEqual(raw, self.example)
        self.assertEqual(loaded, load_inputs(EXAMPLE))
        self.assertEqual(loaded.checksum, load_inputs(EXAMPLE).checksum)

    def test_planning_compatibility_not_b_simulation_truth(self):
        _, inputs = self.reconstruct()
        uniform = evaluate(inputs, baseline(inputs))
        proportional = evaluate(inputs, baseline(inputs, True))
        self.assertEqual((uniform.served, uniform.total), (7, 9))
        self.assertEqual((proportional.served, proportional.total), (6, 9))

    def test_coordinates_and_metadata_do_not_enter_calculation(self):
        network, demand = deepcopy(self.network), deepcopy(self.demand)
        network["coordinates"]["A"]["lat"] = -30.0
        network["metadata"]["extra_note"] = "synthetic note"
        demand["data"]["demands"][0]["extra_note"] = "synthetic note"
        network["data"]["segments"][0]["extra_note"] = "synthetic note"
        raw, _ = self.reconstruct(network, demand)
        self.assertEqual(raw, self.example)
        self.assertNotIn("coordinates", raw)
        self.assertNotIn("metadata", raw)

    def test_reads_new_network_and_demand_instead_of_original_payload(self):
        network, demand, example = deepcopy(self.network), deepcopy(self.demand), deepcopy(self.example)
        network["data"]["segments"][0]["travel_s"] = 61
        demand["data"]["demands"][0]["passenger_count"] = 4
        for field in ("stops", "segments", "allowed_turns", "turnaround_stops", "demands"):
            del example[field]
        raw, loaded = self.reconstruct(network, demand, example)
        self.assertEqual(raw["segments"][0]["travel_s"], 61)
        self.assertEqual(loaded.total_demand, 10)

    def test_missing_fields_fail(self):
        for document, path in (("network", ("data", "stops")),
                               ("network", ("data", "segments", 0, "travel_s")),
                               ("demand", ("data", "demands", 0, "arrival_s")),
                               ("network", ("metadata", "schema_version")),
                               ("demand", ("metadata", "source"))):
            with self.subTest(document=document, path=path):
                n, d = deepcopy(self.network), deepcopy(self.demand)
                parent = n if document == "network" else d
                for field in path[:-1]:
                    parent = parent[field]
                del parent[path[-1]]
                with self.assertRaises(ValueError):
                    self.reconstruct(n, d)

    def test_duplicate_and_invalid_ids_fail(self):
        for target, field in (("stops", None), ("segments", "segment_id"),
                              ("demands", "demand_id")):
            for replacement in ("duplicate", None, 7, " "):
                with self.subTest(target=target, replacement=replacement):
                    n, d = deepcopy(self.network), deepcopy(self.demand)
                    rows = d["data"][target] if target == "demands" else n["data"][target]
                    value = (rows[0][field] if field else rows[0]) if replacement == "duplicate" else replacement
                    if field:
                        rows[1][field] = value
                    else:
                        rows[1] = value
                    with self.assertRaises(ValueError):
                        self.reconstruct(n, d)

    def test_invalid_numeric_fields_fail(self):
        for target, field, bad_values in (
            ("segments", "travel_s", (0, -1, 1.5, True, "60", None)),
            ("segments", "distance_m", (-1, 1.5, True, "300", None)),
            ("demands", "arrival_s", (-1, 1.5, True, "0", None)),
            ("demands", "passenger_count", (-1, 1.5, True, "3", None)),
        ):
            for value in bad_values:
                with self.subTest(target=target, field=field, value=value):
                    n, d = deepcopy(self.network), deepcopy(self.demand)
                    owner = d if target == "demands" else n
                    owner["data"][target][0][field] = value
                    with self.assertRaises(ValueError):
                        self.reconstruct(n, d)

    def test_zero_nonnegative_fields_are_valid(self):
        n, d = deepcopy(self.network), deepcopy(self.demand)
        n["data"]["segments"][0]["distance_m"] = 0
        d["data"]["demands"][0]["passenger_count"] = 0
        raw, _ = self.reconstruct(n, d)
        self.assertEqual(raw["demands"][0]["arrival_s"], 0)

    def test_stop_and_route_reference_failures(self):
        for target, field in (("segments", "origin"), ("segments", "destination"),
                              ("demands", "origin"), ("demands", "destination"),
                              ("demands", "original_route_id")):
            with self.subTest(target=target, field=field):
                n, d = deepcopy(self.network), deepcopy(self.demand)
                owner = d if target == "demands" else n
                owner["data"][target][0][field] = "unknown"
                with self.assertRaises(ValueError):
                    self.reconstruct(n, d)
        d = deepcopy(self.demand)
        d["data"]["demands"][0]["original_route_id"] = "r3"
        with self.assertRaises(ValueError):
            self.reconstruct(demand=d)

    def test_turn_and_turnaround_failures(self):
        for field, value in (("allowed_turns", [["ab", "unknown"]]),
                             ("allowed_turns", [["ab"]]),
                             ("allowed_turns", [["ab", "db"]]),
                             ("turnaround_stops", ["unknown"])):
            with self.subTest(field=field, value=value):
                n = deepcopy(self.network)
                n["data"][field] = value
                with self.assertRaises(ValueError):
                    self.reconstruct(network=n)

    def test_coordinate_ranges_and_types_fail(self):
        for axis, value in (("lat", 91), ("lat", -91), ("lon", 181), ("lon", -181),
                            ("lat", float("nan")), ("lon", float("inf")),
                            ("lat", "37.5"), ("lon", True), ("lat", None)):
            with self.subTest(axis=axis, value=value):
                n = deepcopy(self.network)
                n["coordinates"]["A"][axis] = value
                with self.assertRaises(ValueError):
                    self.reconstruct(network=n)

    def test_coordinate_correspondence_and_crs_failures(self):
        for mode in ("missing_stop", "extra_stop", "missing_axis", "wrong_axis_order"):
            with self.subTest(mode=mode):
                n = deepcopy(self.network)
                if mode == "missing_stop":
                    del n["coordinates"]["A"]
                elif mode == "extra_stop":
                    n["coordinates"]["unknown"] = {"lat": 0, "lon": 0}
                elif mode == "missing_axis":
                    del n["coordinates"]["A"]["lat"]
                else:
                    n["metadata"]["coordinate_reference"]["axis_order"] = ["lon", "lat"]
                with self.assertRaises(ValueError):
                    self.reconstruct(network=n)

    def test_metadata_time_and_version_mismatches_fail(self):
        for path, value in ((("synthetic",), False), (("schema_version",), "unknown"),
                            (("network_version",), "different"),
                            (("time_reference", "timezone"), "UTC"),
                            (("time_reference", "service_date"), "2026-11-08"),
                            (("time_reference", "service_date"), "invalid")):
            with self.subTest(path=path):
                d = deepcopy(self.demand)
                parent = d["metadata"]
                for field in path[:-1]:
                    parent = parent[field]
                parent[path[-1]] = value
                with self.assertRaises(ValueError):
                    self.reconstruct(demand=d)

    def test_cli_output_can_be_loaded_without_overwriting(self):
        with TemporaryDirectory() as directory:
            output = Path(directory) / "inputs.json"
            command = [sys.executable, "-B", "-m", "tests.fixtures.compose", "--output", str(output)]
            result = subprocess.run(command, capture_output=True, text=True,
                                    cwd=REPO_ROOT, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(load_inputs(output), load_inputs(EXAMPLE))
            again = subprocess.run(command, capture_output=True, text=True,
                                   cwd=REPO_ROOT, check=False)
            self.assertNotEqual(again.returncode, 0)
            self.assertEqual(load_inputs(output), load_inputs(EXAMPLE))


if __name__ == "__main__":
    unittest.main()
