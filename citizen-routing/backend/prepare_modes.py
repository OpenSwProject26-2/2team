"""Prepare independent normal and strike OTP graph inputs from the same A feed."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .gtfs_adapter import FeedError, coverage_dates, prepare, read_feed, rows
from .mode_service import NORMAL_DIR, STRIKE_DIR


def selected_strike_ids(strike_routes: Path | None, strike_scenario: Path | None) -> tuple[list[str], str]:
    if bool(strike_routes) == bool(strike_scenario):
        raise FeedError("--strike-routes 또는 --strike-scenario 중 하나가 필요합니다.")
    path = strike_routes or strike_scenario
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    key = "affected_route_ids" if strike_routes else "strike_route_ids"
    values = payload.get(key)
    if not isinstance(values, list) or not values or not all(isinstance(x, str) and x for x in values):
        raise FeedError(f"{key}에 실제 A GTFS route_id 목록이 필요합니다.")
    return sorted(set(values)), str(path)


def prepare_both(existing: Path, osm: Path, strike_ids: list[str],
                 normal_dir: Path, strike_dir: Path, shuttle: Path | None = None,
                 shuttle_meta: Path | None = None, scenario_source: str | None = None) -> dict:
    if not strike_ids:
        raise FeedError("파업 대상 A GTFS route_id가 비어 있습니다.")
    meta = None
    if shuttle:
        if not shuttle_meta:
            raise FeedError("셔틀 GTFS에는 --shuttle-meta가 필요합니다. 출처와 검증 상태를 명시하세요.")
        meta = json.loads(shuttle_meta.read_text(encoding="utf-8-sig"))
        for key in ("plan_id", "revision", "network_snapshot_id", "service_date", "source_kind"):
            if not meta.get(key):
                raise FeedError(f"셔틀 메타데이터에 {key}가 없습니다.")
        if meta["source_kind"] not in {"SYNTHETIC_DEMO", "C_TEAM_CANDIDATE"}:
            raise FeedError("셔틀 출처는 SYNTHETIC_DEMO 또는 C_TEAM_CANDIDATE로 명시하세요.")
        if meta["source_kind"] == "C_TEAM_CANDIDATE" and not meta.get("m07", {}).get("pass"):
            raise FeedError("C팀 생성안은 M07 pass=true 결과가 필요합니다.")
        a_first, a_last = coverage_dates(read_feed(existing))
        if not a_first <= meta["service_date"] <= a_last:
            raise FeedError("셔틀 서비스 날짜가 A GTFS 운행 날짜와 다릅니다.")
        feed = read_feed(shuttle)
        feed_info = rows(feed, "feed_info.txt")[1] if "feed_info.txt" in feed else []
        expected = f"{meta['plan_id']}@{meta['revision']}"
        if not feed_info or feed_info[0].get("feed_version") != expected:
            raise FeedError("셔틀 GTFS feed_version과 plan_id@revision이 다릅니다.")
    elif shuttle_meta:
        raise FeedError("--shuttle-meta를 쓰려면 --shuttle도 지정하세요.")
    strike_dir.mkdir(parents=True, exist_ok=True)
    strike_json = strike_dir / "affected-routes.json"
    strike_json.write_text(json.dumps({"affected_route_ids": strike_ids},
                                      ensure_ascii=False, indent=2), encoding="utf-8")
    normal = prepare(existing, None, None, osm, normal_dir)
    strike = prepare(existing, shuttle, strike_json, osm, strike_dir)
    normal.update(mode="normal")
    strike.update(mode="strike", baseline_version=normal["version"],
                  scenario_source=scenario_source, shuttle_source=meta)
    (normal_dir / "manifest.json").write_text(json.dumps(normal, ensure_ascii=False, indent=2), encoding="utf-8")
    (strike_dir / "manifest.json").write_text(json.dumps(strike, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"normal": normal, "strike": strike}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--existing", type=Path, required=True)
    parser.add_argument("--osm", type=Path, required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--strike-routes", type=Path)
    group.add_argument("--strike-scenario", type=Path)
    parser.add_argument("--shuttle", type=Path)
    parser.add_argument("--shuttle-meta", type=Path)
    parser.add_argument("--normal-dir", type=Path, default=NORMAL_DIR)
    parser.add_argument("--strike-dir", type=Path, default=STRIKE_DIR)
    args = parser.parse_args()
    ids, source = selected_strike_ids(args.strike_routes, args.strike_scenario)
    result = prepare_both(args.existing, args.osm, ids, args.normal_dir, args.strike_dir,
                          args.shuttle, args.shuttle_meta, source)
    print(json.dumps({mode: {"version": value["version"],
                             "excluded_routes": len(value["excluded_route_ids"]),
                             "shuttle_routes": len(value["shuttle_route_ids"])}
                      for mode, value in result.items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()