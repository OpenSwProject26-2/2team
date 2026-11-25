"""Reproduce D's synthetic allocation with no external data or API keys.

This is a D-only check. It does not run or certify C M05 / B M07.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from busstrike.api.d_geo import load_demo  # noqa: E402
from busstrike.api.d_scenario import evaluate_demo  # noqa: E402


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, help="Optional B/C input or output files to hash (no file contents are copied)")
    args = parser.parse_args()
    fixture = ROOT / "tests/fixtures/d_dashboard/demo.json"
    road = fixture.with_name("road_shapes.json")
    snapshot = load_demo(fixture)
    normal = sorted(r["route_id"] for r in snapshot.routes if r["kind"] in {"city", "gyeonggi", "village", "metro"})
    strike = sorted(r["route_id"] for r in snapshot.routes if r["kind"] == "strike")
    shuttle = [{"route_id": "demo-shuttle-01", "stop_ids": ["d-a", "d-b", "d-c", "d-d"], "vehicles": 5}]
    baseline = {"hour": "07", "date": snapshot.service_date, "routes": shuttle,
                "active_normal_route_ids": normal, "strike_route_ids": strike}
    cases = {
        "baseline": baseline,
        "restore_701": {**baseline, "strike_route_ids": strike[:1]},
        "restore_all": {**baseline, "strike_route_ids": []},
        "no_gyeonggi": {**baseline, "active_normal_route_ids": [r for r in normal if "gyeonggi" not in r]},
        "no_city": {**baseline, "active_normal_route_ids": [r for r in normal if "city" not in r]},
        "no_village": {**baseline, "active_normal_route_ids": [r for r in normal if "village" not in r]},
        "no_metro": {**baseline, "active_normal_route_ids": [r for r in normal if "metro" not in r]},
        "no_shuttle": {**baseline, "routes": []},
        "one_vehicle": {**baseline, "routes": [{**shuttle[0], "vehicles": 1}]},
        "saturday": {**baseline, "date": "2026-11-14"},
    }
    results = {name: evaluate_demo(snapshot, request) for name, request in cases.items()}
    for name, result in results.items():
        assert result["total"] == result["served"] + result["unmet"], name
        assert result["served"] == sum(route["served"] for route in result["route_effects"]), name
        assert result["unmet"] == sum(feature["properties"]["unmet"] for feature in result["features"]), name
        assert all(0 <= f["properties"]["unmet"] <= f["properties"]["total"] for f in result["features"]), name
    base = results["baseline"]
    for name in ("restore_701", "restore_all"):
        assert results[name]["total"] == base["total"]
        assert results[name]["unmet"] < base["unmet"]
    for name in ("no_gyeonggi", "no_city", "no_village", "no_metro", "no_shuttle", "one_vehicle"):
        assert results[name]["total"] == base["total"]
        assert results[name]["unmet"] > base["unmet"]
    assert results["saturday"]["total"] != base["total"]
    summary = {name: {key: value[key] for key in ("date", "total", "served", "unmet", "scenario_fingerprint")}
               for name, value in results.items()}
    output = {"status": "pass", "python": sys.version.split()[0],
              "fixture_sha256": sha256(fixture), "road_shapes_sha256": sha256(road),
              "snapshot_id": snapshot.snapshot_id, "cases": summary}
    if args.manifest:
        paths = json.loads(args.manifest.read_text(encoding="utf-8"))
        if not isinstance(paths, list) or any(not isinstance(path, str) for path in paths):
            raise ValueError("Manifest must be a JSON array of file paths")
        output["external_sha256"] = {Path(path).name: sha256(Path(path)) for path in paths}
        output["external_status"] = "hash_only; B/C models were not executed"
    print(json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
