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
from .d_scenario import evaluate_demo

ROOT = Path(__file__).resolve().parents[3]
DEMO = ROOT / "tests" / "fixtures" / "d_dashboard" / "demo.json"
WEB = ROOT / "web" / "operator"


class Store:
    def __init__(
        self, a_gtfs: Path | None = None, service_date: str = "2026-11-02"
    ) -> None:
        self.lock = Lock()
        self.demo = load_demo(DEMO)
        self.a_gtfs = a_gtfs
        self.service_date = service_date
        self.real: MapSnapshot | None = None
        self.last_error: dict | None = None
        if a_gtfs:
            self.refresh()

    def refresh(self) -> dict:
        if self.a_gtfs is None:
            raise DataError("DATA_INCOMPLETE", "A GTFS 경로가 설정되지 않았습니다.")
        try:
            candidate = load_a_gtfs(self.a_gtfs, self.service_date)
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
        return {
            "demo": {"status": "ready", "snapshot_id": self.demo.snapshot_id},
            "A": {
                "status": "ready" if self.real else "unavailable",
                "snapshot_id": self.real.snapshot_id if self.real else None,
                "last_error": self.last_error,
            },
            "B": {
                "status": "unavailable",
                "reason": "검증된 셔틀 시간표 산출물 미연결",
            },
            "C": {"status": "unavailable", "reason": "후보안 산출물 미연결"},
            "M07": {"status": "unavailable", "reason": "완전 독립 검증 산출물 미연결"},
        }


def make_handler(store: Store):
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
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:
            parts = urlsplit(self.path)
            if parts.path in {"/", "/operator", "/operator/"}:
                return self._asset(WEB / "index.html", "text/html; charset=utf-8")
            if parts.path == "/app.js":
                return self._asset(WEB / "app.js", "text/javascript; charset=utf-8")
            if parts.path == "/favicon.ico":
                self.send_response(HTTPStatus.NO_CONTENT)
                self.end_headers()
                return
            if parts.path == "/health":
                return self._json({"status": "ok", "sources": store.sources()})
            query = parse_qs(parts.query)
            mode = query.get("mode", ["demo"])[0]
            try:
                if parts.path == "/api/d/sources":
                    return self._json(store.sources())
                snapshot = store.get(mode)
                if parts.path == "/api/d/dashboard":
                    return self._json(snapshot.summary())
                if parts.path == "/api/d/routes":
                    kind = query.get("kind", [None])[0]
                    q = query.get("q", [""])[0].casefold()
                    routes = [
                        route
                        for route in snapshot.routes
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
                    return self._json(map_geojson(snapshot, bbox))
                if parts.path == "/api/d/demand":
                    hour = query.get("hour", ["07"])[0]
                    demand_mode = query.get("demand_mode", ["total"])[0]
                    return self._json(demand_geojson(snapshot, hour, demand_mode))
                self.send_error(HTTPStatus.NOT_FOUND)
            except DataError as error:
                self._error(error)

        def do_POST(self) -> None:
            path = urlsplit(self.path).path
            if path not in {"/api/d/sources/refresh", "/api/d/demo/scenario/evaluate"}:
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
                if not 0 < size <= 16_384:
                    raise DataError("INPUT_INVALID", "요청 본문 크기가 잘못되었습니다.")
                try:
                    request = json.loads(self.rfile.read(size))
                except (ValueError, UnicodeDecodeError) as error:
                    raise DataError(
                        "INPUT_INVALID", "JSON 본문이 잘못되었습니다."
                    ) from error
                if not isinstance(request, dict):
                    raise DataError("INPUT_INVALID", "JSON 객체가 필요합니다.")
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
    args = parser.parse_args()
    store = Store(args.a_gtfs, args.service_date)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(store))
    print(f"D 대시보드: http://127.0.0.1:{args.port}/")
    print("DEMO는 합성 자료입니다. 실제 운행 안내가 아닙니다.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
