"""운영자 실행 작업 하나를 별도 프로세스로 돌린다(대시보드 서버가 띄움). 진행 상황은 <run_dir>/status.json에 쓴다.

    python -m busstrike.api.d_runjob <run_dir>

<run_dir> = <pareto_root>/<구>/runs/<run_id>/ — job.json(입력)을 읽고, 같은 폴더를 파이프라인 --out으로 쓴다
(pareto.json·export/가 여기에 생겨 d_pareto가 그대로 읽는다). 단계:
  조립(assemble + 수요 묶음) → 생성(형평 기준 none, 후보 캐시) → 배분(하한 1.0 = 형평 상단 찾기) → 시뮬레이션(none 안)
  → 검증(M07) → [lo·hi로 형평 단계 계산] → 배분(형평 단계) → 시뮬레이션 → 검증 → 지표(pareto.json, export/) → 완료
- 형평 단계: 요인 실험 지시문 2.2절 규칙을 단계 수만 줄여 쓴다(steps_between). 빠른 = 3단계, 전체 = 8단계.
- 이어 돌리기: 파이프라인이 끝난 조합·평가를 건너뛰므로 같은 폴더로 다시 부르면 이어서 돈다.
- 테스트 주간 자료를 열지 않는다(RunConfig.test_flows=False, 평가 날짜는 실험 주간만).
- 로그(run.log)에는 단계·개수·시간만 쓴다. 카드 ID·승객 단위 정보는 쓰지 않는다.
"""

from __future__ import annotations

import json
import math
import os
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

STAGES = [("assemble", "조립"), ("generate", "생성"), ("allocate", "배분"), ("simulate", "시뮬레이션"),
          ("verify", "검증"), ("allocate2", "배분(형평 단계)"), ("simulate2", "시뮬레이션(형평 단계)"),
          ("verify2", "검증(형평 단계)"), ("metrics", "지표")]
STATE_OF = {"assemble": "assembling", "generate": "generating", "allocate": "allocating", "allocate2": "allocating",
            "simulate": "simulating", "simulate2": "simulating", "verify": "verifying", "verify2": "verifying",
            "metrics": "metrics"}
QUICK_ALGOS = ["feeder_lg0_fill0", "feeder_lg1_fill1", "sector_lg1_fill1", "oneway_loop_lg0_fill0"]
SETTINGS = {"quick": {"algos": QUICK_ALGOS, "steps": 3, "fine_below": 0.03},
            "full": {"algos": "all", "steps": 8, "fine_below": 0.08}}


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def write_json(p: Path, data: dict) -> None:
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


# ---------------------------------------------------------------- 형평 단계(요인 실험 지시문 2.2절)
def lo_of(none_plans: list[dict]) -> float | None:
    """lo = 기준 없음 안들 중 충족률 최대 안의 형평성, 1%p 올림(동률: 형평성 높은 쪽 → 알고리즘 ID)."""
    ok = [p for p in none_plans if p.get("served_rate") is not None and p.get("equity_far") is not None]
    if not ok:
        return None
    best = sorted(ok, key=lambda p: (-p["served_rate"], -p["equity_far"], p["id"]))[0]
    return math.ceil(100 * best["equity_far"] - 1e-9) / 100


def hi_of(fars: list[float]) -> float | None:
    """hi = 알고리즘별 최대 형평성(역 밖 하한 1.0 배분의 plan_report equity.far) 중 최댓값, 1%p 내림."""
    return math.floor(100 * max(fars) + 1e-9) / 100 if fars else None


def floor_str(v: float) -> str:
    s = f"{v:.3f}".rstrip("0")
    return s + "0" if s.endswith(".") else s


def steps_between(lo: float | None, hi: float | None, n: int, fine_below: float) -> list[str]:
    """lo~hi 사이 n단계: lo + i·(hi − lo)/n(i = 1…n, n번째 = hi), 0.5%p 반올림. hi − lo < fine_below면 lo+0.01부터 0.01 간격.
    hi ≤ lo면 없음. 같은 값은 한 번만."""
    if lo is None or hi is None or hi <= lo:
        return []
    if hi - lo < fine_below - 1e-12:
        vals, v = [], lo + 0.01
        while v <= hi + 1e-9:
            vals.append(round(v, 2))
            v += 0.01
    else:
        vals = [math.floor(200 * (lo + i * (hi - lo) / n) + 0.5) / 200 for i in range(1, n + 1)]
    return list(dict.fromkeys(floor_str(v) for v in vals))


# ---------------------------------------------------------------- 상태 파일
class Status:
    def __init__(self, run_dir: Path):
        self.p = run_dir / "status.json"
        self.log_p = run_dir / "run.log"
        self.lock = threading.Lock()
        try:
            self.s = json.loads(self.p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.s = {}
        old = {x["key"]: x for x in self.s.get("stages", [])}
        self.s.update({"state": "running", "pid": os.getpid(), "error": None, "ended_at": None,
                       "started_at": self.s.get("started_at") or now(), "attempt": int(self.s.get("attempt", 0)) + 1})
        self.s["stages"] = [{"key": k, "label": lab, "state": old.get(k, {}).get("state", "pending"),
                             "started": old.get(k, {}).get("started"), "ended": old.get(k, {}).get("ended"),
                             "seconds": old.get(k, {}).get("seconds"), "done": old.get(k, {}).get("done", 0),
                             "total": old.get(k, {}).get("total", 0), "note": old.get(k, {}).get("note", "")}
                            for k, lab in STAGES]
        self.flush()

    def stage(self, key: str) -> dict:
        return next(x for x in self.s["stages"] if x["key"] == key)

    def begin(self, key: str, total: int = 0, note: str = "") -> None:
        with self.lock:
            st = self.stage(key)
            st.update({"state": "running", "started": now(), "done": 0, "total": total, "note": note, "_t0": time.time()})
            self.s.update({"state": STATE_OF[key], "stage": key, "stage_label": st["label"]})
        self.log(f"[{st['label']}] 시작" + (f" — {note}" if note else ""))
        self.flush()

    def progress(self, key: str, done: int, total: int | None = None, note: str | None = None) -> None:
        with self.lock:
            st = self.stage(key)
            st["done"] = done
            if total is not None:
                st["total"] = total
            if note is not None:
                st["note"] = note
        self.flush()

    def end(self, key: str, note: str | None = None, skipped: bool = False) -> None:
        with self.lock:
            st = self.stage(key)
            t0 = st.pop("_t0", None)
            st.update({"state": "skipped" if skipped else "done", "ended": now(),
                       "seconds": round(time.time() - t0, 1) if t0 else st.get("seconds")})
            if note is not None:
                st["note"] = note
        self.log(f"[{st['label']}] {'건너뜀' if skipped else '끝'} {st.get('seconds') or 0:.0f}초" + (f" — {note}" if note else ""))
        self.flush()

    def finish(self, state: str, error: str | None = None) -> None:
        with self.lock:
            for st in self.s["stages"]:
                st.pop("_t0", None)
                if st["state"] == "running":
                    st["state"] = "failed" if state == "failed" else st["state"]
            self.s.update({"state": state, "stage": state, "stage_label": {"done": "완료", "failed": "실패"}.get(state, state),
                           "error": error, "ended_at": now(), "pid": None})
        self.log(f"[{self.s['stage_label']}]" + (f" {error}" if error else ""))
        self.flush()

    def flush(self) -> None:
        with self.lock:
            self.s["updated_at"] = now()
            out = {**self.s, "stages": [{k: v for k, v in st.items() if not k.startswith("_")} for st in self.s["stages"]]}
            write_json(self.p, out)

    def log(self, line: str) -> None:
        with self.lock, self.log_p.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {line}\n")


class Watch:
    """긴 단계 동안 몇 초마다 파일 수로 진행률을 센다."""

    def __init__(self, fn, every: float = 3.0):
        self.fn, self.every, self.stop = fn, every, threading.Event()
        self.t = threading.Thread(target=self._loop, daemon=True)

    def _loop(self):
        while not self.stop.wait(self.every):
            try:
                self.fn()
            except Exception:  # noqa: BLE001 — 진행률 세기 실패가 작업을 멈추지 않게
                pass

    def __enter__(self):
        self.t.start()
        return self

    def __exit__(self, *a):
        self.stop.set()
        self.t.join(timeout=5)
        try:
            self.fn()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------- 실행
def _gen(P, st: Status, key: str, jobs: list, par: int) -> list[str]:
    fails = []
    todo = [(a, f) for a, f in jobs]
    count = lambda: sum(P.plan_path(a, f).exists() for a, f in todo)  # noqa: E731
    st.progress(key, count(), len(todo))
    with Watch(lambda: st.progress(key, count())), ThreadPoolExecutor(par) as ex:
        futs = {ex.submit(P.gen_one, a, f): (a, f) for a, f in todo}
        for fu, (a, f) in futs.items():
            try:
                fu.result()
                st.log(f"  {a.id} 기준 {f} 끝")
            except Exception as e:  # noqa: BLE001
                fails.append(f"{a.id} 기준 {f}")
                st.log(f"  {a.id} 기준 {f} 실패: {str(e)[:200]}")
    return fails


def _eval(P, st: Status, key: str, rows: list[dict]) -> None:
    from busstrike.pipeline.run import _tag
    tags = sorted({_tag(r["sha"]) for r in rows}) + ["muni"]
    fleet = P.cfg.fleet
    gj = lambda t: P.out / "eval" / "runs" / f"{t}-f{fleet}" / "same_line-w400" / "exp" / "group.json"  # noqa: E731
    count = lambda: sum(gj(t).exists() for t in tags)  # noqa: E731
    st.progress(key, count(), len(tags), f"운행안 {len(tags) - 1}개 + 지자체안 · 평가 {len(P.cfg.eval_days)}일")
    with Watch(lambda: st.progress(key, count())):
        P.evaluate(rows)


def _verify(P, st: Status, key: str, rows: list[dict]) -> tuple[int, int]:
    from busstrike.pipeline.run import _results, _tag
    tags = {_tag(r["sha"]) for r in rows} | {"muni"}
    rs = [r for r in _results(P.out / "eval") if r["label"] in tags and r["date"] in P.cfg.eval_days]
    ok = sum(1 for r in rs if r.get("verify_pass"))
    st.progress(key, ok, len(rs), f"M07 통과 {ok}/{len(rs)}")
    return ok, len(rs)


def run(run_dir: Path) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from busstrike.data import assemble, load_paths
    from busstrike.pipeline import algos as AL
    from busstrike.pipeline.run import Pipeline, RunConfig

    job = json.loads((run_dir / "job.json").read_text(encoding="utf-8"))
    inp = job["input"]
    st = Status(run_dir)
    paths = load_paths(job.get("config") or None)
    setting = SETTINGS[inp["setting"]]
    try:
        # 1 조립
        st.begin("assemble", 2, "입력 파일 검사·manifest, 수요 묶음")
        got = assemble(inp["gu"], inp["strike"], inp["demand_days"], inp["eval_days"], paths=paths)
        write_json(run_dir / "assemble.json", {k: got[k] for k in ("input_hash", "manifest_path", "cached", "seconds")})
        st.progress("assemble", 1)
        algos = [a.id for a in AL.select(setting["algos"] if isinstance(setting["algos"], str) else ",".join(setting["algos"]))]
        base = dict(gu=inp["gu"], strike=float(got["manifest"]["strike"]["ratio"]), fleet=int(inp["fleet"]), algos=algos,
                    eval_days=got["manifest"]["eval_days"], data_root=str(paths.work_root),
                    sweep=str(paths.sweep_dir(inp["gu"])), muni=str(paths.muni_dir(inp["gu"])),
                    par=paths.par, eval_groups=paths.eval_groups, test_flows=False)
        P = Pipeline(RunConfig(floors=["none", "1.0"], **base), run_dir)
        P.write_config()
        P.bundles()
        st.end("assemble", f"manifest {'재사용' if got['cached'] else '새로'} · 입력 {len(got['manifest']['files'])}개")
        A = P.algos
        # 2 생성(기준 없음, 후보 캐시) · 3 배분(하한 1.0)
        st.begin("generate", len(A), f"알고리즘 {len(A)}종 후보 생성 + 기준 없음 배분")
        f1 = _gen(P, st, "generate", [(a, "none") for a in A], paths.par)
        st.end("generate", f"실패 {len(f1)}" if f1 else None)
        st.begin("allocate", len(A), "역 밖 형평 하한 1.0 배분(형평 상단 찾기)")
        f2 = _gen(P, st, "allocate", [(a, "1.0") for a in A], paths.par)
        st.end("allocate", f"실패 {len(f2)}" if f2 else None)
        rows = P.plan_index()
        none_rows = [r for r in rows if r["floor"] == "none"]
        if not none_rows:
            raise RuntimeError("기준 없음 안을 하나도 만들지 못했다(생성 로그 gen/*.log)")
        # 4 시뮬레이션 · 5 검증(기준 없음 안)
        st.begin("simulate")
        _eval(P, st, "simulate", none_rows)
        st.end("simulate")
        st.begin("verify")
        ok, n = _verify(P, st, "verify", none_rows)
        st.end("verify", f"M07 통과 {ok}/{n}")
        doc = P.pareto()
        lo = lo_of([p for p in doc["plans"] if "none" in p["floors"]])
        fars = []
        for a in A:
            rp = P.plan_path(a, "1.0")
            rp = rp.parent / (rp.stem + ".plan_report.json")
            if rp.exists():
                far = (json.loads(rp.read_text(encoding="utf-8")).get("equity") or {}).get("far")
                if far is not None:
                    fars.append(float(far))
        hi = hi_of(fars)
        steps = steps_between(lo, hi, setting["steps"], setting["fine_below"])
        write_json(run_dir / "range.json", {"lo": lo, "hi": hi, "steps": steps, "rule": "요인 실험 지시문 2.2절, 단계 수만 "
                                            f"{setting['steps']}개(빠른·전체 설정)"})
        st.log(f"형평 범위 lo {lo} · hi {hi} → 단계 {steps or '없음'}")
        # 6 배분(형평 단계) · 7 시뮬레이션 · 8 검증
        P = Pipeline(RunConfig(floors=["none", "1.0", *steps], **base), run_dir)
        P.write_config()
        if steps:
            st.begin("allocate2", len(A) * len(steps), f"형평 하한 {', '.join(steps)}")
            f3 = _gen(P, st, "allocate2", [(a, f) for f in steps for a in P.algos], paths.par)
            st.end("allocate2", f"실패 {len(f3)}" if f3 else None)
        else:
            st.end("allocate2", "hi ≤ lo — 형평 단계 없음", skipped=True)
        rows = P.plan_index()
        st.begin("simulate2")
        _eval(P, st, "simulate2", rows)
        st.end("simulate2")
        st.begin("verify2")
        ok, n = _verify(P, st, "verify2", rows)
        st.end("verify2", f"M07 통과 {ok}/{n}")
        # 9 지표
        st.begin("metrics", 2, "pareto.json, export/")
        doc = P.pareto()
        st.progress("metrics", 1)
        P.export(doc)
        n_ok = sum(1 for p in doc["plans"] if p.get("served_rate") is not None)
        st.end("metrics", f"점 {n_ok}개 · 제외 {len(doc.get('excluded', []))}개")
        st.finish("done")
        return 0
    except BaseException as e:  # noqa: BLE001 — 실패도 상태 파일에 남긴다
        if isinstance(e, KeyboardInterrupt):
            st.finish("cancelled", "취소됨")
            return 130
        msg = str(e) if isinstance(e, (RuntimeError, SystemExit)) or e.__class__.__name__ == "AssembleError" else repr(e)
        st.log(traceback.format_exc(limit=3).replace("\n", " | ")[:800])
        st.finish("failed", msg[:500])
        return 1


def main(argv: list[str] | None = None) -> int:
    import signal
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 1:
        print("사용: python -m busstrike.api.d_runjob <run_dir>", file=sys.stderr)
        return 2
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    return run(Path(argv[0]).resolve())


if __name__ == "__main__":
    sys.exit(main())
