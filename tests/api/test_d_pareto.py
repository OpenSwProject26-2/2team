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
