"""D 운영자 지도 개발용 로컬 API 서버.

정식 M08 계약 또는 공개 배포 서버가 아니다. 외부 주소에 바인딩하지 않는다.
"""

from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qs, urlsplit

from .d_geo import (
    DataError,
    MapSnapshot,
    demand_geojson,
    load_a_gtfs,
    load_demo,
    map_geojson,
)
from .d_network import district_options, network_view
from .d_pareto import ParetoStore
from .d_geometry import bus_geometry_view, fetch_rail_gis, load_rail_gis, rail_geometry_view
from .d_scenario import evaluate_demo
from .d_simulation import SelectionStore, SimulationStore
from .d_transit_data import TransitData
from .d_reference import add_reference_metro_stations, load_reference
from .d_transport_demand import TransportDemandStore
from .d_strike_registry import strike_registry_view
from .d_gbis import add_gbis_geometry
from .d_stop_order import longest_trip_stop_ids

ROOT = Path(__file__).resolve().parents[3]
DEMO = ROOT / "tests" / "fixtures" / "d_dashboard" / "demo.json"
WEB = ROOT / "web" / "operator"


class Store:
    def __init__(
        self,
        a_gtfs: Path | None = None,
        service_date: str = "2026-11-02",
        transit_data: Path | None = None,
        rail_gis_file: Path | None = None,
        rail_gis_live: bool = False,
        bus_geometry_file: Path | None = None,
        transport_demand_file: Path | None = None,
        gyeonggi_geometry_file: Path | None = None,
    ) -> None:
        self.lock = Lock()
        self.demo = load_demo(DEMO)
        self.a_gtfs = a_gtfs
        self.service_date = service_date
        self.rail_gis_file = rail_gis_file
        self.rail_gis_live = rail_gis_live
        self.bus_geometry_file = bus_geometry_file
        self.gyeonggi_geometry_file = gyeonggi_geometry_file
        self.gyeonggi_geometry_error: str | None = None
        self.rail_gis_error: str | None = None
        self.bus_geometry_error: str | None = None
        self.real: MapSnapshot | None = None
        self.last_error: dict | None = None
        self.network_cache: dict[tuple[str, str, str], dict] = {}
        self.transit = TransitData(transit_data)
        self.transport_demand = TransportDemandStore(transport_demand_file)
        if a_gtfs:
            self.refresh()
        elif transit_data and transit_data.is_dir():
            self.refresh()

    def refresh(self) -> dict:
        if self.a_gtfs is None:
            if self.transit.root and self.transit.root.is_dir():
                candidate = load_reference(self.transit.root)
                if self.bus_geometry_file:
                    try:
                        bus_data = json.loads(self.bus_geometry_file.read_text(encoding="utf-8"))
                        candidate = bus_geometry_view(candidate, bus_data)
                        self.bus_geometry_error = None
                    except (OSError, ValueError, DataError) as error:
                        self.bus_geometry_error = str(error)
                with self.lock:
                    self.real = candidate
                    self.network_cache.clear()
                return {"snapshot_id": candidate.snapshot_id, "source": candidate.source,
                        "route_count": len(candidate.routes), "stop_count": len(candidate.stops)}
            raise DataError("DATA_INCOMPLETE", "서울 참조 자료 또는 A GTFS 경로가 설정되지 않았습니다.")
        try:
            candidate = load_a_gtfs(self.a_gtfs, self.service_date)
            if self.rail_gis_file or self.rail_gis_live:
                try:
                    rail_data = load_rail_gis(self.rail_gis_file) if self.rail_gis_file else fetch_rail_gis()
                    candidate = rail_geometry_view(candidate, rail_data)
                    self.rail_gis_error = None
                except (OSError, ValueError, DataError) as error:
                    self.rail_gis_error = str(error)
            if self.bus_geometry_file:
                try:
                    bus_data = json.loads(self.bus_geometry_file.read_text(encoding="utf-8"))
                    candidate = bus_geometry_view(candidate, bus_data)
                    self.bus_geometry_error = None
                except (OSError, ValueError, DataError) as error:
                    self.bus_geometry_error = str(error)
            candidate = add_reference_metro_stations(candidate, self.transit.root)
            if self.gyeonggi_geometry_file:
                try:
                    candidate = add_gbis_geometry(candidate, self.gyeonggi_geometry_file)
                    self.gyeonggi_geometry_error = None
                except (OSError, ValueError, DataError) as error:
                    self.gyeonggi_geometry_error = str(error)
        except DataError as error:
            self.last_error = {
                "code": error.code,
                "message": str(error),
                "details": error.details,
            }
            raise
        with self.lock:
            self.real = candidate
            self.last_error = None
            self.network_cache.clear()
        return {
            "snapshot_id": candidate.snapshot_id,
            "source": candidate.source,
            "route_count": len(candidate.routes),
            "stop_count": len(candidate.stops),
        }

    def get(self, mode: str) -> MapSnapshot:
        if mode == "demo":
            return self.demo
        if mode == "real":
            with self.lock:
                snapshot = self.real
            if snapshot is None:
                raise DataError(
                    "DATA_INCOMPLETE",
                    "유효한 A GTFS 스냅샷이 없습니다.",
                    last_error=self.last_error,
                )
            return snapshot
        raise DataError(
            "INPUT_INVALID", "mode는 demo 또는 real이어야 합니다.", mode=mode
        )

    def sources(self) -> dict:
        candidate_path = (self.transit.root / "derived" / "gyeonggi_2xx" /
                          "gyeonggi_candidates_manifest.json") if self.transit.root and self.transit.root.is_dir() else None
        try:
            candidate_summary = json.loads(candidate_path.read_text(encoding="utf-8")) if candidate_path and candidate_path.is_file() else None
        except (OSError, ValueError):
            candidate_summary = None
        return {
            "demo": {"status": "ready", "snapshot_id": self.demo.snapshot_id},
            "A": {
                "status": "ready" if self.real and self.real.source.startswith("a_gtfs") else "unavailable",
                "snapshot_id": self.real.snapshot_id if self.real and self.real.source.startswith("a_gtfs") else None,
                "last_error": self.last_error,
            },
            "reference": {"status": "ready" if self.real and self.real.source == "reference" else "unavailable",
                          "snapshot_id": self.real.snapshot_id if self.real and self.real.source == "reference" else None},
            "gyeonggi_candidates": {"status": "inventory_only" if candidate_summary else "unavailable",
                                    "route_count": candidate_summary.get("route_count", 0) if candidate_summary else 0,
                                    "official_geometry_count": 0,
                                    "classification": "TPSS 2xx candidates; jurisdiction unverified"},
            "rail_gis": {
                "status": "ready" if self.real and any(route.get("geometry_status") == "rail_gis_verified_corridor" for route in self.real.routes) else "unavailable",
                "matched_routes": sum(route.get("geometry_status") == "rail_gis_verified_corridor" for route in self.real.routes) if self.real else 0,
                "last_error": self.rail_gis_error,
            },
            "bus_geometry": {
                "status": "ready" if self.real and any(route.get("kind") != "gyeonggi" and route.get("geometry_status") in {"official", "osm_route", "osrm_estimate", "roadnet_estimate", "roadnet_partial_estimate", "roadnet_pattern_estimate"} for route in self.real.routes) else "unavailable",
                "matched_routes": sum(route.get("kind") != "gyeonggi" and route.get("geometry_status") in {"official", "osm_route", "osrm_estimate", "roadnet_estimate", "roadnet_partial_estimate", "roadnet_pattern_estimate"} for route in self.real.routes) if self.real else 0,
                "official_routes": sum(route.get("kind") != "gyeonggi" and route.get("geometry_status") == "official" for route in self.real.routes) if self.real else 0,
                "osm_routes": sum(route.get("kind") != "gyeonggi" and route.get("geometry_status") == "osm_route" for route in self.real.routes) if self.real else 0,
                "osrm_estimates": sum(route.get("kind") != "gyeonggi" and route.get("geometry_status") == "osrm_estimate" for route in self.real.routes) if self.real else 0,
                "roadnet_estimates": sum(route.get("kind") != "gyeonggi" and route.get("geometry_status") == "roadnet_estimate" for route in self.real.routes) if self.real else 0,
                "roadnet_partial_estimates": sum(route.get("kind") != "gyeonggi" and route.get("geometry_status") == "roadnet_partial_estimate" for route in self.real.routes) if self.real else 0,
                "roadnet_pattern_estimates": sum(route.get("kind") != "gyeonggi" and route.get("geometry_status") == "roadnet_pattern_estimate" for route in self.real.routes) if self.real else 0,
                "last_error": self.bus_geometry_error,
            },
            "gyeonggi_geometry": {
                "status": "ready" if self.real and any(route.get("kind") == "gyeonggi" for route in self.real.routes) else "unavailable",
                "matched_routes": sum(route.get("kind") == "gyeonggi" for route in self.real.routes) if self.real else 0,
                "last_error": self.gyeonggi_geometry_error,
            },
            "B": {
                "status": "unavailable",
                "reason": "검증된 셔틀 시간표 산출물 미연결",
            },
            "C": {"status": "unavailable", "reason": "후보안 산출물 미연결"},
            "M07": {"status": "unavailable", "reason": "완전 독립 검증 산출물 미연결"},
            "transport_demand": {"status": "ready" if self.transport_demand.path else "unavailable",
                                 "scenario_count": len(self.transport_demand.scenarios)},
        }

    def network(self, snapshot: MapSnapshot, district: str, extent: str, compact: bool = False) -> dict:
        if snapshot.source == "synthetic" and district != "all":
            raise DataError("OUT_OF_SCOPE", "자치구 필터에는 A GTFS가 필요합니다.")
        key = (snapshot.snapshot_id, district, extent, compact)
        with self.lock:
            cached = self.network_cache.get(key)
        if cached is not None:
            return cached
        view = network_view(snapshot, district, extent, include_route_stops=not compact)
        with self.lock:
            self.network_cache[key] = view
        return view


LAB_PANEL_FILES = {"pareto_panel.js": "text/javascript; charset=utf-8", "pareto_panel.css": "text/css; charset=utf-8",
                   "plan_layer.js": "text/javascript; charset=utf-8"}


def make_handler(store: Store, simulation: SimulationStore | None = None, lab=None, pareto=None):
    """`lab`(routegen_lab.Lab/LabSet)을 주면 /lab, /api/lab/* 를 함께 처리한다.
    `pareto`(d_pareto.ParetoStore)를 주면 충족률·형평성 곡선 패널 자료(/api/d/simulation/pareto*)를 준다."""
    simulation = simulation or SimulationStore()
    pareto = pareto or ParetoStore(None)
    selections = SelectionStore()

    class Handler(BaseHTTPRequestHandler):
        def _json(self, data: dict | list, status: int = HTTPStatus.OK) -> None:
            payload = json.dumps(
                data, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _error(self, error: DataError) -> None:
            status = (
                HTTPStatus.BAD_REQUEST
                if error.code in {"INPUT_INVALID", "OUT_OF_SCOPE"}
                else HTTPStatus.CONFLICT
                if error.code in {"DATA_VERSION_MISMATCH", "PLAN_INFEASIBLE"}
                else HTTPStatus.SERVICE_UNAVAILABLE
            )
            self._json(
                {
                    "code": error.code,
                    "message": str(error),
                    "details": error.details,
                    "request_id": None,
                },
                status,
            )

        def _asset(self, file: Path, content_type: str) -> None:
            try:
                payload = file.read_bytes()
            except OSError:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _lab_get(self, path: str, query: str = "") -> bool:
            from .routegen_lab import LabError, handle_get
            if lab is None:
                if path == "/lab" or path.startswith("/api/lab/"):
                    self._json({"code": "DATA_INCOMPLETE", "message": "실험실 데이터가 없다(--lab-dir)."},
                               HTTPStatus.SERVICE_UNAVAILABLE)
                    return True
                return False
            try:
                got = handle_get(lab, path, query)
            except (LabError, OSError) as error:
                self._json({"code": getattr(error, "code", "DATA_INCOMPLETE"), "message": str(error)},
                           HTTPStatus.BAD_REQUEST)
                return True
            if got is None:
                return False
            status, ctype, body = got
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return True

        def do_GET(self) -> None:
            parts = urlsplit(self.path)
            if self._lab_get(parts.path, parts.query):
                return
            if parts.path in {"/", "/operator", "/operator/"}:
                return self._asset(WEB / "index.html", "text/html; charset=utf-8")
            if parts.path in {"/simulation", "/simulation/"}:
                return self._asset(WEB / "index.html", "text/html; charset=utf-8")
            if parts.path == "/simulation.js":
                return self._asset(
                    WEB / "simulation.js", "text/javascript; charset=utf-8"
                )
            if parts.path == "/app.js":
                return self._asset(WEB / "app.js", "text/javascript; charset=utf-8")
            if parts.path == "/demand-circles.js":
                return self._asset(WEB / "demand-circles.js", "text/javascript; charset=utf-8")
            if parts.path == "/overlap-layout.js":
                return self._asset(WEB / "overlap-layout.js", "text/javascript; charset=utf-8")
            vendor_assets = {
                "/vendor/maplibre-gl.mjs": "text/javascript; charset=utf-8",
                "/vendor/maplibre-gl-worker.mjs": "text/javascript; charset=utf-8",
                "/vendor/maplibre-gl.css": "text/css; charset=utf-8",
                "/vendor/openfreemap-liberty.json": "application/json; charset=utf-8",
                "/data/districts_all.geojson": "application/geo+json; charset=utf-8",
            }
            if parts.path in vendor_assets:
                asset = (
                    WEB / "data" / "districts_all.geojson"
                    if parts.path.startswith("/data/")
                    else WEB / "vendor" / parts.path.rsplit("/", 1)[-1]
                )
                return self._asset(
                    asset, vendor_assets[parts.path]
                )
            if parts.path.startswith("/lab-panel/") and parts.path[11:] in LAB_PANEL_FILES:
                name = parts.path[11:]
                return self._asset(WEB.parent / "lab" / name, LAB_PANEL_FILES[name])
            if parts.path == "/favicon.ico":
                self.send_response(HTTPStatus.NO_CONTENT)
                self.end_headers()
                return
            if parts.path == "/health":
                return self._json({"status": "ok", "sources": store.sources()})
            query = parse_qs(parts.query)
            mode = query.get("mode", ["demo"])[0]
            try:
                if parts.path == "/api/d/strike-registry":
                    snapshot = store.get("real")
                    return self._json(strike_registry_view(
                        store.transit.root, snapshot, query.get("district", ["all"])[0]
                    ))
                if parts.path == "/api/d/route-geometry":
                    snapshot = store.get(mode)
                    route_id = query.get("route_id", [""])[0]
                    district = query.get("district", ["all"])[0]
                    if not route_id:
                        raise DataError("INPUT_INVALID", "route_id is required")
                    scoped_ids = {route["route_id"] for route in store.network(snapshot, district, "inside", compact=True)["routes"]}
                    route = next((item for item in snapshot.routes if item["route_id"] == route_id and route_id in scoped_ids), None)
                    if route is None:
                        raise DataError("INPUT_INVALID", "Route is not available in the selected district")
                    coordinates = route.get("coordinates")
                    geometry = ({"type": route.get("geometry_type") or "LineString", "coordinates": coordinates}
                                if coordinates else None)
                    stops_by_id = {stop["stop_id"]: stop for stop in snapshot.stops}
                    ordered_ids = (longest_trip_stop_ids(store.a_gtfs, store.service_date, route_id)
                                   if mode == "real" and store.a_gtfs and not route_id.startswith("gbis:") else ())
                    if not ordered_ids:
                        ordered_ids = tuple(route.get("all_stop_ids") or []) if store.a_gtfs is None else tuple(route.get("representative_stop_ids") or route.get("stop_ids") or [])
                    ordered_stops = [
                        {"stop_id": stop_id, "name": stops_by_id[stop_id]["name"],
                         "coordinates": stops_by_id[stop_id]["coordinates"], "order": order}
                        for order, stop_id in enumerate(ordered_ids, 1)
                        if stop_id in stops_by_id
                    ]
                    return self._json({"snapshot_id": snapshot.snapshot_id, "route_id": route_id,
                                       "geometry": geometry, "geometry_status": route.get("geometry_status"),
                                        "pattern_indices": route.get("pattern_indices"),
                                        "kind": route["kind"], "ordered_stops": ordered_stops})
                if parts.path == "/api/d/simulation/options":
                    return self._json(simulation.options())
                if parts.path == "/api/d/simulation/runs":
                    return self._json(simulation.runs(query))
                if parts.path == "/api/d/simulation/comparison":
                    return self._json(simulation.comparison(query))
                if parts.path == "/api/d/simulation/table":
                    return self._json(simulation.comparison_table(query))
                if parts.path == "/api/d/simulation/seoul":
                    return self._json(
                        simulation.seoul_summary(query, selections.read()["selected"])
                    )
                if parts.path == "/api/d/simulation/selection":
                    return self._json(selections.read())
                if parts.path == "/api/d/simulation/residual":
                    return self._json(
                        store.transit.residual(
                            query.get("region", ["guro"])[0],
                            query.get("variant", ["affected"])[0],
                            query.get("period", ["exp"])[0],
                            query.get("date", ["mean"])[0],
                        )
                    )
                if parts.path == "/api/d/simulation/absorb-demand":
                    return self._json(simulation.absorb_demand(query))
                if parts.path == "/api/d/simulation/bus-roads":
                    return self._json(simulation.bus_roads(query))
                if parts.path == "/api/d/simulation/transit-routes":
                    return self._json(simulation.transit_routes(query))
                if parts.path == "/api/d/simulation/pareto":
                    return self._json(pareto.pareto(query.get("region", [""])[0]))
                if parts.path == "/api/d/simulation/pareto-export":
                    return self._json(pareto.export(query.get("region", [""])[0], query.get("id", [""])[0]))
                if parts.path == "/api/d/simulation/transport-demand":
                    return self._json(store.transport_demand.get(query))
                if parts.path.startswith("/api/d/simulation/"):
                    selected = simulation.selection(query)
                    field = {
                        "candidates": "candidates",
                        "routes": "routes",
                        "allocations": "routes",
                        "map": "geojson",
                        "metrics": "metrics",
                        "verification": "verification",
                    }.get(parts.path.rsplit("/", 1)[-1])
                    if parts.path == "/api/d/simulation/result":
                        return self._json(selected)
                    if field:
                        name = parts.path.rsplit("/", 1)[-1]
                        return self._json(
                            {
                                "selection": selected["selection"],
                                name: selected[field],
                                "plan_id": selected["plan_id"],
                                "revision": selected["revision"],
                                "network_snapshot_id": selected["network_snapshot_id"],
                                "outcome": selected["outcome"],
                            }
                        )
                if parts.path == "/api/d/sources":
                    return self._json(store.sources())
                if parts.path == "/api/d/districts":
                    return self._json(district_options())
                if parts.path == "/api/d/transit/status":
                    return self._json(store.transit.status(store.real))
                if parts.path == "/api/d/transit/observed":
                    return self._json(
                        store.transit.observed(
                            query.get("district", ["guro"])[0],
                            query.get("date", ["2026-10-26"])[0],
                            query.get("hour", ["07"])[0],
                        )
                    )
                if parts.path == "/api/d/transit/real-demand":
                    snapshot = store.get("real")
                    route_ids = tuple(filter(None, query.get("strike_route_ids", [""])[0].split(",")))
                    if len(route_ids) > 250:
                        raise DataError("INPUT_INVALID", "파업 노선을 250개 이하로 선택하세요.")
                    return self._json(store.transit.real_demand(
                        snapshot,
                        query.get("district", ["all"])[0],
                        query.get("date", ["2026-10-26"])[0],
                        query.get("hour", ["07"])[0],
                        route_ids,
                        tuple(filter(None, query.get("disabled_modes", [""])[0].split(","))),
                    ))
                snapshot = store.get(mode)
                district = query.get("district", ["all"])[0]
                extent = query.get("extent", ["inside"])[0]
                view = store.network(snapshot, district, extent, compact=query.get("compact", ["0"])[0] == "1")
                if parts.path == "/api/d/network":
                    return self._json({"snapshot_id": snapshot.snapshot_id, **view})
                if parts.path == "/api/d/dashboard":
                    return self._json(snapshot.summary())
                if parts.path == "/api/d/routes":
                    kind = query.get("kind", [None])[0]
                    q = query.get("q", [""])[0].casefold()
                    routes = [
                        route
                        for route in view["routes"]
                        if (kind is None or route["kind"] == kind)
                        and (
                            not q
                            or q in route["short_name"].casefold()
                            or q in route["agency"].casefold()
                        )
                    ]
                    return self._json(
                        {
                            "snapshot_id": snapshot.snapshot_id,
                            "source": snapshot.source,
                            "routes": routes,
                        }
                    )
                if parts.path == "/api/d/map":
                    bbox = None
                    if "bbox" in query:
                        try:
                            vals = tuple(map(float, query["bbox"][0].split(",")))
                        except ValueError as error:
                            raise DataError(
                                "INPUT_INVALID",
                                "bbox는 west,south,east,north 형식이어야 합니다.",
                            ) from error
                        if len(vals) != 4 or vals[0] > vals[2] or vals[1] > vals[3]:
                            raise DataError(
                                "INPUT_INVALID", "bbox 범위가 잘못되었습니다."
                            )
                        bbox = vals
                    if district == "all":
                        return self._json(map_geojson(snapshot, bbox))
                    geo = view["map"]
                    if bbox:
                        geo = {
                            **geo,
                            "features": [
                                f
                                for f in geo["features"]
                                if f["geometry"]["type"] != "Point"
                                or (
                                    bbox[0]
                                    <= f["geometry"]["coordinates"][0]
                                    <= bbox[2]
                                    and bbox[1]
                                    <= f["geometry"]["coordinates"][1]
                                    <= bbox[3]
                                )
                            ],
                        }
                    return self._json(geo)
                if parts.path == "/api/d/demand":
                    hour = query.get("hour", ["07"])[0]
                    demand_mode = query.get("demand_mode", ["total"])[0]
                    return self._json(demand_geojson(snapshot, hour, demand_mode, query.get("date", [None])[0]))
                self.send_error(HTTPStatus.NOT_FOUND)
            except DataError as error:
                self._error(error)

        def do_POST(self) -> None:
            path = urlsplit(self.path).path
            is_lab = path in {"/api/lab/generate", "/api/lab/simulate"}
            if path not in {
                "/api/d/sources/refresh",
                "/api/d/demo/scenario/evaluate",
                "/api/d/simulation/selection",
            } and not is_lab:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            if self.headers.get("Origin") not in {
                None,
                f"http://127.0.0.1:{self.server.server_port}",
                f"http://localhost:{self.server.server_port}",
            }:
                return self._error(
                    DataError(
                        "INPUT_INVALID", "로컬 개발 서버에서만 새로고침할 수 있습니다."
                    )
                )
            try:
                if path == "/api/d/sources/refresh":
                    return self._json(store.refresh())
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                except ValueError as error:
                    raise DataError(
                        "INPUT_INVALID", "요청 본문 크기가 잘못되었습니다."
                    ) from error
                if not 0 < size <= (262_144 if is_lab else 16_384):
                    raise DataError("INPUT_INVALID", "요청 본문 크기가 잘못되었습니다.")
                try:
                    request = json.loads(self.rfile.read(size))
                except (ValueError, UnicodeDecodeError) as error:
                    raise DataError(
                        "INPUT_INVALID", "JSON 본문이 잘못되었습니다."
                    ) from error
                if not isinstance(request, dict):
                    raise DataError("INPUT_INVALID", "JSON 객체가 필요합니다.")
                if path == "/api/d/simulation/selection":
                    return self._json(selections.choose(simulation, request))
                if is_lab:
                    from .routegen_lab import LabError, handle_post
                    if lab is None:
                        raise DataError("DATA_INCOMPLETE", "실험실 데이터가 없다(--lab-dir).")
                    try:
                        return self._json(handle_post(lab, path, request))
                    except LabError as error:
                        raise DataError(error.code, str(error)) from error
                return self._json(evaluate_demo(store.demo, request))
            except DataError as error:
                self._error(error)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="D 운영자 지도 개발용 로컬 서버")
    parser.add_argument(
        "--a-gtfs", type=Path, help="A 배포 GTFS 또는 중첩 ZIP의 로컬 경로"
    )
    parser.add_argument(
        "--service-date", default="2026-11-02", help="A GTFS 서비스일 YYYY-MM-DD"
    )
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--gyeonggi-geometry-file", type=Path,
                        help="Validated official GBIS GeoJSON outside the repository")
    parser.add_argument("--rail-gis-file", type=Path, help="외부 로컬 Esri 철도 선형 GeoJSON")
    parser.add_argument("--rail-gis-live", action="store_true", help="시작할 때 공식 철도 GIS API를 조회")
    parser.add_argument("--bus-geometry-file", type=Path, help="현재 A GTFS 또는 서울 참조 노선 ID 검증이 포함된 외부 버스 선형 GeoJSON")
    parser.add_argument(
        "--transit-data", type=Path, help="seoul_transit_data 폴더 또는 ZIP"
    )
    parser.add_argument(
        "--simulation-results", type=Path, help="C/B팀 결과 루트(<구>/manifest.json)"
    )
    parser.add_argument(
        "--simulation-inputs",
        type=Path,
        help="C팀 지역 입력 루트(<구>_0610_v4/network/stops.csv)",
    )
    parser.add_argument("--transport-demand", type=Path,
                        help="교통수단 제외 조건별 정류장 수요 JSON(d-transport-demand/1), 저장소 밖 파일")
    parser.add_argument("--lab-layers", type=Path,
                        help="노선 실험실 구별 레이어 폴더(<구>/layers). 시뮬레이션 화면이 결과 없는 구도 lab 지도(흡수 수요·마을/경기/파업 노선·버스 도로)로 그린다")
    parser.add_argument("--pareto-root", type=Path,
                        help="구별 파이프라인 출력 폴더들의 부모(<구>/pareto.json, <구>/export/*.json). 시뮬레이션 화면 왼쪽 아래 충족률·형평성 곡선")
    parser.add_argument("--lab-dir", type=Path, help="routegen 실험실 데이터 폴더(build_lab_data.py 출력)")
    parser.add_argument("--lab-region", type=Path, help="구 묶음을 푼 region 폴더(<구>_0610_v4가 들어 있음)")
    parser.add_argument("--lab-all", type=Path, help="구별 실험실 폴더들의 부모(<slug>/layers/meta.json, build_lab_data --sweep 출력)")
    args = parser.parse_args()
    store = Store(args.a_gtfs, args.service_date, args.transit_data, args.rail_gis_file,
                  args.rail_gis_live, args.bus_geometry_file, args.transport_demand,
                  args.gyeonggi_geometry_file)
    lab = None
    if args.lab_all:
        from .routegen_lab import LabSet
        lab = LabSet.scan(args.lab_all)
    elif args.lab_dir:
        from .routegen_lab import Lab
        if not args.lab_region:
            parser.error("--lab-dir에는 --lab-region이 필요하다")
        lab = Lab(args.lab_dir, args.lab_region)
    server = ThreadingHTTPServer(
        ("127.0.0.1", args.port),
        make_handler(
            store, SimulationStore(args.simulation_results, args.simulation_inputs, args.lab_layers), lab=lab,
            pareto=ParetoStore(args.pareto_root),
        ),
    )
    print(f"D 대시보드: http://127.0.0.1:{args.port}/")
    if lab:
        print(f"노선 실험실: http://127.0.0.1:{args.port}/lab")
    print("DEMO는 합성 자료입니다. 실제 운행 안내가 아닙니다.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
