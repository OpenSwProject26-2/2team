import json

from busstrike.api.d_pareto import ParetoStore


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _region(root):
    g = root / "guro"
    _write(g / "pareto.json", {"gu": "guro", "fleet": 11, "eval_days": ["20261028"],
                               "plans": [{"id": "a__floor_none", "plan_sha256": "s1", "served_rate": 0.4, "equity_far": 0.5, "hull": True,
                                          "export_path": "export/a__floor_none.json"},
                                         {"id": "b__floor_none", "plan_sha256": "s2", "served_rate": 0.3, "equity_far": 0.6, "hull": True,
                                          "export_path": None}],
                               "muni": {"id": "muni", "served_rate": 0.15, "equity_far": 0.36}})
    _write(g / "export" / "a__floor_none.json", {"plan_id": "a__floor_none", "routes": []})
    _write(g / "export" / "muni.json", {"plan_id": "muni", "routes": []})
    _write(g / "demo" / "representative.json", {"representative": [{"role": "탑승 우선", "plan_sha256": "s1"}]})
    _write(root / "run_config.json", {"secret": 1})
    return g


def test_pareto_adds_rep_and_muni_export(tmp_path):
    _region(tmp_path)
    doc = ParetoStore(tmp_path).pareto("guro")
    assert doc["status"] == "available"
    assert doc["plans"][0]["rep"] == "탑승 우선" and "rep" not in doc["plans"][1]
    assert doc["muni"]["export_path"] == "export/muni.json"
    assert ParetoStore(tmp_path).regions() == ["guro"]


def test_export_only_listed_plans_inside_region(tmp_path):
    _region(tmp_path)
    store = ParetoStore(tmp_path)
    assert store.export("guro", "a__floor_none")["plan_id"] == "a__floor_none"
    assert store.export("guro", "muni")["plan_id"] == "muni"
    assert store.export("guro", "b__floor_none")["status"] == "unavailable"     # export_path 없음
    assert store.export("guro", "../run_config")["status"] == "unavailable"
    assert store.pareto("../guro")["status"] == "unavailable"
    assert ParetoStore(None).pareto("guro")["status"] == "unavailable"


def _run_doc(plans):
    return {"gu": "guro", "fleet": 11, "eval_days": ["20261028"], "demand_total": 100.0, "plans": plans,
            "muni": {"id": "muni", "served_rate": 0.15, "equity_far": 0.36}}


def _p(pid, served, equity, sha):
    return {"id": pid, "algo_name": pid, "floors": ["none"], "plan_sha256": sha * 64, "served_rate": served,
            "equity_far": equity, "refused_unserved": 1.0}


def _roles_run(tmp_path, plans):
    # 실행 폴더(runs/<id>)와 같은 규칙: demo/representative.json이 없는 곡선
    gu = tmp_path / "guro"
    gu.mkdir(parents=True)
    (gu / "pareto.json").write_text(json.dumps(_run_doc(plans)), encoding="utf-8")
    doc = ParetoStore(tmp_path).pareto("guro")
    return doc, {p["id"]: p.get("rep") for p in doc["plans"]}


PLANS = [_p("feeder_lg0_fill0__floor_none", 0.40, 0.45, "a"),
         _p("feeder_lg1_fill1__floor_0p6", 0.36, 0.58, "b"),
         _p("oneway_loop_lg0_fill0__floor_none", 0.30, 0.66, "c"),
         _p("sector_lg1_fill1__floor_0p75", 0.25, 0.75, "d"),
         _p("feeder_lg0_fill0__floor_0p6", 0.30, 0.50, "e"),          # 지배됨
         {"id": "bad__floor_none", "plan_sha256": "f" * 64, "served_rate": None}]


def test_run_without_representative_gets_roles_by_factorial_rule(tmp_path):
    # 새 실행(작업자 A 실행기)에는 demo/representative.json이 없다. 요인 실험 analyze.py reps 규칙으로
    # 경계 위에서 탑승 우선(충족률 최대)·형평 우선(v* 이상 중 충족률 최대)·균형(무릎점)을 붙인다.
    # v*(편도 후 순회 (0,0) 지자체안 값 하한 점의 형평성)가 없는 빠른 설정은 형평성 최대 점이 형평 우선.
    doc, rep = _roles_run(tmp_path, PLANS)
    assert rep["feeder_lg0_fill0__floor_none"] == "탑승 우선"
    assert rep["sector_lg1_fill1__floor_0p75"] == "형평 우선"
    assert rep["feeder_lg1_fill1__floor_0p6"] == "균형"
    assert rep["oneway_loop_lg0_fill0__floor_none"] is None and rep["feeder_lg0_fill0__floor_0p6"] is None
    assert doc["rep_source"].startswith("계산")


def test_roles_use_vstar_gate_when_present(tmp_path):
    plans = PLANS + [_p("oneway_loop_lg0_fill0__floor_muni", 0.27, 0.70, "g")]   # v* = 0.70
    _, rep = _roles_run(tmp_path, plans)
    assert rep["oneway_loop_lg0_fill0__floor_muni"] == "형평 우선"            # 0.70 이상 중 충족률 최대
