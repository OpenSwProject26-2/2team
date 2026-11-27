"""Independent localhost citizen routing server."""
from __future__ import annotations

import argparse
import json
import mimetypes
from math import isfinite
import sqlite3
import sys
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .gtfs_adapter import rows
from .bus_categories import load_categories
from .citizen_map_style import citizen_style
from .otp_client import OTPError, healthy
from .place_search import compact, nearby as nearby_places, search as search_places, visible as visible_places
from .route_service import InputError
from .mode_service import (NORMAL_DIR, NORMAL_URL, STRIKE_DIR, STRIKE_URL, compare_routes,
                           compare_three, mode_status, read_manifest, route_for_mode)
from . import apply_confirm

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "frontend"
RUNTIME = ROOT / "runtime"
MAX_BODY = 4096
CONFIRM_FIELDS = ("confirm_id", "algo_name", "confirmed_at", "plan_id", "revision", "plan_sha256", "plan_key",
                  "shuttle_version", "graph_version", "m07", "service_date", "build_id", "activated_at")


def local_origin(origin: str | None) -> str | None:
    if not origin:
        return None
    parts = urlsplit(origin)
    return origin if parts.scheme == "http" and parts.hostname in {"127.0.0.1", "localhost"} else None


def load_stops(otp_dir: Path) -> list[dict]:
    stops = {}
    for filename, kind in (("existing.gtfs.zip", "normal"), ("shuttle.gtfs.zip", "shuttle")):
        path = otp_dir / filename
        if not path.exists():
            continue
        with zipfile.ZipFile(path) as feed:
            _, values = rows({"stops.txt": feed.read("stops.txt")}, "stops.txt")
        for row in values:
            try:
                stops[(kind, row["stop_id"])] = {
                    "id": row["stop_id"], "name": row["stop_name"],
                    "lat": float(row["stop_lat"]), "lon": float(row["stop_lon"]), "kind": kind
                }
            except (ValueError, KeyError):
                continue
    return list(stops.values())


def handler(otp_dir: Path, endpoint: str, strike_dir: Path = STRIKE_DIR,
            strike_endpoint: str = STRIKE_URL, runtime: Path | None = None,
            apply_config: Path | None = None):
    stops_cache = None

    def strike_target() -> tuple[Path, str]:
        """운영자 확정 반영으로 바뀐 파업 그래프(strike-active.json)가 있으면 그것을 쓴다."""
        active = apply_confirm.read_active(runtime) if runtime else None
        if active and Path(active.get("dir", "")).is_dir() and active.get("url"):
            return Path(active["dir"]), active["url"]
        return strike_dir, strike_endpoint

    def confirm_summary() -> dict | None:
        active = apply_confirm.read_active(runtime) if runtime else None
        return {k: active.get(k) for k in CONFIRM_FIELDS} if active else None

    def apply_state() -> dict:
        status = (apply_confirm.read_status(runtime) if runtime else None) or {"state": "idle", "steps": []}
        if status.get("state") == "running" and not apply_confirm.pid_alive(int(status.get("pid") or 0)):
            status = {**status, "state": "interrupted",
                      "error": {"code": "INTERRUPTED", "message": "반영 작업이 중간에 멈췄습니다. 다시 반영하세요(같은 입력은 빌드를 다시 씀)."}}
        return status

    def cached_stops():
        nonlocal stops_cache
        if stops_cache is None:
            stops_cache = load_stops(otp_dir)
        return stops_cache

    class CitizenHandler(BaseHTTPRequestHandler):
        def _json(self, status: int, payload: dict | list) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            origin = local_origin(self.headers.get("Origin"))
            if origin:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self) -> None:
            origin = local_origin(self.headers.get("Origin"))
            self.send_response(204 if origin else 403)
            if origin:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise InputError("요청 본문이 너무 큽니다.")
            raw = self.rfile.read(length) if length else b""
            payload = json.loads(raw.decode("utf-8")) if raw.strip() else {}
            if not isinstance(payload, dict):
                raise InputError("요청 본문은 JSON 객체여야 합니다.")
            return payload

        def do_POST(self) -> None:
            parsed = urlsplit(self.path)
            if not parsed.path.startswith("/api/citizen-routing/apply"):
                self.send_error(404)
                return
            try:
                body = self._body()
            except (InputError, ValueError, UnicodeError) as error:
                self._json(400, {"code": "INPUT_INVALID", "message": str(error)})
                return
            if not runtime or not apply_config or not Path(apply_config).is_file():
                self._json(503, {"code": "APPLY_NOT_CONFIGURED",
                                 "message": "확정 반영 설정(runtime/apply-config.json)이 없습니다."})
                return
            state = apply_state()
            if parsed.path == "/api/citizen-routing/apply/cancel":
                ok = apply_confirm.cancel(runtime)
                self._json(200 if ok else 409, {"cancelled": ok, "state": state.get("state")})
                return
            if state.get("state") == "running":
                self._json(409, {"code": "APPLY_BUSY", "message": "다른 반영 작업이 진행 중입니다.",
                                 "step": state.get("step")})
                return
            if parsed.path == "/api/citizen-routing/apply":
                confirm_id = body.get("confirm_id")
                if confirm_id is not None and not isinstance(confirm_id, str):
                    self._json(400, {"code": "INPUT_INVALID", "message": "confirm_id는 문자열이어야 합니다."})
                    return
                pid = apply_confirm.spawn(Path(apply_config), runtime, confirm_id,
                                          bool(body.get("prebuilt_only")))
                self._json(202, {"state": "started", "pid": pid})
                return
            if parsed.path == "/api/citizen-routing/apply/switch":
                build_id = body.get("build_id")
                if not isinstance(build_id, str) or not build_id.startswith("g-"):
                    self._json(400, {"code": "INPUT_INVALID", "message": "build_id가 필요합니다."})
                    return
                pid = apply_confirm.spawn(Path(apply_config), runtime, switch=build_id)
                self._json(202, {"state": "started", "pid": pid, "switch": build_id})
                return
            self.send_error(404)

        def do_GET(self) -> None:
            parsed = urlsplit(self.path)
            if parsed.path == "/api/citizen-routing/apply/status":
                active = confirm_summary()
                asked = parse_qs(parsed.query).get("confirm_id", [""])[0]
                # 안끼리 plan_id@revision이 같을 수 있어 "적용됨"은 confirm_id로만 판정한다.
                applied = {"applied": bool(active and active.get("confirm_id") == asked)} if asked else {}
                self._json(200, {**apply_state(), **applied, "active": active,
                                 "builds": apply_confirm.list_builds(runtime) if runtime else [],
                                 "configured": bool(apply_config and Path(apply_config).is_file())})
                return
            if parsed.path == "/api/citizen-routing/presets":
                active = apply_confirm.read_active(runtime) if runtime else None
                path = Path(active["dir"]) / "presets.json" if active else None
                if path and path.is_file():
                    self._json(200, json.loads(path.read_text(encoding="utf-8")))
                else:
                    self._json(200, {"presets": [], "message": "적용된 확정의 시연 출발지가 없습니다."})
                return
            if parsed.path == "/citizen-map-style.json":
                self._json(200, citizen_style())
                return
            if parsed.path == "/api/citizen-routing/status":
                normal = mode_status("normal", otp_dir, endpoint)
                strike = mode_status("strike", *strike_target(), normal.get("version"))
                extra = {"confirm": confirm_summary(), "apply": {k: apply_state().get(k) for k in ("state", "step")}}
                if normal.get("code") == "DATA_NOT_PREPARED":
                    self._json(503, {**normal, "modes": {"normal": normal, "strike": strike}, **extra})
                else:
                    self._json(200, {**normal, "modes": {"normal": normal, "strike": strike}, **extra})
                return
            if parsed.path == "/api/citizen-routing/stops":
                term = parse_qs(parsed.query).get("q", [""])[0].strip().casefold()
                if len(term) < 2:
                    self._json(200, {"stops": []})
                    return
                try:
                    matches = [s for s in cached_stops() if term in s["name"].casefold()][:30]
                    self._json(200, {"stops": matches})
                except (OSError, zipfile.BadZipFile, ValueError) as error:
                    self._json(503, {"code": "GTFS_INVALID", "message": str(error)})
                return
            if parsed.path == "/api/citizen-routing/places":
                term = parse_qs(parsed.query).get("q", [""])[0].strip()
                key = compact(term)
                if len(key) < 2 or len(term) > 80:
                    self._json(200, {"places": [], "places_ready": (otp_dir.parent / "places.sqlite").is_file()})
                    return
                try:
                    osm = search_places(otp_dir.parent / "places.sqlite", term, 20)
                    for place in osm:
                        place["source"] = "osm"
                    stops = [{**stop, "source": "gtfs"} for stop in cached_stops()
                             if key in compact(stop["name"])][:10]
                    self._json(200, {"places": osm + stops,
                                     "places_ready": (otp_dir.parent / "places.sqlite").is_file()})
                except (OSError, sqlite3.DatabaseError, zipfile.BadZipFile, ValueError):
                    self._json(503, {"code": "PLACE_SEARCH_UNAVAILABLE",
                                     "message": "Place search data could not be read."})
                return
            if parsed.path == "/api/citizen-routing/map-places":
                params = parse_qs(parsed.query)
                try:
                    west, south, east, north, zoom = (
                        float(params.get(key, [""])[0])
                        for key in ("west", "south", "east", "north", "zoom")
                    )
                    if (not all(isfinite(value) for value in (west, south, east, north, zoom))
                            or west >= east or south >= north or east - west > 2
                            or north - south > 1 or not 0 <= zoom <= 22):
                        raise ValueError("Invalid map bounds")
                    places = visible_places(otp_dir.parent / "places.sqlite",
                                            west, south, east, north, zoom)
                    self._json(200, {"places": places})
                except (ValueError, OverflowError):
                    self._json(400, {"code": "INPUT_INVALID", "message": "Invalid map bounds"})
                except (OSError, sqlite3.DatabaseError):
                    self._json(503, {"code": "PLACE_SEARCH_UNAVAILABLE",
                                     "message": "Place search data could not be read."})
                return
            if parsed.path == "/api/citizen-routing/nearby":
                params = parse_qs(parsed.query)
                try:
                    lat = float(params.get("lat", [""])[0])
                    lon = float(params.get("lon", [""])[0])
                    if not 37 <= lat <= 38 or not 126 <= lon <= 128:
                        raise ValueError("Coordinates outside service area")
                    matches = nearby_places(otp_dir.parent / "places.sqlite", lat, lon)
                    self._json(200, {"places": [{**place, "source": "osm"} for place in matches]})
                except (ValueError, OverflowError):
                    self._json(400, {"code": "INPUT_INVALID", "message": "Invalid coordinates"})
                except (OSError, sqlite3.DatabaseError):
                    self._json(503, {"code": "PLACE_SEARCH_UNAVAILABLE", "message": "Place search data could not be read."})
                return
            if parsed.path in {"/api/citizen-routing/plan", "/api/citizen-routing/compare",
                               "/api/citizen-routing/compare3"}:
                try:
                    params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                    normal_manifest = read_manifest(otp_dir)
                    normal_version = normal_manifest.get("version") if normal_manifest else None
                    strike_dir_now, strike_url_now = strike_target()
                    if parsed.path.endswith("/compare3"):
                        result = compare_three(params, otp_dir, endpoint, strike_dir_now, strike_url_now)
                        result["confirm"] = confirm_summary()
                    elif parsed.path.endswith("/compare"):
                        result = compare_routes(params, otp_dir, endpoint, strike_dir_now, strike_url_now)
                    else:
                        mode = params.pop("mode", "normal")
                        if mode not in {"normal", "strike"}:
                            raise InputError("mode는 normal 또는 strike여야 합니다.")
                        directory, url = ((otp_dir, endpoint) if mode == "normal"
                                          else (strike_dir_now, strike_url_now))
                        result = route_for_mode(params, mode, directory, url, normal_version)
                    self._json(200, result)
                except InputError as error:
                    self._json(400, {"code": "INPUT_INVALID", "message": str(error)})
                except (FileNotFoundError, OSError, ValueError) as error:
                    self._json(503, {"code": "DATA_NOT_PREPARED", "message": str(error)})
                except OTPError as error:
                    self._json(404 if error.code == "NO_PATH" else 503,
                               {"code": error.code, "message": str(error)})
                return
            name = "index.html" if parsed.path == "/" else parsed.path.lstrip("/")
            file = WEB / name
            if not file.is_file() or not file.resolve().is_relative_to(WEB.resolve()):
                self.send_error(404)
                return
            content = file.read_bytes()
            self.send_response(200)
            content_type = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
            if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
                content_type += "; charset=utf-8"
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    return CitizenHandler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8775)
    parser.add_argument("--otp-url", default=NORMAL_URL)
    parser.add_argument("--strike-otp-url", default=STRIKE_URL)
    parser.add_argument("--strike-otp-dir", type=Path, default=STRIKE_DIR)
    parser.add_argument("--otp-dir", type=Path, default=NORMAL_DIR)
    parser.add_argument("--runtime", type=Path, default=RUNTIME,
                        help="확정 반영 상태(strike-active.json, apply/)를 둘 runtime 폴더")
    parser.add_argument("--apply-config", type=Path, default=None,
                        help="확정 반영 설정 JSON(기본 <runtime>/apply-config.json, 없으면 반영 API 비활성)")
    args = parser.parse_args()
    apply_config = args.apply_config or args.runtime / "apply-config.json"
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler(
        args.otp_dir, args.otp_url, args.strike_otp_dir, args.strike_otp_url,
        runtime=args.runtime, apply_config=apply_config if apply_config.is_file() else None))
    print(f"Citizen routing: http://127.0.0.1:{args.port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == "__main__":
    main()
