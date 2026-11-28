"""작업 실행기(d_runs·d_runjob): 형평 단계 규칙, 상태 전이(대기 → 실행 → 완료/실패/취소), 재사용·이어 돌리기,
서버 재시작 복구, 로그 개인정보 거르기. 파이프라인은 돌리지 않는다(가짜 작업 프로세스)."""
import json
import sys
import time

import pytest

import busstrike.data.assemble  # noqa: F401 — sys.modules에 올림
from busstrike.api import d_runs as R
from busstrike.api.d_geo import DataError
from busstrike.api.d_runjob import floor_str, hi_of, lo_of, steps_between
from busstrike.data import LocalPaths


def test_steps_rule_like_factorial():
    # 요인 실험 구로: lo 0.49, hi 0.75, 8단계 → 0.5225→0.525, 0.555, 0.5875→0.59 … 0.75
    assert steps_between(0.49, 0.75, 8, 0.08) == ["0.525", "0.555", "0.59", "0.62", "0.655", "0.685", "0.72", "0.75"]
    assert steps_between(0.49, 0.75, 3, 0.03) == ["0.575", "0.665", "0.75"]
    assert steps_between(0.50, 0.52, 3, 0.03) == ["0.51", "0.52"]            # 좁으면 0.01 간격
    assert steps_between(0.6, 0.6, 3, 0.03) == [] and steps_between(None, 0.7, 3, 0.03) == []
    assert floor_str(0.5) == "0.5" and floor_str(0.525) == "0.525"


def test_lo_hi():
    plans = [{"id": "b", "served_rate": 0.378, "equity_far": 0.4897}, {"id": "a", "served_rate": 0.35, "equity_far": 0.6},
             {"id": "c", "served_rate": None, "equity_far": None}]
    assert lo_of(plans) == 0.49
    assert hi_of([0.7517, 0.56]) == 0.75 and hi_of([]) is None


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    paths = LocalPaths(work_root=tmp_path / "w", pareto_root=tmp_path / "pareto", source="")
    mod = sys.modules["busstrike.data.assemble"]                              # 패키지 속성 assemble은 함수라 모듈을 직접
    monkeypatch.setattr(mod, "muni_fleet", lambda gu, p=None: 11)

    def fake_assemble(gu, strike, dd, ed, paths=None, **k):
        return {"input_hash": "h" + "".join(ed), "manifest_path": "m", "manifest": {"strike": strike, "eval_days": ed},
                "cached": False, "seconds": 0.01}
    monkeypatch.setattr(mod, "assemble", fake_assemble)
    return R.RunManager(paths, start_worker=False)


REQ = {"gu": "guro", "fleet": 11, "eval_days": ["20261028"], "setting": "quick", "source": "new"}


def _status(m, rid):
    return m.get_any(rid)


def test_submit_queue_reuse_and_resume(mgr):
    a = mgr.submit(REQ)
    assert a["state"] == "queued" and a["reused"] is False and R.RUN_RE.match(a["run_id"])
    b = mgr.submit(REQ)                                                       # 같은 입력, 아직 대기 → 같은 작업
    assert b["run_id"] == a["run_id"] and b["in_progress"]
    d = mgr.root / "guro" / "runs" / a["run_id"]
    mgr._set(d, state="done", stage="done")
    c = mgr.submit(REQ)
    assert c["run_id"] == a["run_id"] and c["reused"] is True                 # 끝난 결과 재사용
    mgr._set(d, state="failed", stage="failed", error="x")
    e = mgr.submit(REQ)
    assert e["run_id"] == a["run_id"] and e["resumed"] and _status(mgr, a["run_id"])["state"] == "queued"
    f = mgr.submit({**REQ, "eval_days": ["20261029"]})                        # 다른 입력 → 새 작업
    assert f["run_id"] != a["run_id"]
    assert {r["run_id"] for r in mgr.list("guro")["runs"]} == {a["run_id"], f["run_id"]}


@pytest.mark.parametrize("bad", [{"gu": "gangseo"}, {"gu": "../x"}, {"eval_days": ["20261103"]}, {"eval_days": ["20261025"]},
                                 {"setting": "slow"}, {"fleet": 0}, {"demand_days": ["20261026"]}])
def test_bad_input(mgr, bad):
    with pytest.raises(DataError):
        mgr.submit({**REQ, **bad})


def test_worker_runs_one_at_a_time_and_finishes(mgr, monkeypatch, tmp_path):
    # 가짜 작업 프로세스: status.json을 done으로 쓰고 끝난다
    script = tmp_path / "fake_runjob.py"
    script.write_text("import json,sys,pathlib\nd=pathlib.Path(sys.argv[-1])\ns=json.loads((d/'status.json').read_text())\n"
                      "s.update(state='done',stage='done',stage_label='완료')\n(d/'status.json').write_text(json.dumps(s))\n",
                      encoding="utf-8")
    real = R.subprocess.Popen
    monkeypatch.setattr(R.subprocess, "Popen", lambda cmd, **k: real([sys.executable, str(script), cmd[-1]], **k))
    a = mgr.submit(REQ)
    b = mgr.submit({**REQ, "eval_days": ["20261029"]})
    mgr._tick()
    assert mgr.current == a["run_id"]
    assert _status(mgr, b["run_id"])["state"] == "queued"                      # 동시에 1개
    mgr.procs[a["run_id"]].wait(timeout=20)
    mgr._tick()                                                               # a 끝 확인 → b 시작
    assert _status(mgr, a["run_id"])["state"] == "done" and mgr.current == b["run_id"]
    mgr.procs[b["run_id"]].wait(timeout=20)
    mgr._tick()
    assert _status(mgr, b["run_id"])["state"] == "done"


def test_process_exit_without_record_is_failed(mgr, monkeypatch):
    real = R.subprocess.Popen
    monkeypatch.setattr(R.subprocess, "Popen", lambda cmd, **k: real([sys.executable, "-c", "pass"], **k))
    a = mgr.submit(REQ)
    mgr._tick()
    mgr.procs[a["run_id"]].wait(timeout=20)
    mgr._tick()
    s = _status(mgr, a["run_id"])
    assert s["state"] == "failed" and "완료 기록" in s["error"]


def test_restart_requeues_dead_running_job(mgr, tmp_path):
    a = mgr.submit(REQ)
    d = mgr.root / "guro" / "runs" / a["run_id"]
    mgr._set(d, state="generating", stage="generate", pid=999999)            # 죽은 pid
    m2 = R.RunManager(mgr.paths, start_worker=False)
    m2._recover()
    s = m2.get_any(a["run_id"])
    assert s["state"] == "queued" and "재시작" in s["stage_label"]
    assert any("서버 재시작" in x for x in s["log_tail"])


def test_cancel_queued_and_running(mgr, monkeypatch):
    a = mgr.submit(REQ)
    assert mgr.cancel(a["run_id"])["state"] == "cancelled"
    real = R.subprocess.Popen
    monkeypatch.setattr(R.subprocess, "Popen",
                        lambda cmd, **k: real([sys.executable, "-c", "import time; time.sleep(30)", "d_runjob", cmd[-1]], **k))
    b = mgr.submit({**REQ, "eval_days": ["20261029"]})
    mgr._tick()
    t0 = time.time()
    assert mgr.cancel(b["run_id"])["state"] == "cancelled" and time.time() - t0 < 10
    assert mgr.procs[b["run_id"]].wait(timeout=10) is not None


def test_tail_drops_private_lines(tmp_path):
    p = tmp_path / "run.log"
    p.write_text("12:00 [생성] 끝\n12:01 TRCR_ID 1234\n12:02 card_id=9\n12:03 " + "a" * 40 + "\n12:04 [지표] 끝\n", encoding="utf-8")
    assert R.tail(p) == ["12:00 [생성] 끝", "12:04 [지표] 끝"]


def test_precomputed_requires_matching_conditions(mgr, tmp_path):
    pre = tmp_path / "pre" / "guro"
    pre.mkdir(parents=True)
    (pre / "pareto.json").write_text(json.dumps({"fleet": 11, "strike": 1.0, "eval_days": ["20261027", "20261028", "20261029",
                                                                                          "20261030"], "plans": []}))
    object.__setattr__(mgr.paths, "precomputed_root", tmp_path / "pre")
    full = {"gu": "guro", "fleet": 11, "setting": "full"}
    a = mgr.submit(full)
    assert a["precomputed"] and a["state"] == "done" and a["has_pareto"]
    assert mgr.submit(full)["run_id"] == a["run_id"]
    with pytest.raises(DataError):
        mgr.submit({**full, "fleet": 12})
