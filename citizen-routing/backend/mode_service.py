"""Separate normal and strike graph state and route comparison."""
from __future__ import annotations

import json
from pathlib import Path

from .bus_categories import load_categories
from .otp_client import OTPError, healthy
from .route_service import plan

ROOT = Path(__file__).resolve().parents[1]
NORMAL_DIR = ROOT / "runtime" / "otp"
STRIKE_DIR = ROOT / "runtime" / "otp-strike"
NORMAL_URL = "http://127.0.0.1:8080/otp/gtfs/v1"
STRIKE_URL = "http://127.0.0.1:8081/otp/gtfs/v1"


def read_manifest(directory: Path) -> dict | None:
    path = directory / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def graph_ready(directory: Path, manifest: dict) -> bool:
    stamp = directory / "graph-version.txt"
    return (stamp.is_file() and (directory / "graph.obj").is_file()
            and stamp.read_text(encoding="utf-8").strip() == manifest.get("version"))


def mode_status(mode: str, directory: Path, endpoint: str,
                normal_version: str | None = None) -> dict:
    manifest = read_manifest(directory)
    if manifest is None:
        code = "STRIKE_ROUTES_MISSING" if mode == "strike" else "DATA_NOT_PREPARED"
        return {"mode": mode, "available": False, "code": code,
                "message": "파업 교통망을 준비하지 않았습니다." if mode == "strike" else "A GTFS와 OSM을 준비하세요."}
    result = {**manifest, "mode": mode}
    result["graph_ready"] = graph_ready(directory, manifest)
    result["otp_running"] = healthy(endpoint) if result["graph_ready"] else False
    result["places_ready"] = (NORMAL_DIR.parent / "places.sqlite").is_file()
    result["available"] = result["graph_ready"] and result["otp_running"]
    if mode == "strike":
        if not manifest.get("excluded_route_ids"):
            result.update(available=False, code="STRIKE_ROUTES_MISSING",
                          message="정확한 A GTFS 파업 route_id 목록이 없습니다.")
        elif not normal_version or manifest.get("baseline_version") != normal_version:
            result.update(available=False, code="DATA_VERSION_MISMATCH",
                          message="일반·파업 교통망의 A 입력 버전이 다릅니다. 두 그래프를 다시 준비하세요.")
    if not result["graph_ready"]:
        result.update(available=False, code="GRAPH_NOT_BUILT",
                      message="현재 입력 버전의 OTP 그래프를 구축하세요.")
    elif not result["otp_running"]:
        result.update(available=False, code="OTP_UNAVAILABLE",
                      message="해당 모드의 OTP 서버가 실행 중이지 않습니다.")
    return result


def require_mode(mode: str, directory: Path, endpoint: str,
                 normal_version: str | None = None) -> dict:
    status = mode_status(mode, directory, endpoint, normal_version)
    if not status["available"]:
        raise OTPError(status["code"], status["message"])
    return status


def route_for_mode(params: dict[str, str], mode: str, directory: Path, endpoint: str,
                   normal_version: str | None = None) -> dict:
    manifest = require_mode(mode, directory, endpoint, normal_version)
    request = dict(params)
    if mode == "normal":
        request["include_shuttle"] = "false"
    elif not manifest.get("shuttle_route_ids"):
        request["include_shuttle"] = "false"
    result = plan(request, manifest, endpoint, load_categories(directory))
    result["mode"] = mode
    result["shuttle_source"] = manifest.get("shuttle_source")
    return result


def compare_routes(params: dict[str, str], normal_dir: Path, normal_url: str,
                   strike_dir: Path, strike_url: str) -> dict:
    normal_manifest = require_mode("normal", normal_dir, normal_url)
    strike_manifest = require_mode("strike", strike_dir, strike_url, normal_manifest["version"])
    routes = {}
    for mode, directory, endpoint in (("normal", normal_dir, normal_url),
                                      ("strike", strike_dir, strike_url)):
        try:
            routes[mode] = route_for_mode(params, mode, directory, endpoint,
                                          normal_manifest["version"])
        except OTPError as error:
            if error.code != "NO_PATH":
                raise
            routes[mode] = {"mode": mode, "routes": [], "count": 0,
                            "code": "NO_PATH", "message": str(error)}
    affected = {"A:" + rid for rid in strike_manifest["excluded_route_ids"]}
    lost = [route for route in routes["normal"]["routes"]
            if any(leg.get("route_id") in affected for leg in route["legs"])]
    score = lambda route: (route["duration_seconds"] + route["transfers"] * 600
                           + route["walk_seconds"] * .5, route["duration_seconds"])
    best_normal = min(routes["normal"]["routes"], key=score, default=None)
    best_strike = min(routes["strike"]["routes"], key=score, default=None)
    return {
        "normal": routes["normal"], "strike": routes["strike"],
        "normal_version": normal_manifest["version"],
        "strike_version": strike_manifest["version"],
        "affected_route_ids": strike_manifest["excluded_route_ids"],
        "unavailable_normal_routes": lost,
        "normal_best": best_normal,
        "strike_best": best_strike,
        "delta_seconds": (best_strike["duration_seconds"] - best_normal["duration_seconds"]
                          if best_normal and best_strike else None),
        "shuttle_source": strike_manifest.get("shuttle_source"),
    }