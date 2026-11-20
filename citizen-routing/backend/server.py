"""Independent localhost citizen routing server."""
from __future__ import annotations

import argparse
import json
import mimetypes
import sqlite3
import sys
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .gtfs_adapter import rows
from .otp_client import OTPError, healthy
from .place_search import compact, nearby as nearby_places, search as search_places
from .route_service import InputError, plan

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


def handler(otp_dir: Path, endpoint: str):
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
            if parsed.path == "/api/citizen-routing/status":
                path = otp_dir / "manifest.json"
                if not path.exists():
                    self._json(503, {"code": "DATA_NOT_PREPARED", "message": "GTFS와 OSM을 먼저 준비하세요."})
                else:
                    manifest = json.loads(path.read_text(encoding="utf-8"))
                    stamp = otp_dir / "graph-version.txt"
                    manifest["graph_ready"] = (stamp.is_file() and
                                               stamp.read_text(encoding="utf-8") == manifest["version"])
                    manifest["otp_running"] = healthy(endpoint) if manifest["graph_ready"] else False
                    manifest["places_ready"] = (otp_dir.parent / "places.sqlite").is_file()
                    self._json(200, manifest)
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
            if parsed.path == "/api/citizen-routing/plan":
                try:
                    manifest = json.loads((otp_dir / "manifest.json").read_text(encoding="utf-8"))
                    stamp = otp_dir / "graph-version.txt"
                    if not stamp.is_file() or stamp.read_text(encoding="utf-8") != manifest["version"]:
                        raise OTPError("GRAPH_NOT_BUILT", "현재 입력 버전의 OTP 그래프를 먼저 구축하세요.")
                    params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                    self._json(200, plan(params, manifest, endpoint))
                except FileNotFoundError:
                    self._json(503, {"code": "DATA_NOT_PREPARED", "message": "GTFS와 OSM을 먼저 준비하세요."})
                except InputError as error:
                    self._json(400, {"code": "INPUT_INVALID", "message": str(error)})
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
            self.send_header("Content-Type", mimetypes.guess_type(file.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    return CitizenHandler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8775)
    parser.add_argument("--otp-url", default="http://127.0.0.1:8080/otp/gtfs/v1")
    parser.add_argument("--otp-dir", type=Path, default=ROOT / "runtime" / "otp")
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler(args.otp_dir, args.otp_url))
    print(f"Citizen routing: http://127.0.0.1:{args.port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == "__main__":
    main()
