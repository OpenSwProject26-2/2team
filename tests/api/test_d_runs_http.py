"""실행 버튼·확정 HTTP 경계: /api/d/runs*, /api/d/confirm*, 곡선 run 인자, 설정 없을 때 '연결 안 됨', 응답에 카드 ID·승객 단위 정보 없음."""
import json
import re
import sys
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

import busstrike.data.assemble  # noqa: F401
from busstrike.api.d_confirm import ConfirmStore
from busstrike.api.d_dashboard import Store, make_handler
from busstrike.api.d_pareto import ParetoStore
from busstrike.api.d_runs import RunManager
from busstrike.api.d_simulation import SelectionStore
from tests.api.test_d_confirm import _run

PRIVATE = re.compile(r"(?i)trcr|card_?id|passenger_id")


def _serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


def _get(base, path):
    with urlopen(base + path, timeout=5) as r:
        body = r.read().decode("utf-8")
    assert not PRIVATE.search(body)
    return json.loads(body)


def _post(base, path, data):
    req = Request(base + path, data=json.dumps(data).encode(), headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=10) as r:
            body = r.read().decode("utf-8")
            assert not PRIVATE.search(body)
            return r.status, json.loads(body)
    except HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def test_unconfigured_is_unavailable():
    srv, base = _serve(make_handler(Store()))
    try:
        assert _get(base, "/api/d/runs/config")["status"] == "unavailable"
        assert _get(base, "/api/d/runs")["status"] == "unavailable"
        code, body = _post(base, "/api/d/runs", {"gu": "guro"})
        assert code == 503 and "설정" in body["message"]
        code, body = _post(base, "/api/d/confirm", {"region": "guro", "id": "x"})
        assert code == 503
    finally:
        srv.shutdown()


@pytest.fixture
def served(tmp_path, monkeypatch):
    paths, _ = _run(tmp_path)
    mod = sys.modules["busstrike.data.assemble"]
    monkeypatch.setattr(mod, "muni_fleet", lambda gu, p=None: 2)
    monkeypatch.setattr(mod, "assemble", lambda gu, s, dd, ed, paths=None, **k: {
        "input_hash": "h", "manifest_path": "m", "manifest": {"strike": s, "eval_days": ed}, "cached": True, "seconds": 0.0})
    runs = RunManager(paths, start_worker=False)
    pareto = ParetoStore(paths.pareto_root, runs=runs)
    sel = SelectionStore(tmp_path / "sel.json")
    confirms = ConfirmStore(paths, pareto, runs, sel)
    monkeypatch.setattr(confirms, "_shuttle_check", lambda d: (True, "가짜 통과", {}))
    srv, base = _serve(make_handler(Store(), pareto=pareto, runs=runs, confirms=confirms, selections=sel, local_paths=paths))
    yield base, runs, paths
    srv.shutdown()


def test_runs_and_confirm_over_http(served):
    base, runs, paths = served
    cfg = _get(base, "/api/d/runs/config")
    assert cfg["status"] == "available" and [g["id"] for g in cfg["gus"] if g["enabled"]] == ["guro"]
    assert "20261103" in cfg["test_week"] and "20261103" not in cfg["eval_choices"]
    code, r = _post(base, "/api/d/runs", {"gu": "guro", "fleet": 2, "eval_days": ["20261028"], "setting": "quick"})
    assert code == 200 and r["state"] == "queued"
    code, bad = _post(base, "/api/d/runs", {"gu": "guro", "eval_days": ["20261104"]})
    assert code == 400 and "테스트 주간" in bad["message"]
    code, off = _post(base, "/api/d/runs", {"gu": "gangnam"})
    assert code == 400 and "준비 중" in off["message"]
    got = _get(base, f"/api/d/runs/{r['run_id']}")
    assert got["run_id"] == r["run_id"] and [s["key"] for s in got["stages"]][0] == "assemble"
    assert _get(base, "/api/d/runs?gu=guro")["runs"][0]["run_id"] == r["run_id"]
    code, c = _post(base, f"/api/d/runs/{r['run_id']}/cancel", {})
    assert code == 200 and c["state"] == "cancelled"
    # 기본 곡선(run 없음)으로 확정
    doc = _get(base, "/api/d/simulation/pareto?region=guro")
    assert doc["status"] == "available" and doc["plans"][0]["refused_rate"] == 0.05 and doc["excluded_count"] == 0
    assert _get(base, "/api/d/simulation/pareto?region=guro&run=20990101-000000-abcdef")["status"] == "unavailable"
    pv = _get(base, "/api/d/confirm/preview?region=guro&id=feeder_lg0_fill0__floor_none")
    assert pv["ok"] and pv["vehicles"] == 2
    code, done = _post(base, "/api/d/confirm", {"region": "guro", "id": "feeder_lg0_fill0__floor_none"})
    assert code == 200 and done["status"] == "confirmed"
    assert _get(base, "/api/d/confirm/current?region=guro")["confirm_id"] == done["confirm_id"]
    code, no = _post(base, "/api/d/confirm", {"region": "guro", "id": "bad__floor_none"})
    assert code == 409
