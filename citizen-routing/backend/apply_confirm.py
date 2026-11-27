"""운영자 확정 묶음 → 셔틀 GTFS → 파업 그래프 새로 빌드 → 파업 OTP 교체.

확정 묶음 계약(작업자 A가 쓰고 여기서 읽는다)::

    <confirm_root>/<gu>/CURRENT                 지금 적용할 confirm_id 한 줄
    <confirm_root>/<gu>/<confirm_id>/confirm.json  files: {name: {path, sha256}}
        scenario.json plan.json timetable.json verifier_report.json stops.csv

일반 그래프는 건드리지 않는다. 새 파업 그래프는 새 폴더에 만들고, 새 OTP가 응답한 뒤에야
활성 표시(strike-active.json)를 바꾼다. 그 전까지 이전 그래프가 계속 응답한다.
카드 ID·승객 단위 정보는 읽지도 쓰지도 않는다(확정 묶음에 없음).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from .gtfs_adapter import FeedError, read_feed, rows
from .otp_client import healthy as otp_healthy
from .prepare_modes import prepare_strike
from .shuttle_export import export_checked

ROOT = Path(__file__).resolve().parents[1]
KST = timezone(timedelta(hours=9))
FILES = ("scenario", "plan", "timetable", "m07_report", "stops")
MAX_CLOSING_S = 300
SLOW_BUILD_S = 300
OTP_IMAGE = "docker.io/opentripplanner/opentripplanner:2.10.0"


class ConfirmError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def now() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------- 확정 묶음

def _file_entry(name: str, entry) -> tuple[str, str]:
    if isinstance(entry, dict) and isinstance(entry.get("path"), str) and isinstance(entry.get("sha256"), str):
        return entry["path"], entry["sha256"]
    if isinstance(entry, (list, tuple)) and len(entry) == 2 and all(isinstance(x, str) for x in entry):
        return entry[0], entry[1]
    raise ConfirmError("CONFIRM_INVALID", f"confirm.json files.{name}에 상대 경로와 sha256이 필요합니다.")


def load_confirm(confirm_root: Path, gu: str, confirm_id: str | None = None) -> dict:
    """CURRENT(또는 지정 id)의 확정 묶음을 읽고 파일 sha256을 confirm.json과 대조한다."""
    gu_dir = Path(confirm_root) / gu
    if confirm_id is None:
        current = gu_dir / "CURRENT"
        if not current.is_file():
            raise ConfirmError("CONFIRM_NOT_FOUND", f"{gu} 확정 묶음 CURRENT가 없습니다.")
        confirm_id = current.read_text(encoding="utf-8").strip()
    if not confirm_id or "/" in confirm_id or "\\" in confirm_id or confirm_id.startswith("."):
        raise ConfirmError("CONFIRM_INVALID", "confirm_id 형식이 잘못되었습니다.")
    directory = gu_dir / confirm_id
    path = directory / "confirm.json"
    if not path.is_file():
        raise ConfirmError("CONFIRM_NOT_FOUND", f"확정 묶음이 없습니다: {gu}/{confirm_id}")
    try:
        confirm = json.loads(path.read_text(encoding="utf-8-sig"))
    except ValueError as error:
        raise ConfirmError("CONFIRM_INVALID", "confirm.json을 읽을 수 없습니다.") from error
    if confirm.get("schema_version") != 1:
        raise ConfirmError("CONFIRM_INVALID", "confirm.json schema_version은 1이어야 합니다.")
    if confirm.get("confirm_id") != confirm_id or confirm.get("gu") != gu:
        raise ConfirmError("CONFIRM_INVALID", "confirm.json의 confirm_id·gu가 폴더와 다릅니다.")
    files = confirm.get("files")
    if not isinstance(files, dict):
        raise ConfirmError("CONFIRM_INVALID", "confirm.json에 files가 없습니다.")
    paths, mismatched = {}, []
    base = directory.resolve()
    for name in FILES:
        if name not in files:
            raise ConfirmError("CONFIRM_INVALID", f"confirm.json files에 {name}이 없습니다.")
        rel, expected = _file_entry(name, files[name])
        target = (directory / rel).resolve()
        if Path(rel).is_absolute() or ".." in Path(rel).parts or not target.is_relative_to(base):
            raise ConfirmError("CONFIRM_INVALID", f"files.{name} 경로가 묶음 폴더 밖입니다.")
        if not target.is_file():
            raise ConfirmError("CONFIRM_INVALID", f"묶음 파일이 없습니다: {rel}")
        if sha(target) != expected.lower():
            mismatched.append(name)
        paths[name] = target
    if mismatched:
        raise ConfirmError("CONFIRM_HASH_MISMATCH", f"sha256이 confirm.json과 다릅니다: {mismatched}")
    plan = json.loads(paths["plan"].read_text(encoding="utf-8-sig"))
    # plan_sha256은 곡선(pareto.json) 위 안의 식별자다. 묶음 plan.json(M05 입력 형식) 해시와 다를 수 있어
    # 무결성은 files.plan.sha256으로만 본다.
    if not isinstance(confirm.get("plan_sha256"), str) or not confirm["plan_sha256"]:
        raise ConfirmError("CONFIRM_INVALID", "confirm.json에 plan_sha256이 필요합니다.")
    if plan.get("plan_id") != confirm.get("plan_id") or plan.get("revision") != confirm.get("revision"):
        raise ConfirmError("CONFIRM_INVALID", "plan.json의 plan_id/revision이 confirm.json과 다릅니다.")
    m07 = confirm.get("m07") or {}
    if not m07.get("pass") or m07.get("failed_checks"):
        raise ConfirmError("M07_NOT_PASSED", "M07 pass=true, 실패 검사 0건인 확정만 반영합니다.")
    return {"confirm": confirm, "dir": directory, "paths": paths, "plan": plan}


def plan_key(confirm: dict) -> str:
    """안 식별 표시. 실제 파이프라인 안들은 plan_id@revision이 모두 같아(plan-eqmc07-f11@1)
    plan_sha256 앞 8자를 붙여야 안끼리 구별된다."""
    return f"{confirm.get('plan_id')}@{confirm.get('revision')}#{(confirm.get('plan_sha256') or '')[:8]}"


def bundle_hashes(confirm: dict) -> dict[str, str]:
    """확정 묶음 다섯 파일의 sha256(confirm.json files). 그래프 셔틀 입력과 대조한다."""
    return {name: _file_entry(name, confirm["files"][name])[1].lower() for name in FILES}


def check_loops(bundle: dict, max_closing_s: int = MAX_CLOSING_S) -> dict:
    """모든 셔틀 노선이 순환인지 본다. 편도(닫히지 않음·닫는 공차가 김)는 거부한다."""
    plan = bundle["plan"]
    timetable = json.loads(bundle["paths"]["timetable"].read_text(encoding="utf-8-sig"))
    routes = plan.get("routes")
    if not isinstance(routes, list) or not routes:
        raise ConfirmError("CONFIRM_INVALID", "plan.json에 노선이 없습니다.")
    closing_by_route: dict[str, int] = {}
    for trip in timetable.get("trips") or []:
        if trip.get("in_service") is False and trip.get("stop_times"):
            times = [st["sched_s"] for st in trip["stop_times"]]
            closing_by_route[trip["route_id"]] = max(closing_by_route.get(trip["route_id"], 0),
                                                     times[-1] - times[0])
    summary = {}
    for route in routes:
        rid = route.get("route_id")
        legs = route.get("legs")
        if not isinstance(legs, list) or not legs:
            raise ConfirmError("CONFIRM_INVALID", f"노선 {rid}에 legs가 없습니다.")
        if not any(leg.get("in_service") for leg in legs):
            raise ConfirmError("ONE_WAY_ROUTE", f"노선 {rid}에 영업 구간이 없습니다.")
        for prev, leg in zip(legs, legs[1:]):
            if prev["stop_ids"][-1] != leg["stop_ids"][0]:
                raise ConfirmError("ONE_WAY_ROUTE", f"노선 {rid}의 구간이 이어지지 않습니다.")
        if legs[-1]["stop_ids"][-1] != legs[0]["stop_ids"][0]:
            raise ConfirmError("ONE_WAY_ROUTE", f"노선 {rid}가 출발 정류장으로 돌아오지 않습니다(편도).")
        closing = closing_by_route.get(rid, 0)
        if closing > max_closing_s:
            raise ConfirmError("ONE_WAY_ROUTE",
                               f"노선 {rid}의 닫는 공차가 {closing}초로 {max_closing_s}초를 넘습니다(사실상 편도).")
        summary[rid] = {"closing_s": closing, "legs": len(legs)}
    return summary


# ---------------------------------------------------------------- 날짜·노선 대응

def feed_service_dates(feed: dict[str, bytes]) -> list[str]:
    """A GTFS의 실제 운행일 목록(calendar 요일 범위 + calendar_dates 추가, 제외 반영)."""
    days: set[str] = set()
    names = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    if "calendar.txt" in feed:
        for row in rows(feed, "calendar.txt")[1]:
            day = datetime.strptime(row["start_date"], "%Y%m%d").date()
            end = datetime.strptime(row["end_date"], "%Y%m%d").date()
            while day <= end:
                if row.get(names[day.weekday()]) == "1":
                    days.add(day.isoformat())
                day += timedelta(days=1)
    if "calendar_dates.txt" in feed:
        for row in rows(feed, "calendar_dates.txt")[1]:
            value = datetime.strptime(row["date"], "%Y%m%d").date().isoformat()
            if row.get("exception_type") == "1":
                days.add(value)
    return sorted(days)


def map_service_date(simulated: str, a_dates: list[str]) -> tuple[str, str]:
    """시뮬레이션 날짜를 A GTFS 운행일로 옮긴다: 같은 날 → 같은 요일 → 첫 운행일."""
    if not a_dates:
        raise ConfirmError("A_GTFS_NO_DATES", "A GTFS에 운행일이 없습니다.")
    if simulated in a_dates:
        return simulated, "same_date"
    weekday = date.fromisoformat(simulated).weekday()
    same = [d for d in a_dates if date.fromisoformat(d).weekday() == weekday]
    if same:
        return same[0], "same_weekday"
    return a_dates[0], "first_available"


def strike_route_map(strike_ids: list[str], a_routes: list[dict]) -> dict:
    """파업 노선 ID를 A GTFS route_id와 정확히 같은 것만 맞춘다(이름 추정 없음)."""
    by_id = {r["route_id"]: r for r in a_routes}
    table, matched, unmatched = [], [], []
    for rid in sorted(set(strike_ids)):
        route = by_id.get(rid)
        if route:
            matched.append(rid)
            table.append({"strike_route_id": rid, "a_route_id": rid,
                          "a_route_short_name": route.get("route_short_name", ""),
                          "match": "exact_id"})
        else:
            unmatched.append(rid)
            table.append({"strike_route_id": rid, "a_route_id": "", "a_route_short_name": "",
                          "match": "unmatched"})
    return {"rows": table, "matched": matched, "unmatched": unmatched}


def write_route_map(path: Path, table: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("strike_route_id", "a_route_id",
                                                     "a_route_short_name", "match"),
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(table["rows"])


def label_shuttle_routes(export_dir: Path, timetable_path: Path) -> None:
    """셔틀 routes.txt 이름을 시민 화면용으로 바꾼다(첫 정류장 이름 순환 · 배차)."""
    timetable = json.loads(timetable_path.read_text(encoding="utf-8-sig"))
    headway = {r["route_id"]: (r.get("period_s") or 0) / max(1, len(r.get("vehicle_ids") or []))
               for r in timetable.get("routes") or []}
    with (export_dir / "stops.txt").open(encoding="utf-8", newline="") as stream:
        stops = {r["stop_id"]: r["stop_name"] for r in csv.DictReader(stream)}
    first_stop = {}
    for trip in timetable.get("trips") or []:
        if trip.get("in_service") and trip["route_id"] not in first_stop:
            first_stop[trip["route_id"]] = trip["stop_times"][0]["stop_id"]
    path = export_dir / "routes.txt"
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        fields, values = reader.fieldnames, list(reader)
    for row in values:
        rid = row["route_id"]
        name = stops.get(first_stop.get(rid, ""), "")
        minutes = round(headway.get(rid, 0) / 60, 1)
        row["route_short_name"] = f"셔틀 {rid}"
        row["route_long_name"] = (f"{name} 순환" if name else "순환") + (
            f" · 배차 약 {minutes:g}분" if minutes else "")
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(values)
    path.write_text(out.getvalue(), encoding="utf-8")


# ---------------------------------------------------------------- OTP 실행기

class DockerRunner:
    """rootless Docker(--network host)로 OTP 2.10.0을 빌드·실행한다."""

    def __init__(self, prefix: str = "citizen-e2e", image: str = OTP_IMAGE,
                 build_heap: str = "6g", serve_heap: str = "4g"):
        self.prefix, self.image = prefix, image
        self.build_heap, self.serve_heap = build_heap, serve_heap
        self.build_name: str | None = None

    def build(self, graph_dir: Path, log: Path) -> None:
        self.build_name = f"{self.prefix}-build-{os.getpid()}"
        command = ["docker", "run", "--rm", "--name", self.build_name, "--network", "host",
                   "-e", f"JAVA_TOOL_OPTIONS=-Xmx{self.build_heap}",
                   "-v", f"{graph_dir}:/var/opentripplanner", self.image, "--build", "--save"]
        with log.open("ab") as out:
            code = subprocess.run(command, stdout=out, stderr=subprocess.STDOUT).returncode
        self.build_name = None
        if code != 0:
            raise RuntimeError(f"OTP 그래프 빌드 실패(종료 코드 {code}). 로그: {log.name}")

    def cancel_build(self) -> None:
        if self.build_name:
            subprocess.run(["docker", "rm", "-f", self.build_name], capture_output=True)

    def serve(self, slot: str, graph_dir: Path, port: int) -> str:
        name = f"{self.prefix}-otp-strike-{slot}"
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        subprocess.run(["docker", "run", "-d", "--name", name, "--network", "host",
                        "-e", f"JAVA_TOOL_OPTIONS=-Xmx{self.serve_heap}",
                        "-v", f"{graph_dir}:/var/opentripplanner", self.image,
                        "--load", "--serve", "--port", str(port)],
                       check=True, capture_output=True)
        return name

    def healthy(self, url: str) -> bool:
        return otp_healthy(url)

    def stop(self, handle: str) -> None:
        if handle and handle.startswith(self.prefix + "-"):
            subprocess.run(["docker", "rm", "-f", handle], capture_output=True)


# ---------------------------------------------------------------- 적용

@dataclass
class ApplyConfig:
    confirm_root: Path
    gu: str
    a_gtfs: Path
    osm: Path
    normal_dir: Path
    runtime: Path
    ports: tuple[int, int] = (8811, 8812)
    keep: int = 2
    health_timeout_s: int = 600
    host: str = "127.0.0.1"
    presets: Callable[[dict], dict] | None = field(default=None, repr=False)

    @classmethod
    def from_file(cls, path: Path) -> "ApplyConfig":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        values = {k: Path(raw[k]) for k in ("confirm_root", "a_gtfs", "osm", "normal_dir", "runtime")}
        return cls(gu=raw.get("gu", "guro"), ports=tuple(raw.get("ports", (8811, 8812))),
                   keep=int(raw.get("keep", 2)), health_timeout_s=int(raw.get("health_timeout_s", 600)),
                   **values)


def apply_dir(runtime: Path) -> Path:
    return Path(runtime) / "apply"


def read_active(runtime: Path) -> dict | None:
    path = Path(runtime) / "strike-active.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def read_status(runtime: Path) -> dict | None:
    path = apply_dir(runtime) / "status.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def list_builds(runtime: Path) -> list[dict]:
    root = Path(runtime) / "strike-graphs"
    builds = []
    for info_path in sorted(root.glob("g-*/build-info.json")) if root.is_dir() else []:
        info = json.loads(info_path.read_text(encoding="utf-8"))
        ready = graph_ready(info_path.parent)
        builds.append({**info, "ready": ready})
    builds.sort(key=lambda b: b.get("last_used_at", ""), reverse=True)
    return builds


def graph_ready(directory: Path) -> bool:
    manifest = directory / "manifest.json"
    stamp = directory / "graph-version.txt"
    if not (manifest.is_file() and stamp.is_file() and (directory / "graph.obj").is_file()):
        return False
    version = json.loads(manifest.read_text(encoding="utf-8")).get("version")
    return stamp.read_text(encoding="utf-8").strip() == version


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


class Applier:
    def __init__(self, config: ApplyConfig, runner=None):
        self.config = config
        self.runner = runner or DockerRunner()
        self.runtime = Path(config.runtime)
        self.status: dict = {}

    # 상태 기록 ---------------------------------------------------------
    def _save(self) -> None:
        self.status["updated_at"] = now()
        write_json(apply_dir(self.runtime) / "status.json", self.status)

    def _step(self, name: str):
        applier = self

        class Step:
            def __enter__(self):
                self.started = time.monotonic()
                applier.status["step"] = name
                applier.status["steps"].append({"name": name, "state": "running", "started_at": now()})
                applier._save()
                return applier.status["steps"][-1]

            def __exit__(self, kind, error, trace):
                entry = applier.status["steps"][-1]
                entry["seconds"] = round(time.monotonic() - self.started, 2)
                entry["ended_at"] = now()
                entry["state"] = "done" if kind is None else "failed"
                if kind is not None:
                    applier.status.update(state="failed", error={
                        "code": getattr(error, "code", type(error).__name__),
                        "message": str(error)})
                applier._save()
                return False

        return Step()

    def _lock(self) -> Path:
        lock = apply_dir(self.runtime) / "lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        if lock.is_file():
            try:
                pid = int(lock.read_text().strip() or 0)
            except ValueError:
                pid = 0
            if pid and pid != os.getpid() and pid_alive(pid):
                raise ConfirmError("APPLY_BUSY", "다른 반영 작업이 진행 중입니다.")
            lock.unlink(missing_ok=True)
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return lock

    # 본 작업 -----------------------------------------------------------
    def run(self, confirm_id: str | None = None, prebuilt_only: bool = False) -> dict:
        lock = self._lock()
        self.status = {"state": "running", "step": None, "steps": [], "started_at": now(),
                       "pid": os.getpid(), "prebuilt_only": prebuilt_only, "cache_hit": False,
                       "slow": False, "error": None}
        self._save()
        try:
            return self._run(confirm_id, prebuilt_only)
        finally:
            lock.unlink(missing_ok=True)

    def _run(self, confirm_id: str | None, prebuilt_only: bool) -> dict:
        cfg = self.config
        graphs = self.runtime / "strike-graphs"
        with self._step("verify") as entry:
            bundle = load_confirm(cfg.confirm_root, cfg.gu, confirm_id)
            loops = check_loops(bundle)
            confirm = bundle["confirm"]
            entry["message"] = f"노선 {len(loops)}개 모두 순환, 해시 일치"
            self.status.update(confirm_id=confirm["confirm_id"], plan_id=confirm["plan_id"],
                               revision=confirm["revision"], plan_sha256=confirm.get("plan_sha256"),
                               plan_key=plan_key(confirm), algo_name=confirm.get("algo_name"))
        staging = graphs / f"_staging-{os.getpid()}-{int(time.time() * 1000)}"
        try:
            with self._step("export") as entry:
                a_feed = read_feed(cfg.a_gtfs)
                scenario = json.loads(bundle["paths"]["scenario"].read_text(encoding="utf-8-sig"))
                service_date, rule = map_service_date(scenario["service_date"], feed_service_dates(a_feed))
                export_dir = staging / "shuttle-export"
                p = bundle["paths"]
                meta = export_checked(p["scenario"], p["plan"], p["timetable"], p["m07_report"],
                                      p["stops"], export_dir, demo=False, service_date=service_date,
                                      confirm=confirm)
                meta["service_date_rule"] = rule
                label_shuttle_routes(export_dir, p["timetable"])
                write_json(export_dir / "shuttle-meta.json", meta)
                entry["message"] = (f"셔틀 {meta['stats']['routes']}개 노선·{meta['stats']['trips']}편, "
                                    f"운행일 {scenario['service_date']} → {service_date} ({rule})")
                self.status["service_date"] = {"simulated": scenario["service_date"],
                                               "gtfs": service_date, "rule": rule}
            with self._step("route_map") as entry:
                table = strike_route_map(scenario.get("strike_route_ids") or [],
                                         rows(a_feed, "routes.txt")[1])
                write_route_map(apply_dir(self.runtime) / f"route_map_{confirm['confirm_id']}.csv", table)
                if not table["matched"]:
                    raise ConfirmError("STRIKE_ROUTES_UNMATCHED", "A GTFS와 정확히 맞는 파업 노선이 없습니다.")
                entry["message"] = f"정확 일치 {len(table['matched'])}개, 미일치 {len(table['unmatched'])}개"
                self.status["route_map"] = {"matched": len(table["matched"]),
                                            "unmatched": table["unmatched"]}
            with self._step("prepare") as entry:
                normal = json.loads((Path(cfg.normal_dir) / "manifest.json").read_text(encoding="utf-8"))
                osm_local = staging / "streets.osm.pbf"
                try:
                    os.link(cfg.osm, osm_local)
                except OSError:
                    shutil.copyfile(cfg.osm, osm_local)
                manifest = prepare_strike(Path(cfg.a_gtfs), osm_local, table["matched"], staging,
                                          normal["version"], export_dir, export_dir / "shuttle-meta.json",
                                          f"confirm:{confirm['gu']}/{confirm['confirm_id']}")
                entry["message"] = (f"파업 입력 {manifest['version']}: A 노선 "
                                    f"{manifest['existing_stats']['routes']}개 + 셔틀 "
                                    f"{len(manifest['shuttle_route_ids'])}개")
            # 실제 안들은 plan_id@revision이 같을 수 있어(plan-eqmc07-f11@1) plan_sha256까지 묶는다.
            plan_short = confirm["plan_sha256"][:12]
            build_dir = graphs / f"g-{manifest['version']}-{plan_short}"
            with self._step("build") as entry:
                if graph_ready(build_dir):
                    self.status["cache_hit"] = True
                    entry["message"] = "같은 입력의 그래프를 다시 씀(빌드 생략)"
                    shutil.rmtree(staging, ignore_errors=True)
                elif prebuilt_only:
                    raise ConfirmError("PREBUILT_MISSING", "미리 빌드한 같은 입력의 그래프가 없습니다.")
                else:
                    shutil.rmtree(build_dir, ignore_errors=True)
                    started = time.monotonic()
                    self.runner.build(staging, staging / "build.log")
                    seconds = time.monotonic() - started
                    if not (staging / "graph.obj").is_file():
                        raise RuntimeError("OTP graph.obj가 만들어지지 않았습니다.")
                    (staging / "graph-version.txt").write_text(manifest["version"], encoding="utf-8")
                    os.replace(staging, build_dir)
                    self.status["slow"] = seconds > SLOW_BUILD_S
                    entry["message"] = f"빌드 {seconds:.0f}초"
                info = {"build_id": build_dir.name, "version": manifest["version"],
                        "confirm_id": confirm["confirm_id"], "plan_id": confirm["plan_id"],
                        "revision": confirm["revision"], "plan_sha256": confirm.get("plan_sha256"),
                        "plan_key": plan_key(confirm), "bundle_sha256": bundle_hashes(confirm),
                        "algo_name": confirm.get("algo_name"), "confirmed_at": confirm.get("confirmed_at"),
                        "m07": confirm.get("m07"), "service_date": self.status["service_date"],
                        "last_used_at": now()}
                old = build_dir / "build-info.json"
                info["built_at"] = (json.loads(old.read_text(encoding="utf-8")).get("built_at", now())
                                    if old.is_file() else now())
                write_json(old, info)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        with self._step("swap") as entry:
            active = self._swap(build_dir, info)
            entry["message"] = f"파업 OTP {active['url']} ({active['slot']})"
        self._prune()
        if cfg.presets:
            with self._step("presets") as entry:
                try:
                    result = cfg.presets(active)
                    entry["message"] = f"프리셋 {len(result.get('presets', []))}곳"
                except Exception as error:  # 프리셋 실패는 반영 실패가 아님
                    entry["message"] = f"프리셋 생략: {error}"
                    entry["nonfatal_error"] = True
        self.status.update(state="done", step="done", graph_version=manifest["version"],
                           applied_confirm_id=active.get("confirm_id"),
                           build_id=build_dir.name, finished_at=now())
        self._save()
        return {"graph_version": manifest["version"], "build_id": build_dir.name, "active": active}

    def _swap(self, build_dir: Path, info: dict) -> dict:
        if not graph_ready(build_dir):
            raise ConfirmError("GRAPH_NOT_BUILT", "그래프 버전 표시가 manifest와 다릅니다.")
        manifest = json.loads((build_dir / "manifest.json").read_text(encoding="utf-8"))
        source = manifest.get("shuttle_source") or {}
        expected = f"{info['plan_id']}@{info['revision']}"
        if f"{source.get('plan_id')}@{source.get('revision')}" != expected or \
                (source.get("confirm") or {}).get("plan_sha256") != info.get("plan_sha256"):
            raise ConfirmError("GRAPH_VERSION_MISMATCH", "그래프의 셔틀 버전이 확정 plan_id@revision과 다릅니다.")
        # plan_id@revision은 안끼리 같을 수 있다. 그래프 셔틀 입력 해시가 이 확정 묶음 파일과 같아야 한다
        # (bundle_sha256이 없는 예전 build-info는 위 검사만).
        expected_files = info.get("bundle_sha256")
        if expected_files:
            got_files = source.get("input_sha256") or {}
            bad = sorted(k for k, v in expected_files.items() if got_files.get(k) != v)
            if bad:
                raise ConfirmError("GRAPH_VERSION_MISMATCH",
                                   f"그래프의 셔틀 입력이 확정 묶음과 다릅니다({', '.join(bad)}). 이전 그래프를 유지합니다.")
        previous = read_active(self.runtime)
        ports = list(self.config.ports)
        slot = "b" if previous and previous.get("slot") == "a" else "a"
        port = ports[1] if slot == "b" else ports[0]
        url = f"http://{self.config.host}:{port}/otp/gtfs/v1"
        handle = self.runner.serve(slot, build_dir, port)
        deadline = time.monotonic() + self.config.health_timeout_s
        while not self.runner.healthy(url):
            if time.monotonic() > deadline:
                self.runner.stop(handle)
                raise ConfirmError("OTP_START_TIMEOUT", "새 파업 OTP가 제시간에 응답하지 않았습니다. 이전 그래프를 유지합니다.")
            time.sleep(2)
        active = {**{k: info.get(k) for k in ("build_id", "version", "confirm_id", "plan_id", "revision",
                                              "plan_sha256", "plan_key", "algo_name", "confirmed_at", "m07",
                                              "service_date")},
                  "dir": str(build_dir), "url": url, "port": port, "slot": slot, "handle": handle,
                  "graph_version": manifest["version"], "shuttle_version": expected,
                  "activated_at": now()}
        write_json(self.runtime / "strike-active.json", active)
        if previous and previous.get("handle") and previous.get("handle") != handle:
            self.runner.stop(previous["handle"])
        return active

    def switch(self, build_id: str) -> dict:
        """보관된(미리 빌드한) 그래프로 파업 OTP를 바꾼다."""
        lock = self._lock()
        try:
            if not build_id.startswith("g-") or "/" in build_id:
                raise ConfirmError("BUILD_NOT_FOUND", "build_id 형식이 잘못되었습니다.")
            build_dir = self.runtime / "strike-graphs" / build_id
            info_path = build_dir / "build-info.json"
            if not info_path.is_file():
                raise ConfirmError("BUILD_NOT_FOUND", f"보관된 그래프가 없습니다: {build_id}")
            info = json.loads(info_path.read_text(encoding="utf-8"))
            info["last_used_at"] = now()
            write_json(info_path, info)
            return self._swap(build_dir, info)
        finally:
            lock.unlink(missing_ok=True)

    def _prune(self) -> None:
        active = read_active(self.runtime) or {}
        builds = list_builds(self.runtime)
        keep = {b["build_id"] for b in builds[: self.config.keep]} | {active.get("build_id")}
        for build in builds:
            if build["build_id"] not in keep:
                shutil.rmtree(self.runtime / "strike-graphs" / build["build_id"], ignore_errors=True)
        for stale in (self.runtime / "strike-graphs").glob("_staging-*"):
            pid = stale.name.split("-")[1]
            if not (pid.isdigit() and pid_alive(int(pid))):
                shutil.rmtree(stale, ignore_errors=True)


def spawn(config_path: Path, runtime: Path, confirm_id: str | None = None,
          prebuilt_only: bool = False, switch: str | None = None) -> int:
    """서버에서 반영 작업을 별도 프로세스로 띄운다(서버를 다시 켜도 상태 파일이 남는다)."""
    command = [sys.executable, "-B", "-m", "backend.apply_confirm", "--config", str(config_path)]
    if confirm_id:
        command += ["--confirm-id", confirm_id]
    if prebuilt_only:
        command.append("--prebuilt-only")
    if switch:
        command += ["--switch", switch]
    log = apply_dir(runtime) / "apply.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("ab") as out:
        process = subprocess.Popen(command, cwd=ROOT, stdout=out, stderr=subprocess.STDOUT,
                                   start_new_session=True)
    return process.pid


def cancel(runtime: Path) -> bool:
    status = read_status(runtime) or {}
    pid = status.get("pid")
    if status.get("state") != "running" or not pid or not pid_alive(pid):
        return False
    os.kill(pid, signal.SIGTERM)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="운영자 확정 묶음을 시민 파업 그래프에 반영")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--confirm-id")
    parser.add_argument("--prebuilt-only", action="store_true")
    parser.add_argument("--switch", help="보관된 build_id로 파업 OTP만 바꾼다")
    args = parser.parse_args()
    config = ApplyConfig.from_file(args.config)
    raw = json.loads(args.config.read_text(encoding="utf-8"))
    runner = DockerRunner(prefix=raw.get("container_prefix", "citizen-e2e"),
                          build_heap=raw.get("build_heap", "6g"), serve_heap=raw.get("serve_heap", "4g"))
    if raw.get("presets"):
        from .select_presets import presets_hook
        config.presets = presets_hook(raw, config)
    applier = Applier(config, runner)

    def stop(signum, frame):
        runner.cancel_build()
        raise ConfirmError("CANCELLED", "사용자가 반영을 취소했습니다. 이전 그래프를 유지합니다.")

    signal.signal(signal.SIGTERM, stop)
    try:
        result = applier.switch(args.switch) if args.switch else applier.run(args.confirm_id, args.prebuilt_only)
    except (ConfirmError, FeedError, RuntimeError, OSError, ValueError) as error:
        if args.switch:
            applier.status = {"state": "failed", "step": "switch", "steps": [], "error": {
                "code": getattr(error, "code", type(error).__name__), "message": str(error)}}
            applier._save()
        if isinstance(error, ConfirmError) and error.code == "CANCELLED" and applier.status:
            applier.status["state"] = "cancelled"
            applier._save()
        print(json.dumps({"error": getattr(error, "code", type(error).__name__),
                          "message": str(error)}, ensure_ascii=False))
        raise SystemExit(1)
    if args.switch:
        applier.status = {"state": "done", "step": "switch", "steps": [], "error": None,
                          "build_id": result["build_id"], "confirm_id": result["confirm_id"],
                          "graph_version": result["graph_version"], "finished_at": now()}
        applier._save()
    print(json.dumps({"graph_version": result.get("graph_version")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
