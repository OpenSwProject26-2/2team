"""web/lab/pareto_panel.js·plan_layer.js의 순수 함수(선택 규칙, 경계 계단선, 눈금, 노선 색)를 작은 합성 예제로 확인한다.

node가 없으면 건너뛴다. 브라우저·지도 없이 ES module만 불러 쓴다.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "web" / "lab"
NODE = shutil.which("node")

PLANS = [
    {"id": "a", "base": "sector", "served_rate": 0.40, "equity_far": 0.40, "hull": True},
    {"id": "b", "base": "feeder", "served_rate": 0.35, "equity_far": 0.50, "hull": True},
    {"id": "c", "base": "oneway_loop", "served_rate": 0.30, "equity_far": 0.60, "hull": True},
    {"id": "d", "base": "feeder", "served_rate": 0.30, "equity_far": 0.45, "hull": False},   # b에 지배당함
    {"id": "e", "base": "sector", "served_rate": 0.30, "equity_far": 0.60, "hull": True},    # c와 같은 값 → id 순
    {"id": "x", "base": "sector", "served_rate": None, "equity_far": None, "hull": False},   # 지표 없음
]


def run(js: str):
    script = f"""
import * as P from {json.dumps((LAB / 'pareto_panel.js').as_uri())};
import * as L from {json.dumps((LAB / 'plan_layer.js').as_uri())};
const plans = {json.dumps(PLANS)};
const out = (() => {{ {js} }})();
console.log(JSON.stringify(out));
"""
    r = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


pytestmark = pytest.mark.skipif(NODE is None, reason="node 없음")


def test_pick_by_floor_takes_max_served_then_equity_then_id():
    got = run("return [0, 0.40, 0.41, 0.50, 0.55, 0.60, 0.61].map((f) => P.pickByFloor(plans, f)?.id ?? null);")
    # 0.55·0.60 → c와 e가 같은 값: 형평성 같음 → id 순 c
    assert got == ["a", "a", "b", "b", "c", "c", None]


def test_pick_by_floor_prefers_higher_equity_on_served_tie():
    got = run("return P.pickByFloor([{id:'p',served_rate:0.3,equity_far:0.5},{id:'q',served_rate:0.3,equity_far:0.55}], 0.5).id;")
    assert got == "q"


def test_hull_staircase_goes_down_then_right():
    got = run("return P.hullSteps(P.hullPoints(plans));")
    assert got == [[0.40, 0.40], [0.40, 0.35], [0.50, 0.35], [0.50, 0.30], [0.60, 0.30], [0.60, 0.30], [0.60, 0.30]]


def test_hull_points_skip_dominated_and_missing():
    assert run("return P.hullPoints(plans).map((p) => p.id);") == ["a", "b", "c", "e"]


def test_axis_has_three_or_four_ticks_and_padding():
    got = run("return [P.axis([0.364, 0.75]), P.axis([0.1527, 0.38]), P.axis([0.3, 0.3])];")
    for ax in got:
        assert 3 <= len(ax["ticks"]) <= 4, ax
        assert all(ax["lo"] - 1e-9 <= t <= ax["hi"] + 1e-9 for t in ax["ticks"])
    assert got[0]["lo"] < 0.364 and got[0]["hi"] > 0.75


def test_days_label_and_gu_name():
    assert run("return [P.daysLabel(['20261030','20261027','20261028','20261029']), P.daysLabel(['20261028']), P.guName('guro')];") == \
        ["10/27~30 (4일)", "10/28", "구로구"]


def test_route_color_fixed_by_route_number_and_features_use_lon_lat():
    got = run("""
const ex = {routes: [{route_id: 'r02', shape: [[37.5, 126.9], [37.501, 126.902]], stops: [{seq: 1, stop_id: 's', name: 'S', lat: 37.5, lon: 126.9, kind: 'station'}]}]};
const f = L.planFeatures(ex);
return [L.routeColor({route_id: 'r02'}, 0) === L.routeColor({route_id: 'r02'}, 5), L.routeColor({route_id: 'r01'}, 0) !== L.routeColor({route_id: 'r02'}, 0),
        f.lines.features[0].geometry.coordinates[0], f.stops.features[0].properties.kind, L.planBounds(ex)];
""")
    assert got[0] is True and got[1] is True
    assert got[2] == [126.9, 37.5]
    assert got[3] == "station"
    assert got[4] == [[126.9, 37.5], [126.902, 37.501]]


def test_lab_server_serves_only_listed_files(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("lab_serve", LAB / "serve.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    (tmp_path / "export").mkdir()
    assert mod.resolve("/run/pareto.json", tmp_path) == tmp_path / "pareto.json"
    assert mod.resolve("/run/export/a__floor_none.json", tmp_path) == tmp_path / "export" / "a__floor_none.json"
    assert mod.resolve("/run/run_config.json", tmp_path) is None
    assert mod.resolve("/run/export/../run_config.json", tmp_path) is None
    assert mod.resolve("/lab/serve.py", tmp_path) is None
