"""파업 범위(시나리오 strike_route_ids) ↔ A GTFS route_id 대응 표를 만든다.

정확한 route_id 일치만 쓴다. 노선 번호·이름으로 추정하지 않는다(맞지 않으면 unmatched).
사용: python -m backend.strike_route_map --scenario scenario.json --gtfs A.zip --out map.csv
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .apply_confirm import strike_route_map, write_route_map
from .gtfs_adapter import read_feed, rows


def build(scenario: Path, gtfs: Path, out: Path) -> dict:
    raw = json.loads(Path(scenario).read_text(encoding="utf-8-sig"))
    ids = raw.get("strike_route_ids") or raw.get("affected_route_ids") or []
    table = strike_route_map(ids, rows(read_feed(Path(gtfs)), "routes.txt")[1])
    write_route_map(Path(out), table)
    return {"scenario_id": raw.get("scenario_id"), "strike_routes": len(set(ids)),
            "matched": len(table["matched"]), "unmatched": len(table["unmatched"]),
            "unmatched_ids": table["unmatched"], "rule": "exact route_id only"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--gtfs", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.scenario, args.gtfs, args.out), ensure_ascii=False))


if __name__ == "__main__":
    main()
