"""충족률·형평성 곡선에서 고른 안 확정 → 확정 묶음(시민 길찾기 입력). /api/d/confirm* 가 부른다.

<confirm_root>/<gu>/<confirm_id>/
  confirm.json  (docs/schemas/confirm.schema.json)
  scenario.json  plan.json  timetable.json  verifier_report.json  stops.csv
<confirm_root>/<gu>/CURRENT   지금 적용 중인 confirm_id 한 줄
- 다섯 파일은 citizen-routing backend.shuttle_export의 입력 그대로다. 확정 전에 같은 함수(export_checked)를 임시 폴더에
  돌려(GTFS는 버림) 통과한 것만 확정한다. 실패하면 아무것도 쓰지 않고 이유를 돌려준다.
- 파일은 파이프라인 실행 폴더에서 모은다(대표 날짜 하루 = 10/28이 평가 날짜에 있으면 그날, 아니면 첫 평가 날짜):
  scenario = bundles/road/strike_<날짜>/scenario_f<대수>.json(그 M05 실행의 scenario_manifest와 scenario_id 대조),
  plan = gen/…/eqmc07-f<대수>.json(sha256 = pareto plan_sha256) + scenario_id·status를 붙인 C 형식,
  timetable = eval/runs/…/run/timetable.json, verifier_report = 같은 실행의 M07 보고서, stops = 영업 정차점 좌표.
- 순환 확인: M05 시간표의 빈 차 구간은 노선마다 고리를 닫는 한 구간이고 300초 이하여야 한다(편도 구간 섞이면 거절).
- 기록은 지우지 않고 쌓는다. 기존 선택 기록(data/d_selection.json)에 pareto_selected로 같이 남긴다.
- 카드 ID·승객 단위 정보 없음(정류장 좌표·시간표·지표만).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .d_geo import DataError
from .d_pareto import metrics_of

REPO = Path(__file__).resolve().parents[3]
SCHEMA = REPO / "docs" / "schemas" / "confirm.schema.json"
KST = timezone(timedelta(hours=9))
RULE = "same_line-w400"
CLOSE_MAX_S = 300                       # 고리 닫는 빈 차 구간 상한(초) — 순환 운행 규칙
FILES = {"scenario": "scenario.json", "plan": "plan.json", "timetable": "timetable.json",
         "m07_report": "verifier_report.json", "stops": "stops.csv"}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as s:
        for chunk in iter(lambda: s.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _hhmm(s: int) -> str:
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}"


def loop_check(timetable: dict) -> tuple[bool, str]:
    """M05 시간표로 순환 운행 확인: 노선마다 영업 구간 1개 + 고리 닫는 빈 차 구간 0~1개(≤ 300초)."""
    legs: dict[str, set] = {}
    worst = 0
    for t in timetable.get("trips", []):
        legs.setdefault(t["route_id"], set()).add((t.get("leg_index"), bool(t.get("in_service"))))
        if not t.get("in_service"):
            ts = [x["sched_s"] for x in t["stop_times"]]
            worst = max(worst, max(ts) - min(ts))
    bad = [r for r, ls in legs.items() if sum(1 for _, s in ls if s) != 1 or sum(1 for _, s in ls if not s) > 1]
    if bad:
        return False, f"순환이 아닌 노선(영업·빈 차 구간 수가 맞지 않음): {', '.join(sorted(bad))}"
    if worst > CLOSE_MAX_S:
        return False, f"고리 닫는 빈 차 구간이 {worst}초(> {CLOSE_MAX_S}초) — 편도에 가까운 노선"
    return True, f"노선 {len(legs)}개 모두 순환, 고리 닫는 빈 차 최대 {worst}초"


class ConfirmStore:
    def __init__(self, paths, pareto, runs=None, selections=None):
        self.paths = paths
        self.root = Path(paths.confirm_root) if paths.confirm_root else None
        self.pareto = pareto
        self.runs = runs
        self.selections = selections

    # ------------------------------------------------------------ 모으기 + 검사(쓰지 않음)
    def gather(self, region: str, run: str | None, plan_ref: str) -> dict:
        doc = self.pareto.pareto(region, run or None)
        if doc.get("status") != "available":
            raise DataError("DATA_INCOMPLETE", "곡선 자료가 없습니다.")
        plan = next((p for p in doc.get("plans", []) if p.get("id") == plan_ref), None)
        if plan is None:
            raise DataError("INPUT_INVALID", "곡선에 없는 안입니다(지자체안은 확정 대상이 아님).")
        if plan.get("served_rate") is None:
            raise DataError("PLAN_INFEASIBLE", "M07을 통과하지 못했거나 지표가 없는 안은 확정할 수 없습니다.")
        out = self.pareto.plan_dir(region, run or None)
        fleet = int(doc["fleet"])
        days = list(doc.get("eval_days") or [])
        day = "20261028" if "20261028" in days else days[0]
        tag = plan.get("eval_tag") or ("p" + plan["plan_sha256"][:12])
        idx = json.loads((out / "plans_index.json").read_text(encoding="utf-8"))
        row = next((r for r in idx if r["sha"] == plan["plan_sha256"]), None)
        if row is None or not Path(row["plan"]).is_file():
            raise DataError("DATA_INCOMPLETE", "운행안 원본 파일(gen/…/plans)을 찾지 못했습니다.")
        plan_file = Path(row["plan"])
        if sha256(plan_file) != plan["plan_sha256"]:
            raise DataError("DATA_VERSION_MISMATCH", "운행안 파일 해시가 곡선의 plan_sha256과 다릅니다.")
        runs_dir = out / "eval" / "runs" / f"{tag}-f{fleet}" / RULE / "exp"
        cand = sorted(runs_dir.glob(f"*/strike_{day}/f{fleet}"))
        if not cand:
            raise DataError("DATA_INCOMPLETE", f"M05 실행 폴더가 없습니다({tag}, {day}).")
        rd = cand[0]
        files = {"scenario": out / "bundles" / "road" / f"strike_{day}" / f"scenario_f{fleet}.json",
                 "timetable": rd / "run" / "timetable.json", "m07_report": rd / "verifier_report.json"}
        for k, f in files.items():
            if not f.is_file():
                raise DataError("DATA_INCOMPLETE", f"{FILES[k]}가 실행 폴더에 없습니다: {f.name}")
        scenario = json.loads(files["scenario"].read_text(encoding="utf-8"))
        manifest = json.loads((rd / "scenario" / "scenario_manifest.json").read_text(encoding="utf-8"))
        timetable = json.loads(files["timetable"].read_text(encoding="utf-8"))
        report = json.loads(files["m07_report"].read_text(encoding="utf-8"))
        raw_plan = json.loads(plan_file.read_text(encoding="utf-8"))
        rc = json.loads((out / "run_config.json").read_text(encoding="utf-8")) if (out / "run_config.json").is_file() else {}
        job = self.runs.find(run)[1] if (run and self.runs is not None and self.runs.find(run)) else {}
        checks = []

        def check(name, ok, detail=""):
            checks.append({"name": name, "ok": bool(ok), "detail": detail})
            return ok

        check("M05 시나리오 ID 일치(실행 기록 ↔ 시나리오 파일)", manifest.get("scenario_id") == scenario.get("scenario_id"),
              str(scenario.get("scenario_id")))
        check("M07 통과(평가 날짜 모두)", str(plan.get("m07", "")).split("/")[0] == str(len(days)) and report.get("pass")
              and not report.get("failed_checks"), f"{plan.get('m07')} · 대표 날짜 {day} pass={report.get('pass')}")
        ok_loop, why_loop = loop_check(timetable)
        check("순환 운행(편도 구간 없음, 고리 닫기 ≤ 300초)", ok_loop, why_loop)
        check("시간표 plan_id·revision = 운행안", timetable.get("plan_id") == raw_plan.get("plan_id")
              and timetable.get("plan_revision") == raw_plan.get("revision"),
              f"{timetable.get('plan_id')}@{timetable.get('plan_revision')}")
        stops = self._stops(region, timetable)
        check("영업 정차점 좌표", stops["missing"] == [], f"정차점 {stops['n']}개" +
              (f", 좌표 없음 {stops['missing'][:5]}" if stops["missing"] else ""))
        ins = [t for t in timetable.get("trips", []) if t.get("in_service")]
        first = min((t["stop_times"][0]["sched_s"] for t in ins), default=None)
        last = max((t["stop_times"][-1]["sched_s"] for t in ins), default=None)
        bundle_plan = {**raw_plan, "scenario_id": scenario["scenario_id"], "status": "candidate",
                       "source": {"kind": "busstrike.pipeline", "plan_ref": plan_ref, "run_id": run or "default",
                                  "plan_sha256": plan["plan_sha256"]}}
        return {"doc": doc, "plan": plan, "day": day, "files": files, "scenario": scenario, "timetable": timetable,
                "report": report, "bundle_plan": bundle_plan, "stops_csv": stops["csv"], "checks": checks,
                "rc": rc, "job": job, "out": out,
                "summary": {"plan_ref": plan_ref, "algo_name": plan.get("algo_name"), "floors": plan.get("floors"),
                            "fleet": fleet, "routes": plan.get("routes"),
                            "vehicles": sum(int(r.get("vehicles", 0)) for r in raw_plan.get("routes", [])),
                            "service": None if first is None else f"{_hhmm(first)}~{_hhmm(last)}",
                            "service_date": scenario.get("service_date"), "eval_days": days, "day": day,
                            "metrics": metrics_of(plan, doc.get("demand_total")), "m07": plan.get("m07"),
                            "plan_id": raw_plan.get("plan_id"), "revision": raw_plan.get("revision"),
                            "plan_sha256": plan["plan_sha256"], "run_id": run or "default"}}

    def _stops(self, region: str, timetable: dict) -> dict:
        used = sorted({x["stop_id"] for t in timetable.get("trips", []) if t.get("in_service") for x in t["stop_times"]})
        root = Path(self.paths.work_root) / "region_all" / f"{region}_0610_v4" / "network" / "stops.csv"
        rows = {}
        with root.open(encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                if r["stop_id"] in used:
                    rows[r["stop_id"]] = r
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(["stop_id", "stop_lat", "stop_lon", "stop_name"])
        for s in used:
            if s in rows:
                w.writerow([s, rows[s]["stop_lat"], rows[s]["stop_lon"], rows[s]["stop_name"]])
        return {"csv": buf.getvalue(), "n": len(used), "missing": [s for s in used if s not in rows]}

    def _write_bundle(self, g: dict, d: Path) -> dict:
        d.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(g["files"]["scenario"], d / FILES["scenario"])
        (d / FILES["plan"]).write_text(json.dumps(g["bundle_plan"], ensure_ascii=False, indent=1), encoding="utf-8")
        shutil.copyfile(g["files"]["timetable"], d / FILES["timetable"])
        shutil.copyfile(g["files"]["m07_report"], d / FILES["m07_report"])
        (d / FILES["stops"]).write_text(g["stops_csv"], encoding="utf-8")
        return {k: {"path": v, "sha256": sha256(d / v)} for k, v in FILES.items()}

    def _shuttle_check(self, d: Path) -> tuple[bool, str, dict | None]:
        """citizen-routing backend.shuttle_export.export_checked를 그대로 부른다(GTFS는 임시 폴더, 바로 지움)."""
        cr = REPO / "citizen-routing"
        if not (cr / "backend" / "shuttle_export.py").is_file():
            return False, "citizen-routing/backend/shuttle_export.py가 없음", None
        if str(cr) not in sys.path:
            sys.path.insert(0, str(cr))
        try:
            from backend.shuttle_export import export_checked
        except Exception as e:  # noqa: BLE001
            return False, f"shuttle_export를 불러오지 못함: {e!r}", None
        tmp = d.parent / (d.name + ".gtfs-check")
        try:
            meta = export_checked(d / FILES["scenario"], d / FILES["plan"], d / FILES["timetable"],
                                  d / FILES["m07_report"], d / FILES["stops"], tmp, demo=False)
            return True, f"셔틀 GTFS 사전 내보내기 통과(정차점 {meta['stats'].get('stops', '?')}개, " \
                         f"운행 {meta['stats'].get('trips', '?')}회)", {"stats": meta.get("stats"), "source_kind": meta.get("source_kind")}
        except Exception as e:  # noqa: BLE001 — 검사 실패 사유를 화면에
            return False, f"shuttle_export 검사 실패: {e}", None
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _schema_check(self, doc: dict) -> tuple[bool, str]:
        try:
            import jsonschema
        except ImportError:
            return True, "jsonschema 없음 — 필수 키만 확인"
        try:
            jsonschema.validate(doc, json.loads(SCHEMA.read_text(encoding="utf-8")))
            return True, "confirm.schema.json 통과"
        except jsonschema.ValidationError as e:
            return False, f"스키마 불일치: {e.message}"

    # ------------------------------------------------------------ 공개
    def preview(self, region: str, run: str | None, plan_ref: str) -> dict:
        if self.root is None:
            raise DataError("DATA_INCOMPLETE", "확정 묶음 폴더(confirm_root)가 설정되지 않았습니다.")
        g = self.gather(region, run, plan_ref)
        tmp = self.root / ".tmp" / f"preview-{os.getpid()}-{time.time_ns()}"
        try:
            self._write_bundle(g, tmp)
            ok, detail, _ = self._shuttle_check(tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        checks = g["checks"] + [{"name": "시민 셔틀 내보내기 사전 검사(shuttle_export)", "ok": ok, "detail": detail}]
        return {"status": "available", "ok": all(c["ok"] for c in checks), "checks": checks, **g["summary"],
                "citizen": {"api": bool(self.paths.citizen_api), "ui": self.paths.citizen_ui or None}}

    def confirm(self, region: str, run: str | None, plan_ref: str) -> dict:
        if self.root is None:
            raise DataError("DATA_INCOMPLETE", "확정 묶음 폴더(confirm_root)가 설정되지 않았습니다.")
        g = self.gather(region, run, plan_ref)
        bad = [c for c in g["checks"] if not c["ok"]]
        if bad:
            raise DataError("PLAN_INFEASIBLE", "확정 전 검사 실패: " + "; ".join(f"{c['name']} — {c['detail']}" for c in bad),
                            checks=g["checks"])
        t = datetime.now(KST)
        cid = t.strftime("%Y%m%dT%H%M%S") + "-" + g["plan"]["plan_sha256"][:8]
        gdir = self.root / region
        tmp = gdir / f".{cid}.partial"
        final = gdir / cid
        if final.exists():
            raise DataError("INPUT_INVALID", "같은 확정 ID가 이미 있습니다. 잠시 뒤 다시 누르세요.")
        try:
            files = self._write_bundle(g, tmp)
            ok, detail, meta = self._shuttle_check(tmp)
            g["checks"].append({"name": "시민 셔틀 내보내기 사전 검사(shuttle_export)", "ok": ok, "detail": detail})
            if not ok:
                raise DataError("PLAN_INFEASIBLE", detail, checks=g["checks"])
            s = g["summary"]
            job, rc = g["job"], g["rc"]
            input_sha = job.get("input_hash") or (sha256(g["out"] / "run_config.json")
                                                  if (g["out"] / "run_config.json").is_file() else None)
            strike = (job.get("input") or {}).get("strike") or {"mode": "full" if float(g["doc"].get("strike", 1)) == 1.0
                                                                 else "ratio", "ratio": float(g["doc"].get("strike", 1)),
                                                                 "seed": 20261121}
            doc = {"schema_version": 1, "confirm_id": cid, "confirmed_at": t.strftime("%Y-%m-%dT%H:%M:%S+09:00"),
                   "gu": region, "fleet": s["fleet"], "strike": strike, "run_id": s["run_id"], "plan_ref": plan_ref,
                   "plan_id": s["plan_id"], "revision": s["revision"], "plan_sha256": s["plan_sha256"],
                   "algo_name": s["algo_name"] or "", "service_date": s["service_date"], "metrics": s["metrics"],
                   "m07": {"pass": True, "fully_verified": bool(g["report"].get("fully_verified")),
                           "failed_checks": list(g["report"].get("failed_checks") or []), "days": str(s["m07"])},
                   "code_commit": (rc.get("created") or {}).get("code"), "input_sha256": input_sha,
                   "precheck": {"checks": g["checks"], "shuttle_export": meta}, "files": files}
            ok2, why2 = self._schema_check(doc)
            if not ok2:
                raise DataError("PLAN_INFEASIBLE", why2)
            (tmp / "confirm.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, final)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        cur = gdir / "CURRENT"
        ctmp = gdir / ".CURRENT.tmp"
        ctmp.write_text(cid + "\n", encoding="utf-8")
        os.replace(ctmp, cur)
        if self.selections is not None:
            self.selections.record_pareto({"region": region, "confirm_id": cid, "run_id": s["run_id"], "plan_ref": plan_ref,
                                           "plan_id": s["plan_id"], "revision": s["revision"],
                                           "plan_sha256": s["plan_sha256"], "algo_name": s["algo_name"],
                                           "metrics": s["metrics"], "m07": s["m07"], "status": "confirmed",
                                           "confirmed_at": doc["confirmed_at"],
                                           "bundle": str(final)})
        citizen = self.apply(region, cid)
        return {"status": "confirmed", "confirm_id": cid, "path": str(final), "checks": g["checks"], **s,
                "citizen": citizen}

    def apply(self, region: str, cid: str) -> dict:
        """프롬프트 B의 POST /api/citizen-routing/apply. 없거나 꺼져 있으면 '시민 경로 반영 대기'."""
        api, ui = self.paths.citizen_api, self.paths.citizen_ui or None
        if not api:
            return {"state": "pending", "label": "시민 경로 반영 대기(시민 서버 주소 미설정)", "ui": ui}
        body = json.dumps({"gu": region, "confirm_id": cid}).encode("utf-8")
        req = urllib.request.Request(f"{api}/api/citizen-routing/apply", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                got = json.loads(r.read().decode("utf-8") or "{}")
            return {"state": "requested", "label": "시민 경로 반영 요청됨(파업 그래프 다시 빌드 중)", "response": got, "ui": ui}
        except urllib.error.HTTPError as e:            # 시민 서버가 거절(409 다른 반영 진행 중, 503 설정 없음 등)
            try:
                why = json.loads(e.read().decode("utf-8") or "{}").get("message") or e.reason
            except (OSError, ValueError):
                why = e.reason
            return {"state": "pending", "label": f"시민 경로 반영 대기(시민 서버 {e.code}: {why})", "ui": ui}
        except (urllib.error.URLError, OSError, ValueError) as e:
            return {"state": "pending", "label": f"시민 경로 반영 대기(apply 응답 없음: {getattr(e, 'reason', e)})", "ui": ui}

    def citizen_status(self, region: str, cid: str) -> dict:
        """이 확정이 시민 경로에 반영됐는지. 안끼리 plan_id@revision이 같을 수 있어(plan-eqmc07-f11@1)
        시민 쪽 활성 그래프의 confirm_id·plan_sha256이 이 확정과 같을 때만 '반영 완료'다."""
        api, ui = self.paths.citizen_api, self.paths.citizen_ui or None
        if not api:
            return {"state": "unconfigured", "label": "시민 경로 반영 대기(시민 서버 주소 미설정)", "ui": ui}
        if not cid or "/" in cid or cid.startswith("."):
            raise DataError("INPUT_INVALID", "confirm_id가 필요합니다.")
        want = None
        if self.root is not None:
            p = self.root / region / cid / "confirm.json"
            if p.is_file():
                want = json.loads(p.read_text(encoding="utf-8")).get("plan_sha256")
        url = f"{api}/api/citizen-routing/apply/status?confirm_id={urllib.parse.quote(cid)}"
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                st = json.loads(r.read().decode("utf-8") or "{}")
        except (urllib.error.URLError, OSError, ValueError) as e:
            return {"state": "pending", "label": f"시민 경로 반영 대기(상태 응답 없음: {getattr(e, 'reason', e)})", "ui": ui}
        active = st.get("active") or {}
        keep = {"graph_version": active.get("graph_version"), "build_id": active.get("build_id"),
                "plan_key": active.get("plan_key"), "activated_at": active.get("activated_at"), "ui": ui}
        if active.get("confirm_id") == cid and (want is None or active.get("plan_sha256") == want) \
                and st.get("state") != "running":
            return {"state": "applied", "label": f"시민 경로 반영 완료(파업 그래프 {active.get('graph_version')})", **keep}
        mine = st.get("confirm_id") in (cid, None)
        if st.get("state") == "running" and mine:
            return {"state": "applying", "step": st.get("step"), "label": f"시민 경로 반영 중({st.get('step') or '시작'})", **keep}
        if st.get("state") in ("failed", "interrupted", "cancelled") and st.get("confirm_id") == cid:
            msg = (st.get("error") or {}).get("message") or st.get("state")
            return {"state": "failed", "label": f"시민 경로 반영 실패: {msg} (이전 그래프로 안내 중)", **keep}
        if active.get("confirm_id"):
            return {"state": "other", "label": f"시민 경로에는 다른 확정({active.get('confirm_id')})이 적용 중", **keep}
        return {"state": "pending", "label": "시민 경로 반영 대기", **keep}

    def current(self, region: str) -> dict:
        if self.root is None:
            return {"status": "unavailable"}
        cur = self.root / region / "CURRENT"
        if not cur.is_file():
            return {"status": "none", "region": region}
        cid = cur.read_text(encoding="utf-8").strip()
        p = self.root / region / cid / "confirm.json"
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"status": "broken", "region": region, "confirm_id": cid}
        keep = ("confirm_id", "confirmed_at", "plan_ref", "plan_id", "revision", "algo_name", "metrics", "m07", "fleet", "run_id")
        return {"status": "available", "region": region, **{k: doc.get(k) for k in keep},
                "citizen_ui": self.paths.citizen_ui or None}
