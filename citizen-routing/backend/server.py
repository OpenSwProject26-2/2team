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
                           mode_status, read_manifest, route_for_mode)

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "frontend"


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
            strike_endpoint: str = STRIKE_URL):
    stops_cache = None

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
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            parsed = urlsplit(self.path)
            if parsed.path == "/citizen-map-style.json":
                self._json(200, citizen_style())
                return
            if parsed.path == "/api/citizen-routing/status":
                normal = mode_status("normal", otp_dir, endpoint)
                strike = mode_status("strike", strike_dir, strike_endpoint, normal.get("version"))
                if normal.get("code") == "DATA_NOT_PREPARED":
                    self._json(503, {**normal, "modes": {"normal": normal, "strike": strike}})
                else:
                    self._json(200, {**normal, "modes": {"normal": normal, "strike": strike}})
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
            if parsed.path in {"/api/citizen-routing/plan", "/api/citizen-routing/compare"}:
                try:
                    params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                    normal_manifest = read_manifest(otp_dir)
                    normal_version = normal_manifest.get("version") if normal_manifest else None
                    if parsed.path.endswith("/compare"):
                        result = compare_routes(params, otp_dir, endpoint, strike_dir, strike_endpoint)
                    else:
                        mode = params.pop("mode", "normal")
                        if mode not in {"normal", "strike"}:
                            raise InputError("mode는 normal 또는 strike여야 합니다.")
                        directory, url = ((otp_dir, endpoint) if mode == "normal"
                                          else (strike_dir, strike_endpoint))
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
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler(args.otp_dir, args.otp_url, args.strike_otp_dir, args.strike_otp_url))
    print(f"Citizen routing: http://127.0.0.1:{args.port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == "__main__":
    main()
