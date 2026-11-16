"""합성 버스 정류장 순서를 OSRM 데모 자동차 도로망에 일회성 매칭한다.

수동 실행 전용. OSRM 데모 서버의 비상업·초당 1요청 제한을 지킨다.
결과는 버스 통행 가능 검증을 뜻하지 않으며 실시간 API로 사용하지 않는다.
"""

from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
from itertools import pairwise
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "tests" / "fixtures" / "d_dashboard" / "demo.json"
OUT = DEMO.with_name("road_shapes.json")
BASE = "https://router.project-osrm.org/route/v1/driving/"
USER_AGENT = "OpenSwProject26-2-2team-D/0.1 (academic local demo; route preview)"


def _distance_m(a: list[float], b: list[float]) -> float:
    lat = math.radians((a[1] + b[1]) / 2)
    east = math.radians(a[0] - b[0]) * math.cos(lat)
    north = math.radians(a[1] - b[1])
    return 6_371_000 * math.hypot(east, north)


def build() -> None:
    demo = json.loads(DEMO.read_text(encoding="utf-8"))
    output = []
    for index, route in enumerate(demo["routes"]):
        if route["kind"] == "metro":
            continue  # 철도에 자동차 도로 경로를 적용하지 않는다.
        if index:
            time.sleep(1.1)
        waypoints = route["coordinates"]
        points = ";".join(f"{lon},{lat}" for lon, lat in waypoints)
        uri = (
            BASE
            + quote(points, safe=";,.")
            + "?overview=full&geometries=geojson&steps=false"
        )
        request = Request(uri, headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=25) as response:
            data = json.load(response)
        if data.get("code") != "Ok" or len(data.get("routes", [])) != 1:
            raise RuntimeError(
                f"OSRM 경로 실패: {route['route_id']} {data.get('code')}"
            )
        matches = data["waypoints"]
        geometry = data["routes"][0]["geometry"]
        if (
            len(matches) != len(waypoints)
            or geometry.get("type") != "LineString"
            or len(geometry.get("coordinates", [])) < 2
            or any(match["distance"] > 120 for match in matches)
        ):
            raise RuntimeError(f"정류장 스냅·경로 선형 검증 실패: {route['route_id']}")
        direct = sum(_distance_m(a, b) for a, b in pairwise(waypoints))
        item = {
            "route_id": route["route_id"],
            "profile": "driving",
            "source_waypoints": waypoints,
            "snapped_waypoints": [match["location"] for match in matches],
            "snap_distances_m": [round(match["distance"], 1) for match in matches],
            "coordinates": geometry["coordinates"],
            "distance_m": round(data["routes"][0]["distance"], 1),
            "duration_s": round(data["routes"][0]["duration"], 1),
            "straight_waypoint_distance_m": round(direct, 1),
        }
        output.append(item)
        print(
            f"{item['route_id']}: {len(item['coordinates'])}점, "
            f"도로 {item['distance_m']}m / 정류장 직선 {item['straight_waypoint_distance_m']}m, "
            f"최대 스냅 {max(item['snap_distances_m'])}m"
        )
    manifest = {
        "schema_version": "d-road-shapes-v1",
        "source": "OSRM public demo / OpenStreetMap car routing estimate",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "service_url": "https://router.project-osrm.org",
        "route_count": len(output),
        "routes": output,
    }
    temporary = OUT.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(OUT)
    print(f"저장: {OUT}")


if __name__ == "__main__":
    build()
