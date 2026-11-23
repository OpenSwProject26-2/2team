"""로컬 DEMO 전용: 가상 파업 대상 OD·정상 노선·셔틀 정차 순서와 시간당 정원 배분.

실제 수송 실적·B 시뮬레이션·M07 평가가 아니다. 운행 시간, 환승, 도로,
차량 회전은 모델링하지 않으므로 공개 M08 계약으로 사용하지 않는다.
"""

from __future__ import annotations

import hashlib
import json

from .d_geo import DataError, MapSnapshot

FACTORS = {"06": 0.65, "07": 1.0, "08": 1.24, "09": 0.88, "18": 1.13}
NORMAL_KINDS = {"city", "gyeonggi", "village", "metro"}


def evaluate_demo(snapshot: MapSnapshot, request: dict) -> dict:
    if snapshot.source != "synthetic":
        raise DataError("OUT_OF_SCOPE", "합성 DEMO에서만 수요 배분을 계산합니다.")
    hour = request.get("hour")
    service_date = request.get("date", snapshot.service_date)
    if snapshot.demand and service_date not in snapshot.demand[0]["date_counts"]:
        raise DataError("OUT_OF_SCOPE", "합성 예제에서 지원하지 않는 날짜입니다.", date=service_date)
    if hour not in FACTORS:
        raise DataError("OUT_OF_SCOPE", "합성 예제의 지원 시간대 밖입니다.", hour=hour)
    routes = request.get("routes")
    if not isinstance(routes, list) or len(routes) > 20:
        raise DataError("INPUT_INVALID", "routes는 최대 20개 배열이어야 합니다.")
    normal_ids = request.get("active_normal_route_ids", [])
    normal_by_id = {
        route["route_id"]: route
        for route in snapshot.routes
        if route["kind"] in NORMAL_KINDS
    }
    if (
        not isinstance(normal_ids, list)
        or len(normal_ids) > 20
        or any(not isinstance(route_id, str) for route_id in normal_ids)
        or len(set(normal_ids)) != len(normal_ids)
        or any(route_id not in normal_by_id for route_id in normal_ids)
    ):
        raise DataError("INPUT_INVALID", "가상 정상 노선 ID 목록이 잘못되었습니다.")
    normal_ids = sorted(normal_ids)
    strike_routes = {route["route_id"]: route for route in snapshot.routes if route["kind"] == "strike"}
    strike_by_id = set(strike_routes)
    strike_ids = request.get("strike_route_ids", sorted(strike_by_id))
    if (
        not isinstance(strike_ids, list)
        or len(strike_ids) > 20
        or any(not isinstance(route_id, str) for route_id in strike_ids)
        or len(set(strike_ids)) != len(strike_ids)
        or any(route_id not in strike_by_id for route_id in strike_ids)
    ):
        raise DataError("INPUT_INVALID", "가상 파업 노선 ID 목록이 잘못되었습니다.")
    strike_ids = sorted(strike_ids)
    known_stops = {stop["stop_id"] for stop in snapshot.stops}
    seen: set[str] = set()
    normalized = []
    for route in routes:
        if not isinstance(route, dict):
            raise DataError("INPUT_INVALID", "노선 항목 형식이 잘못되었습니다.")
        route_id = route.get("route_id")
        stop_ids = route.get("stop_ids")
        vehicles = route.get("vehicles")
        if (
            not isinstance(route_id, str)
            or not route_id.startswith("demo-shuttle-")
            or len(route_id) > 64
            or route_id in seen
            or not isinstance(stop_ids, list)
            or not 2 <= len(stop_ids) <= 10
            or len(set(stop_ids)) != len(stop_ids)
            or any(stop not in known_stops for stop in stop_ids)
            or type(vehicles) is not int
            or not 1 <= vehicles <= snapshot.fleet["available"]
        ):
            raise DataError(
                "INPUT_INVALID", "합성 노선 ID·정차 순서·차량 수가 잘못되었습니다."
            )
        seen.add(route_id)
        normalized.append(
            {"route_id": route_id, "stop_ids": stop_ids, "vehicles": vehicles}
        )
    if sum(route["vehicles"] for route in normalized) > snapshot.fleet["available"]:
        raise DataError("PLAN_INFEASIBLE", "가용 차량 수를 초과했습니다.")
    normalized.sort(key=lambda item: item["route_id"])

    cells = []
    for cell in snapshot.demand:
        if cell.get("strike_route_id") not in strike_by_id:
            raise DataError("DATA_INCOMPLETE", "가상 수요의 파업 노선 연결이 없습니다.", cell_id=cell["cell_id"])
        # The same travellers remain in the OD pool when their original bus resumes.
        # Recovery adds usable capacity; it must not erase travellers from total OD.
        total = round(cell["date_counts"][service_date] * FACTORS[hour])
        cells.append({"cell": cell, "total": total, "remaining": total})
    route_effects = []
    active_routes = []
    restored_ids = sorted(strike_by_id - set(strike_ids))
    for route_id in restored_ids:
        route = strike_routes[route_id]
        stop_ids = route.get("stop_ids")
        capacity = route.get("demo_capacity_per_hour")
        if (
            not isinstance(stop_ids, list)
            or not 2 <= len(stop_ids) <= 20
            or len(set(stop_ids)) != len(stop_ids)
            or any(stop_id not in known_stops for stop_id in stop_ids)
            or type(capacity) is not int
            or capacity <= 0
        ):
            raise DataError("DATA_INCOMPLETE", "복구 노선 정차 순서 또는 수송 정원이 없습니다.", route_id=route_id)
        active_routes.append((route_id, "restored_city", stop_ids, capacity))
    for route_id in normal_ids:
        route = normal_by_id[route_id]
        stop_ids = route.get("stop_ids")
        capacity = route.get("demo_capacity_per_hour")
        if (
            not isinstance(stop_ids, list)
            or not 2 <= len(stop_ids) <= 20
            or len(set(stop_ids)) != len(stop_ids)
            or any(stop_id not in known_stops for stop_id in stop_ids)
            or type(capacity) is not int
            or capacity <= 0
        ):
            raise DataError(
                "DATA_INCOMPLETE",
                "가상 정상 노선 정차점·정원이 잘못되었습니다.",
                route_id=route_id,
            )
        active_routes.append((route_id, route["kind"], stop_ids, capacity))
    for route in normalized:
        active_routes.append(
            (
                route["route_id"],
                "shuttle",
                route["stop_ids"],
                route["vehicles"] * snapshot.fleet["capacity_per_vehicle"],
            )
        )
    for route_id, kind, stop_ids, capacity in active_routes:
        order = {stop_id: index for index, stop_id in enumerate(stop_ids)}
        assumed_capacity = capacity
        served = 0
        for item in cells:
            if kind == "restored_city" and item["cell"]["strike_route_id"] != route_id:
                continue
            origin = order.get(item["cell"]["stop_id"])
            destination = order.get(item["cell"]["destination_stop_id"])
            if (
                origin is None
                or destination is None
                or origin >= destination
                or capacity == 0
            ):
                continue
            take = min(item["remaining"], capacity)
            item["remaining"] -= take
            capacity -= take
            served += take
        route_effects.append(
            {
                "route_id": route_id,
                "kind": kind,
                "served": served,
                "assumed_capacity_per_hour": assumed_capacity,
            }
        )
    features = []
    for item in cells:
        cell, total, unmet = item["cell"], item["total"], item["remaining"]
        features.append(
            {
                "type": "Feature",
                "id": cell["cell_id"],
                "geometry": {"type": "Point", "coordinates": cell["coordinates"]},
                "properties": {
                    "cell_id": cell["cell_id"],
                    "stop_id": cell["stop_id"],
                    "total": total,
                    "origin_count": round(total * cell["origin_count"] / cell["base_count"]),
                    "destination_count": total - round(total * cell["origin_count"] / cell["base_count"]),
                    "served": total - unmet,
                    "unmet": unmet,
                    "strike_route_id": cell["strike_route_id"],
                    "source": "synthetic_capacity_demo",
                },
            }
        )
    total = sum(item["total"] for item in cells)
    unmet = sum(item["remaining"] for item in cells)
    canonical = json.dumps(
        {
            "snapshot_id": snapshot.snapshot_id,
            "hour": hour,
            "date": service_date,
            "routes": normalized,
            "active_normal_route_ids": normal_ids,
            "strike_route_ids": strike_ids,
            "restored_strike_route_ids": restored_ids,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "status": "synthetic_capacity_demo",
        "snapshot_id": snapshot.snapshot_id,
        "scenario_fingerprint": hashlib.sha256(canonical.encode()).hexdigest()[:16],
        "hour": hour,
        "date": service_date,
        "total": total,
        "served": total - unmet,
        "unmet": unmet,
        "coverage_rate": round((total - unmet) / total, 4) if total else 0,
        "assigned_vehicles": sum(route["vehicles"] for route in normalized),
        "active_normal_route_ids": normal_ids,
        "strike_route_ids": strike_ids,
        "restored_strike_route_ids": restored_ids,
        "route_effects": route_effects,
        "features": features,
        "limitations": "정상 노선별 가정 시간당 정원·셔틀 차량당 1회 정원만 반영한 가상 OD 배분; 배차·환승·도로·현실 검증 없음",
    }
