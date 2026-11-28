"""확정(d_confirm): 확정 묶음 다섯 파일·confirm.json 스키마·CURRENT·선택 기록, 사전 검사 실패 시 쓰지 않음, 순환 검사.
가짜 실행 폴더로 돈다. shuttle_export 실제 검사는 로컬 요인 실험 결과가 있을 때만(없으면 건너뜀)."""
import csv
import hashlib
import json
from pathlib import Path

import pytest

from busstrike.api.d_confirm import ConfirmStore, loop_check
from busstrike.api.d_geo import DataError
from busstrike.api.d_pareto import ParetoStore
from busstrike.api.d_simulation import SelectionStore
from busstrike.data import LocalPaths


def _w(p: Path, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def _timetable(close_s=40, oneway=False):
    trips = [{"trip_id": "r01-V1-0-0", "route_id": "r01", "leg_index": 0, "in_service": True, "vehicle_id": "V1", "cycle_index": 0,
              "stop_times": [{"stop_id": "S1", "sched_s": 25200, "stop_seq": 0}, {"stop_id": "S2", "sched_s": 25500, "stop_seq": 1}]},
             {"trip_id": "r01-V1-0-1", "route_id": "r01", "leg_index": 1, "in_service": False, "vehicle_id": "V1", "cycle_index": 0,
              "stop_times": [{"stop_id": "S2", "sched_s": 25500, "stop_seq": 0}, {"stop_id": "S1", "sched_s": 25500 + close_s, "stop_seq": 1}]}]
    if oneway:
        trips.append({**trips[1], "trip_id": "x", "leg_index": 2})
    return {"schema_version": 1, "origin": "generated", "plan_id": "plan-eqmc07-f2", "plan_revision": 1, "routes": [], "trips": trips,
            "duties": []}


def _run(tmp_path, *, close_s=40, oneway=False, report_pass=True):
    W = tmp_path / "work"
    _w(W / "region_all/guro_0610_v4/network/stops.csv",
       "stop_id,stop_name,stop_lat,stop_lon,gu\nS1,가 정류장,37.49,126.88,구로구\nS2,나 정류장,37.50,126.89,구로구\nS9,먼 곳,37.6,127.0,강남구\n")
    pr = tmp_path / "pareto"
    out = pr / "guro"
    plan = {"network_snapshot_id": "net-1", "plan_id": "plan-eqmc07-f2", "revision": 1,
            "routes": [{"route_id": "r01", "stops": ["S1", "S2"], "vehicles": 2}]}
    pf = _w(out / "gen/feeder_lg0_fill0/floor_none/plans/eqmc07-f2.json", plan)
    sha = hashlib.sha256(pf.read_bytes()).hexdigest()
    tag = "p" + sha[:12]
    _w(out / "plans_index.json", [{"algo": "feeder_lg0_fill0", "floor": "none", "plan": str(pf), "sha": sha}])
    _w(out / "run_config.json", {"created": {"code": "abc123"}})
    _w(out / "pareto.json", {"gu": "guro", "fleet": 2, "strike": 1.0, "eval_days": ["20261028"], "demand_total": 1000.0,
                             "plans": [{"id": "feeder_lg0_fill0__floor_none", "algo_name": "피더 순회", "base": "feeder", "floors": ["none"],
                                        "plan_sha256": sha, "eval_tag": tag, "served_rate": 0.4, "equity_far": 0.5, "isolation": 0.5,
                                        "time_ratio_rode": 1.2, "refused_unserved": 50.0, "m07": "1/1", "routes": 1},
                                       {"id": "bad__floor_none", "plan_sha256": "0" * 64, "served_rate": None, "m07": "0/1"}],
                             "muni": {"id": "muni", "served_rate": 0.15, "equity_far": 0.36}})
    scen = {"scenario_id": "sc-1", "network_snapshot_id": "net-1", "service_date": "2026-10-28"}
    _w(out / "bundles/road/strike_20261028/scenario_f2.json", scen)
    rd = out / f"eval/runs/{tag}-f2/same_line-w400/exp/{tag}/strike_20261028/f2"
    _w(rd / "scenario/scenario_manifest.json", {"scenario_id": "sc-1"})
    _w(rd / "run/timetable.json", _timetable(close_s, oneway))
    _w(rd / "verifier_report.json", {"pass": report_pass, "failed_checks": [] if report_pass else ["X"], "fully_verified": False})
    paths = LocalPaths(work_root=W, pareto_root=pr, confirm_root=tmp_path / "confirm")
    ps = ParetoStore(pr)
    return paths, ps


@pytest.fixture
def store(tmp_path, monkeypatch):
    paths, ps = _run(tmp_path)
    sel = SelectionStore(tmp_path / "sel.json")
    cs = ConfirmStore(paths, ps, None, sel)
    monkeypatch.setattr(cs, "_shuttle_check", lambda d: (True, "가짜 통과", {"stats": {}}))
    return cs, sel


def test_confirm_writes_bundle_schema_current_and_selection(store):
    cs, sel = store
    pv = cs.preview("guro", None, "feeder_lg0_fill0__floor_none")
    assert pv["ok"] and pv["metrics"]["refused_rate"] == 0.05 and pv["service"] == "07:00~07:05"
    r = cs.confirm("guro", None, "feeder_lg0_fill0__floor_none")
    d = Path(r["path"])
    assert sorted(x.name for x in d.iterdir()) == ["confirm.json", "plan.json", "scenario.json", "stops.csv", "timetable.json",
                                                   "verifier_report.json"]
    doc = json.loads((d / "confirm.json").read_text(encoding="utf-8"))
    assert doc["schema_version"] == 1 and doc["confirmed_at"].endswith("+09:00") and doc["code_commit"] == "abc123"
    assert doc["metrics"] == {"served_rate": 0.4, "equity_far": 0.5, "isolation": 0.5, "refused_rate": 0.05, "time_ratio_rode": 1.2}
    for k, f in doc["files"].items():
        assert hashlib.sha256((d / f["path"]).read_bytes()).hexdigest() == f["sha256"]
    plan = json.loads((d / "plan.json").read_text(encoding="utf-8"))
    assert plan["scenario_id"] == "sc-1" and plan["status"] == "candidate"
    assert list(csv.DictReader((d / "stops.csv").open(encoding="utf-8")))[0] == {"stop_id": "S1", "stop_lat": "37.49",
                                                                                  "stop_lon": "126.88", "stop_name": "가 정류장"}
    assert (d.parent / "CURRENT").read_text().strip() == r["confirm_id"]
    s = json.loads(sel.path.read_text(encoding="utf-8"))
    assert s["selected"] is None and s["pareto_selected"]["confirm_id"] == r["confirm_id"]
    assert s["history"][-1]["kind"] == "pareto_confirm"
    assert r["citizen"]["state"] == "pending"                                # 시민 서버 주소 없음 → 반영 대기
    assert cs.current("guro")["confirm_id"] == r["confirm_id"]
    text = "".join(p.read_text(encoding="utf-8") for p in d.iterdir())
    assert "TRCR" not in text and "passenger_id" not in text


def test_no_write_when_checks_fail(tmp_path, monkeypatch):
    for kw, why in [({"close_s": 900}, "편도"), ({"oneway": True}, "순환"), ({"report_pass": False}, "M07")]:
        t = tmp_path / why
        paths, ps = _run(t, **kw)
        cs = ConfirmStore(paths, ps, None, SelectionStore(t / "sel.json"))
        monkeypatch.setattr(cs, "_shuttle_check", lambda d: (True, "", {}))
        with pytest.raises(DataError):
            cs.confirm("guro", None, "feeder_lg0_fill0__floor_none")
        assert not (t / "confirm" / "guro" / "CURRENT").exists()
        assert not [x for x in (t / "confirm" / "guro").glob("*") if not x.name.startswith(".")] if (t / "confirm" / "guro").exists() else True


def test_shuttle_check_failure_blocks(store, monkeypatch):
    cs, _ = store
    monkeypatch.setattr(cs, "_shuttle_check", lambda d: (False, "shuttle_export 검사 실패: 좌표", None))
    with pytest.raises(DataError, match="shuttle_export"):
        cs.confirm("guro", None, "feeder_lg0_fill0__floor_none")
    assert not (cs.root / "guro" / "CURRENT").exists()
    assert not list((cs.root / "guro").glob("*.partial"))


def test_cannot_confirm_unverified_or_unknown(store):
    cs, _ = store
    with pytest.raises(DataError):
        cs.confirm("guro", None, "bad__floor_none")
    with pytest.raises(DataError):
        cs.confirm("guro", None, "muni")


def test_loop_check():
    assert loop_check(_timetable(40))[0] and not loop_check(_timetable(301))[0] and not loop_check(_timetable(40, True))[0]


def test_apply_called_when_configured(tmp_path, monkeypatch):
    paths, ps = _run(tmp_path)
    object.__setattr__(paths, "citizen_api", "http://127.0.0.1:1")           # 닫힌 포트 → 반영 대기
    cs = ConfirmStore(paths, ps, None, SelectionStore(tmp_path / "sel.json"))
    monkeypatch.setattr(cs, "_shuttle_check", lambda d: (True, "", {}))
    r = cs.confirm("guro", None, "feeder_lg0_fill0__floor_none")
    assert r["citizen"]["state"] == "pending" and "응답 없음" in r["citizen"]["label"]


FACT = Path("/mnt/c/Users/ddonggojin/orca/projects/guro_exp_work/routegen_integrated/factorial_1126")


@pytest.mark.skipif(not (FACT / "guro" / "pareto.json").is_file(), reason="로컬 요인 실험 결과(factorial_1126/guro) 없음 — 이 PC 전용 통합 확인")
def test_real_shuttle_export_precheck_on_factorial(tmp_path):
    paths = LocalPaths(work_root=FACT.parents[1], confirm_root=tmp_path / "confirm")
    cs = ConfirmStore(paths, ParetoStore(FACT))
    pv = cs.preview("guro", None, "feeder_lg0_fill0__floor_none")
    assert pv["ok"], pv["checks"]
    assert any("shuttle_export" in c["name"] and c["ok"] for c in pv["checks"])
    assert not list((tmp_path / "confirm").rglob("*.txt"))                      # GTFS는 임시 폴더에서 지움
