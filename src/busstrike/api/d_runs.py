"""운영자 대시보드 실행 버튼(작업 실행기). /api/d/runs* 가 부른다.

작업 = <pareto_root>/<구>/runs/<run_id>/
  job.json      입력(구, 파업, 대수, 생성 수요 기간, 평가 날짜, 설정)과 작업 해시, 만든 시각
  status.json   상태(queued·assembling·generating·allocating·simulating·verifying·metrics·done·failed·cancelled),
                단계별 진행(별도 프로세스 d_runjob이 씀)
  run.log       단계·개수·시간만(카드 ID·승객 단위 정보 없음)
  pareto.json, export/   파이프라인 출력(d_pareto가 읽음)
- 파이프라인은 별도 프로세스(새 세션)로 돈다. 서버를 꺼도 계속 돌고, 다시 켜면 살아 있는 프로세스를 이어 지켜본다.
  프로세스가 죽어 있으면 대기열에 다시 넣어 이어 돌린다(파이프라인이 끝난 조합을 건너뜀).
- 동시에 1개만 돈다(나머지는 대기열). 같은 구 파이프라인이 두 번 겹치지 않는다.
- 작업 해시(입력 해시 + 대수 + 설정)가 같고 끝난 작업이 있으면 새로 돌리지 않고 그 결과를 연다(이전 결과 재사용).
- "전체" 설정은 미리 계산된 결과(precomputed_root/<구>/pareto.json)가 있으면 그것을 여는 작업(source=precomputed)을 만든다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from .d_geo import DataError
from .d_runjob import SETTINGS, STAGES, now, write_json

REPO = Path(__file__).resolve().parents[3]
GU_RE = re.compile(r"^[a-z_]+$")
RUN_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")
ENABLED_GU = {"guro"}                 # 이번 E2E에서 실행할 수 있는 구(나머지는 "준비 중")
EXP_WEEK = ("20261026", "20261027", "20261028", "20261029", "20261030")
DEFAULT_EST = {"quick": {"assemble": 60, "generate": 600, "allocate": 60, "simulate": 120, "verify": 2,
                         "allocate2": 120, "simulate2": 150, "verify2": 2, "metrics": 30},
               "full": {"assemble": 60, "generate": 2400, "allocate": 300, "simulate": 300, "verify": 2,
                        "allocate2": 1800, "simulate2": 900, "verify2": 2, "metrics": 120}}
# 로그 줄 필터: 카드 ID·승객 단위 정보가 섞이면 그 줄을 내보내지 않는다(파이프라인 로그에는 원래 없음)
PRIVATE = re.compile(r"(?i)trcr|card[_ ]?id|카드\s*id|passenger_id|승객\s*id|\b[0-9a-f]{32,}\b")


def _read(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _alive(pid: int | None, run_id: str) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError):
        return False
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
    except OSError:
        return True
    return "d_runjob" in cmd and run_id in cmd


def tail(p: Path, n: int = 8) -> list[str]:
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]
    except OSError:
        return []
    return [x for x in lines if not PRIVATE.search(x)][-n:]


def normalize_input(req: dict, paths) -> dict:
    from busstrike.data.assemble import AssembleError, muni_fleet, normalize_strike
    gu = str(req.get("gu") or req.get("region") or "")
    if not GU_RE.match(gu):
        raise DataError("INPUT_INVALID", "구 ID가 잘못되었습니다.")
    if gu not in ENABLED_GU:
        raise DataError("OUT_OF_SCOPE", f"{gu}: 준비 중(이번엔 구로만 실행할 수 있습니다).")
    setting = req.get("setting", "quick")
    if setting not in SETTINGS:
        raise DataError("INPUT_INVALID", "실행 설정은 quick(빠른) 또는 full(전체)입니다.")
    try:
        strike = normalize_strike(req.get("strike") or {"mode": "full"})
    except AssembleError as e:
        raise DataError("INPUT_INVALID", str(e)) from e
    fleet = req.get("fleet")
    if fleet in (None, ""):                            # 비우면 지자체안 대수(0은 기본값으로 바꾸지 않고 거절)
        fleet = muni_fleet(gu, paths)
    try:
        fleet = int(fleet)
    except (TypeError, ValueError) as e:
        raise DataError("INPUT_INVALID", "가용 대수가 필요합니다(지자체안 변환본이 없어 기본값을 정할 수 없음).") from e
    if not 1 <= fleet <= 60:
        raise DataError("INPUT_INVALID", "가용 대수는 1~60대입니다.")
    days = lambda v, d: sorted({str(x).replace("-", "") for x in (v or d)})  # noqa: E731
    demand_days = days(req.get("demand_days"), EXP_WEEK)
    eval_days = days(req.get("eval_days"), ["20261028"] if setting == "quick" else EXP_WEEK[1:])
    if any(d.startswith("202611") and "02" <= d[6:] <= "06" for d in demand_days + eval_days):
        raise DataError("INPUT_INVALID", "테스트 주간(11/02~06)은 고를 수 없습니다.")
    if set(eval_days) - set(EXP_WEEK) or not eval_days:
        raise DataError("INPUT_INVALID", "평가 날짜는 실험 주간 10/26~30 안에서 고릅니다.")
    if demand_days != list(EXP_WEEK):
        raise DataError("INPUT_INVALID", "생성 수요 기간은 10/26~30(실험 주간 전체)만 지원합니다.")
    source = req.get("source", "precomputed" if setting == "full" else "new")
    if source not in ("new", "precomputed"):
        raise DataError("INPUT_INVALID", "source는 new 또는 precomputed입니다.")
    return {"gu": gu, "strike": strike, "fleet": fleet, "demand_days": demand_days, "eval_days": eval_days,
            "setting": setting, "source": source}


class RunManager:
    def __init__(self, paths, start_worker: bool = True, python: str | None = None):
        self.paths = paths
        self.root = Path(paths.pareto_root) if paths.pareto_root else None
        self.python = python or sys.executable
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.current: str | None = None
        self.procs: dict[str, subprocess.Popen] = {}
        if self.root is not None and start_worker:
            self._recover()
            threading.Thread(target=self._loop, daemon=True, name="d-runs").start()

    # ------------------------------------------------------------ 저장소
    def _dir(self, gu: str, run_id: str) -> Path:
        if self.root is None or not GU_RE.match(gu or "") or not RUN_RE.match(run_id or ""):
            raise DataError("INPUT_INVALID", "구 또는 작업 ID가 잘못되었습니다.")
        return self.root / gu / "runs" / run_id

    def _jobs(self) -> list[tuple[Path, dict]]:
        if self.root is None or not self.root.is_dir():
            return []
        out = []
        for jp in sorted(self.root.glob("*/runs/*/job.json")):
            j = _read(jp)
            if j:
                out.append((jp.parent, j))
        return out

    def _status(self, d: Path) -> dict:
        return _read(d / "status.json") or {"state": "queued"}

    def _set(self, d: Path, **kw) -> None:
        s = self._status(d)
        s.update(kw, updated_at=now())
        write_json(d / "status.json", s)

    # ------------------------------------------------------------ 공개 API
    def available(self) -> bool:
        return self.root is not None

    def submit(self, req: dict) -> dict:
        if self.root is None:
            raise DataError("DATA_INCOMPLETE", "실행 결과 폴더(pareto_root)가 설정되지 않았습니다(config/local_paths.toml).")
        inp = normalize_input(req, self.paths)
        from busstrike.data.assemble import AssembleError, assemble
        if inp["source"] == "precomputed":
            return self._precomputed(inp)
        try:
            got = assemble(inp["gu"], inp["strike"], inp["demand_days"], inp["eval_days"], paths=self.paths)
        except AssembleError as e:
            raise DataError("DATA_INCOMPLETE", str(e), missing=e.missing) from e
        job_hash = hashlib.sha256(json.dumps([got["input_hash"], inp["fleet"], inp["setting"]]).encode()).hexdigest()
        with self.lock:
            for d, j in reversed(self._jobs()):
                if j.get("job_hash") != job_hash:
                    continue
                s = self._status(d)
                if s.get("state") == "done":
                    return {**self._view(d, j, s), "reused": True, "assemble_seconds": got["seconds"]}
                if s.get("state") in ("failed", "cancelled"):        # 같은 입력이면 같은 폴더에서 이어 돌린다
                    self._set(d, state="queued", stage="queued", stage_label="대기", error=None, resumed=True)
                    self.wake.set()
                    return {**self._view(d, j, self._status(d)), "reused": False, "resumed": True}
                return {**self._view(d, j, s), "reused": False, "in_progress": True}
            run_id = time.strftime("%Y%m%d-%H%M%S-") + job_hash[:6]
            d = self._dir(inp["gu"], run_id)
            d.mkdir(parents=True, exist_ok=False)
            job = {"schema": "busstrike.d_run/1", "run_id": run_id, "gu": inp["gu"], "input": inp,
                   "input_hash": got["input_hash"], "job_hash": job_hash, "manifest_path": got["manifest_path"],
                   "created_at": now(), "seq": time.time_ns(), "out_dir": str(d), "config": self.paths.source}
            write_json(d / "job.json", job)
            write_json(d / "status.json", {"state": "queued", "stage": "queued", "stage_label": "대기", "created_at": now(),
                                           "stages": [{"key": k, "label": lab, "state": "pending", "done": 0, "total": 0}
                                                      for k, lab in STAGES]})
        self.wake.set()
        return {**self._view(d, job, self._status(d)), "reused": False, "assemble_seconds": got["seconds"]}

    def _precomputed(self, inp: dict) -> dict:
        pre = Path(self.paths.precomputed_root) / inp["gu"] if self.paths.precomputed_root else None
        if pre is None or not (pre / "pareto.json").is_file():
            raise DataError("DATA_INCOMPLETE", "미리 계산된 결과(요인 실험)가 없습니다. '새로 돌리기'를 고르세요.")
        doc = _read(pre / "pareto.json") or {}
        want = (inp["fleet"], float(inp["strike"]["ratio"]), inp["eval_days"])
        have = (doc.get("fleet"), float(doc.get("strike", 0)), sorted(doc.get("eval_days", [])))
        if want != have:
            raise DataError("INPUT_INVALID", f"미리 계산된 결과는 {have[0]}대·파업 {have[1]}·평가 {','.join(have[2])} 조건만 있습니다.")
        job_hash = hashlib.sha256(json.dumps(["precomputed", str(pre), (pre / "pareto.json").stat().st_mtime_ns]).encode()).hexdigest()
        with self.lock:
            for d, j in self._jobs():
                if j.get("job_hash") == job_hash:
                    return {**self._view(d, j, self._status(d)), "reused": True}
            run_id = time.strftime("%Y%m%d-%H%M%S-") + job_hash[:6]
            d = self._dir(inp["gu"], run_id)
            d.mkdir(parents=True)
            rc = _read(pre / "run_config.json") or {}
            job = {"schema": "busstrike.d_run/1", "run_id": run_id, "gu": inp["gu"], "input": inp, "job_hash": job_hash,
                   "input_hash": None, "created_at": now(), "out_dir": str(pre), "precomputed": True,
                   "note": "미리 계산된 결과(요인 실험). 다시 계산하지 않음. 이 결과는 역 색인에 테스트 주간 흐름을 넣던 옛 방식으로 만들어졌다",
                   "code_commit": (rc.get("created") or {}).get("code")}
            write_json(d / "job.json", job)
            write_json(d / "status.json", {"state": "done", "stage": "done", "stage_label": "완료(미리 계산된 결과)",
                                           "created_at": now(), "ended_at": now(), "stages": []})
            with (d / "run.log").open("a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%H:%M:%S')} 미리 계산된 결과를 연결: {pre.name}\n")
        return {**self._view(d, job, self._status(d)), "reused": False}

    def get(self, gu: str, run_id: str) -> dict:
        d = self._dir(gu, run_id)
        j = _read(d / "job.json")
        if not j:
            raise DataError("DATA_INCOMPLETE", "작업이 없습니다.")
        return self._view(d, j, self._status(d))

    def find(self, run_id: str) -> tuple[Path, dict] | None:
        if not RUN_RE.match(run_id or ""):
            return None
        for d, j in self._jobs():
            if j.get("run_id") == run_id:
                return d, j
        return None

    def get_any(self, run_id: str) -> dict:
        hit = self.find(run_id)
        if not hit:
            raise DataError("DATA_INCOMPLETE", "작업이 없습니다.")
        return self._view(hit[0], hit[1], self._status(hit[0]))

    def list(self, gu: str | None = None) -> dict:
        rows = [self._view(d, j, self._status(d), brief=True) for d, j in self._jobs() if not gu or j.get("gu") == gu]
        rows.sort(key=lambda r: r["created_at"] or "", reverse=True)
        return {"status": "available" if self.root else "unavailable", "runs": rows, "current": self.current}

    def cancel(self, run_id: str) -> dict:
        hit = self.find(run_id)
        if not hit:
            raise DataError("DATA_INCOMPLETE", "작업이 없습니다.")
        d, j = hit
        with self.lock:
            s = self._status(d)
            if s.get("state") in ("done", "failed", "cancelled"):
                return self._view(d, j, s)
            pid = s.get("pid")
            proc = self.procs.get(run_id)
            if proc is not None and proc.poll() is None:   # 이 서버가 띄운 프로세스: 시작 직후(exec 전)라도 확실히 끈다
                try:
                    os.killpg(proc.pid, signal.SIGTERM)          # start_new_session이라 그룹 ID = pid
                except OSError:
                    pass
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
            elif s.get("state") != "queued" and _alive(pid, run_id):   # 서버 재시작 뒤 이어 지켜보던 프로세스
                try:
                    os.killpg(os.getpgid(int(pid)), signal.SIGTERM)
                except OSError:
                    pass
                for _ in range(50):
                    if not _alive(pid, run_id):
                        break
                    time.sleep(0.1)
            s = self._status(d)
            if s.get("state") not in ("cancelled", "done", "failed"):
                self._set(d, state="cancelled", stage="cancelled", stage_label="취소", ended_at=now(), pid=None,
                          error="취소됨")
        return self._view(d, j, self._status(d))

    # ------------------------------------------------------------ 보기
    def _eta(self, j: dict, s: dict) -> int | None:
        if s.get("state") in ("done", "failed", "cancelled") or j.get("precomputed"):
            return None
        setting = j["input"]["setting"]
        est = dict(DEFAULT_EST[setting])
        for d2, j2 in self._jobs():                    # 같은 설정의 마지막 완료 작업에서 단계 시간을 배운다
            s2 = self._status(d2)
            if j2.get("run_id") != j["run_id"] and j2["input"].get("setting") == setting and s2.get("state") == "done" \
                    and not j2.get("precomputed"):
                for st in s2.get("stages", []):
                    if st.get("seconds") is not None and st.get("state") == "done":
                        est[st["key"]] = st["seconds"]
        left = 0.0
        for st in s.get("stages", []):
            e = est.get(st["key"], 0)
            if st.get("state") in ("done", "skipped"):
                continue
            if st.get("state") == "running" and st.get("total"):
                left += e * max(0.0, 1 - st.get("done", 0) / st["total"])
            else:
                left += e
        return int(left)

    def _progress(self, s: dict) -> float:
        sts = s.get("stages") or []
        if s.get("state") == "done":
            return 1.0
        if not sts:
            return 0.0
        w = {k: v for k, v in DEFAULT_EST["quick"].items()}
        tot = sum(w.get(st["key"], 1) for st in sts)
        got = 0.0
        for st in sts:
            ww = w.get(st["key"], 1)
            if st.get("state") in ("done", "skipped"):
                got += ww
            elif st.get("state") == "running" and st.get("total"):
                got += ww * min(1.0, st.get("done", 0) / st["total"])
        return round(got / tot, 3)

    def _view(self, d: Path, j: dict, s: dict, brief: bool = False) -> dict:
        out_dir = Path(j.get("out_dir") or d)
        v = {"run_id": j["run_id"], "gu": j["gu"], "input": j["input"], "created_at": j.get("created_at"),
             "precomputed": bool(j.get("precomputed")), "note": j.get("note"),
             "state": s.get("state"), "stage": s.get("stage"), "stage_label": s.get("stage_label"),
             "progress": self._progress(s), "eta_s": self._eta(j, s), "error": s.get("error"),
             "started_at": s.get("started_at"), "ended_at": s.get("ended_at"),
             "has_pareto": (out_dir / "pareto.json").is_file()}
        if not brief:
            v.update({"stages": [{k: x.get(k) for k in ("key", "label", "state", "done", "total", "seconds", "note")}
                                 for x in s.get("stages", [])],
                      "log_tail": tail(d / "run.log"), "range": _read(d / "range.json"),
                      "input_hash": j.get("input_hash"), "elapsed_s": _elapsed(s)})
        return v

    # ------------------------------------------------------------ 실행 고리
    def _recover(self) -> None:
        """서버 재시작: 살아 있는 작업 프로세스는 이어 지켜보고, 죽은 '실행 중' 작업은 대기열로 돌린다."""
        for d, j in self._jobs():
            s = self._status(d)
            if s.get("state") in ("done", "failed", "cancelled", "queued"):
                continue
            if _alive(s.get("pid"), j["run_id"]):
                self.current = j["run_id"]
            else:
                self._set(d, state="queued", stage="queued", stage_label="대기(서버 재시작 후 이어 돌리기)", pid=None,
                          resumed=True)
                with (d / "run.log").open("a", encoding="utf-8") as f:
                    f.write(f"{time.strftime('%H:%M:%S')} 서버 재시작: 작업 프로세스가 없어 대기열로 돌림(이어 돌리기)\n")

    def _next(self) -> tuple[Path, dict] | None:
        q = [(d, j) for d, j in self._jobs() if self._status(d).get("state") == "queued"]
        q.sort(key=lambda x: (x[1].get("created_at") or "", x[1].get("seq") or 0))   # 같은 초에 낸 작업도 낸 순서대로
        return q[0] if q else None

    def _loop(self) -> None:
        while True:
            try:
                self._tick()
            except Exception as e:  # noqa: BLE001 — 실행 고리는 멈추지 않는다
                print(f"[d-runs] {e!r}", file=sys.stderr)
            self.wake.wait(2.0)
            self.wake.clear()

    def _tick(self) -> None:
        with self.lock:
            if self.current:
                hit = self.find(self.current)
                if hit:
                    s = self._status(hit[0])
                    proc = self.procs.get(self.current)
                    if proc is not None and proc.poll() is None:
                        return
                    if proc is None and _alive(s.get("pid"), self.current):
                        return
                    self.procs.pop(self.current, None)
                    if s.get("state") not in ("done", "failed", "cancelled"):
                        self._set(hit[0], state="failed", stage="failed", stage_label="실패", pid=None, ended_at=now(),
                                  error=s.get("error") or "작업 프로세스가 끝났지만 완료 기록이 없다(run.log 확인)")
                self.current = None
            nxt = self._next()
            if not nxt:
                return
            d, j = nxt
            log = (d / "run.log").open("a", encoding="utf-8")
            proc = subprocess.Popen([self.python, "-m", "busstrike.api.d_runjob", str(d)], cwd=REPO,
                                    env={**os.environ, "PYTHONPATH": str(REPO / "src")}, stdout=log, stderr=log,
                                    stdin=subprocess.DEVNULL, start_new_session=True)
            log.close()
            self.procs[j["run_id"]] = proc
            self.current = j["run_id"]
            self._set(d, state="assembling", stage="assemble", stage_label="조립", pid=proc.pid)


def _elapsed(s: dict) -> int | None:
    import datetime as dt
    a = s.get("started_at")
    if not a:
        return None
    b = s.get("ended_at") or now()
    f = "%Y-%m-%dT%H:%M:%S"
    return int((dt.datetime.strptime(b, f) - dt.datetime.strptime(a, f)).total_seconds())
