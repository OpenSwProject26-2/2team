"""확정 → 시민 경로 반영 연결(작업자 C): 시민 서버 apply 요청과 반영 상태 판정.

안끼리 plan_id@revision이 같을 수 있어(plan-eqmc07-f11@1) "반영 완료"는 시민 쪽 활성 그래프의
confirm_id와 plan_sha256이 이 확정과 같을 때만이다. 가짜 시민 서버(HTTP)로 돈다."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from busstrike.api.d_confirm import ConfirmStore
from busstrike.api.d_dashboard import Store, make_handler
from busstrike.api.d_simulation import SelectionStore
from tests.api.test_d_confirm import _run
from tests.api.test_d_runs_http import _get, _serve


class FakeCitizen:
    """시민 서버 흉내: POST /api/citizen-routing/apply, GET /api/citizen-routing/apply/status."""

    def __init__(self):
        self.posts = []
        self.status = {"state": "idle", "steps": [], "active": None}
        self.post_reply = (202, {"state": "started", "pid": 1})
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, payload):
                body = json.dumps(payload, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                fake.posts.append((self.path, json.loads(self.rfile.read(n) or b"{}")))
                self._send(*fake.post_reply)

            def do_GET(self):
                u = urlsplit(self.path)
                if u.path != "/api/citizen-routing/apply/status":
                    return self._send(404, {})
                asked = parse_qs(u.query).get("confirm_id", [""])[0]
                st = dict(fake.status)
                if asked:
                    st["applied"] = bool(st.get("active") and st["active"].get("confirm_id") == asked)
                self._send(200, st)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def env(tmp_path, monkeypatch):
    citizen = FakeCitizen()
    paths, ps = _run(tmp_path)
    object.__setattr__(paths, "citizen_api", citizen.url)
    object.__setattr__(paths, "citizen_ui", citizen.url + "/")
    cs = ConfirmStore(paths, ps, None, SelectionStore(tmp_path / "sel.json"))
    monkeypatch.setattr(cs, "_shuttle_check", lambda d: (True, "가짜 통과", {}))
    yield cs, citizen
    citizen.close()


def _confirm(cs):
    r = cs.confirm("guro", None, "feeder_lg0_fill0__floor_none")
    doc = json.loads((cs.root / "guro" / r["confirm_id"] / "confirm.json").read_text(encoding="utf-8"))
    return r, doc


def test_confirm_posts_apply_with_confirm_id(env):
    cs, citizen = env
    r, _ = _confirm(cs)
    assert citizen.posts == [("/api/citizen-routing/apply", {"gu": "guro", "confirm_id": r["confirm_id"]})]
    assert r["citizen"]["state"] == "requested"


def test_status_applied_only_when_active_confirm_and_plan_match(env):
    cs, citizen = env
    r, doc = _confirm(cs)
    cid = r["confirm_id"]
    citizen.status = {"state": "done", "confirm_id": cid, "steps": [],
                      "active": {"confirm_id": cid, "plan_sha256": doc["plan_sha256"], "graph_version": "v1",
                                 "build_id": "g-v1-x", "plan_key": "plan-eqmc07-f2@1#" + doc["plan_sha256"][:8]}}
    got = cs.citizen_status("guro", cid)
    assert got["state"] == "applied" and got["graph_version"] == "v1" and "반영 완료" in got["label"]


def test_same_plan_id_but_other_confirm_active_is_not_applied(env):
    cs, citizen = env
    r, doc = _confirm(cs)
    # 시민 쪽 활성 안이 plan_id@revision은 같지만 다른 확정(다른 안)이면 반영 완료가 아니다
    citizen.status = {"state": "done", "confirm_id": "20261127T000000-deadbeef", "steps": [],
                      "active": {"confirm_id": "20261127T000000-deadbeef", "plan_id": doc["plan_id"],
                                 "revision": doc["revision"], "plan_sha256": "f" * 64}}
    got = cs.citizen_status("guro", r["confirm_id"])
    assert got["state"] == "other"


def test_status_applying_and_failed_follow_this_confirm(env):
    cs, citizen = env
    r, _ = _confirm(cs)
    cid = r["confirm_id"]
    citizen.status = {"state": "running", "step": "build", "confirm_id": cid, "steps": [], "active": None}
    got = cs.citizen_status("guro", cid)
    assert got["state"] == "applying" and got["step"] == "build"
    citizen.status = {"state": "failed", "step": "build", "confirm_id": cid, "steps": [], "active": None,
                      "error": {"code": "X", "message": "빌드 실패"}}
    got = cs.citizen_status("guro", cid)
    assert got["state"] == "failed" and "빌드 실패" in got["label"]


def test_failure_before_bundle_is_read_is_still_failed(env):
    # 시민 쪽이 묶음을 읽기 전에 실패하면 confirm_id는 없고 requested_confirm_id만 있다
    cs, citizen = env
    r, _ = _confirm(cs)
    citizen.status = {"state": "failed", "step": "verify", "requested_confirm_id": r["confirm_id"], "steps": [],
                      "active": None, "error": {"code": "CONFIRM_INVALID", "message": "묶음이 잘못됨"}}
    got = cs.citizen_status("guro", r["confirm_id"])
    assert got["state"] == "failed" and "묶음이 잘못됨" in got["label"]


def test_busy_citizen_reply_is_reported(env):
    cs, citizen = env
    citizen.post_reply = (409, {"code": "APPLY_BUSY", "message": "다른 반영 작업이 진행 중입니다."})
    r, _ = _confirm(cs)
    assert r["citizen"]["state"] == "pending" and "다른 반영 작업" in r["citizen"]["label"]


def test_unconfigured_citizen_status(tmp_path):
    paths, ps = _run(tmp_path)
    cs = ConfirmStore(paths, ps, None, None)
    assert cs.citizen_status("guro", "x")["state"] == "unconfigured"


def test_http_route_returns_citizen_status(env):
    cs, citizen = env
    r, doc = _confirm(cs)
    cid = r["confirm_id"]
    citizen.status = {"state": "done", "confirm_id": cid, "steps": [],
                      "active": {"confirm_id": cid, "plan_sha256": doc["plan_sha256"], "graph_version": "v1"}}
    srv, base = _serve(make_handler(Store(), confirms=cs))
    try:
        got = _get(base, f"/api/d/confirm/citizen?region=guro&confirm_id={cid}")
        assert got["state"] == "applied"
    finally:
        srv.shutdown()
