"""busstrike.data.assemble: 입력 검사·manifest·입력 해시 캐시·테스트 주간 거절(가짜 폴더로, 실제 자료 없이)."""
import json

import pytest

from busstrike.data import AssembleError, LocalPaths, assemble, load_paths
from busstrike.data.assemble import EXP_WEEK, muni_fleet, normalize_strike


def _tree(tmp_path, drop=()):
    W = tmp_path / "work"
    root = W / "region_all" / "guro_0610_v4"
    files = ["network/stops.csv", "network/stations.csv", "network/station_stops.csv", "network/travel_times.json",
             "network/absorbing_routes.csv", "demand/demand_flows.csv.gz"]
    for d in EXP_WEEK:
        files += [f"bundles/affected_{d}/demand_bins.csv", f"bundles/affected_{d}/travel_times.json",
                  f"bundles/affected_{d}/scenario_f11.json"]
    for f in files:
        p = root / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f, encoding="utf-8")
    sweep = W / "sweep" / "guro"
    muni = W / "muni" / "guro"
    for p in (sweep / "net/road.json", sweep / "net/turns.json", sweep / "hub_sweep.json", W / "osm/guro_roads.json",
              W / "routegen_v2/facilities/facilities_seoul.csv", muni / "road_muni.json"):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(p.name, encoding="utf-8")
    (muni / "plan.json").write_text(json.dumps({"routes": [{"vehicles": 3}, {"vehicles": 8}]}), encoding="utf-8")
    for rel in drop:
        (W / rel).unlink()
    return LocalPaths(work_root=W, pareto_root=tmp_path / "pareto", sweep=str(W / "sweep/{gu}"),
                      muni=str(W / "muni/{gu}"))


def test_manifest_then_cache(tmp_path):
    paths = _tree(tmp_path)
    a = assemble("guro", {"mode": "full"}, list(EXP_WEEK), ["2026-10-28"], paths=paths, code_commit="c1")
    assert not a["cached"] and a["manifest"]["eval_days"] == ["20261028"]
    files = a["manifest"]["files"]
    assert all(f["present"] and len(f["sha256"]) == 64 and f["size"] > 0 for f in files)
    assert not any("_test" in f["path"] for f in files)
    b = assemble("guro", {"mode": "full"}, list(EXP_WEEK), ["20261028"], paths=paths, code_commit="c1")
    assert b["cached"] and b["input_hash"] == a["input_hash"]
    c = assemble("guro", {"mode": "full"}, list(EXP_WEEK), ["20261028"], paths=paths, code_commit="c2")
    assert not c["cached"] and c["input_hash"] != a["input_hash"]          # 코드 커밋이 바뀌면 새 해시
    (paths.work_root / "osm/guro_roads.json").write_text("changed", encoding="utf-8")
    d = assemble("guro", {"mode": "full"}, list(EXP_WEEK), ["20261028"], paths=paths, code_commit="c1")
    assert d["input_hash"] != a["input_hash"]                               # 파일이 바뀌면 새 해시


def test_missing_reports_what_and_how(tmp_path):
    paths = _tree(tmp_path, drop=("sweep/guro/net/turns.json", "region_all/guro_0610_v4/demand/demand_flows.csv.gz"))
    with pytest.raises(AssembleError) as e:
        assemble("guro", None, list(EXP_WEEK), ["20261028"], paths=paths, code_commit="c")
    items = {m["item"] for m in e.value.missing}
    assert any("turns.json" in x for x in items) and any("demand_flows" in x for x in items)
    assert all(m["how"] for m in e.value.missing)


@pytest.mark.parametrize("demand,evald", [(list(EXP_WEEK), ["20261103"]), (["20261102"], ["20261028"]),
                                          (list(EXP_WEEK), ["20261025"]), (list(EXP_WEEK)[:3], ["20261028"])])
def test_bad_days_rejected(tmp_path, demand, evald):
    paths = _tree(tmp_path)
    with pytest.raises(AssembleError):
        assemble("guro", None, demand, evald, paths=paths, code_commit="c")


def test_strike_normalize():
    assert normalize_strike(None) == {"mode": "full", "ratio": 1.0, "seed": 20261121}
    assert normalize_strike({"mode": "ratio", "ratio": 0.5})["mode"] == "ratio"
    with pytest.raises(AssembleError):
        normalize_strike({"mode": "ratio", "ratio": 0.5, "seed": 7})       # 파이프라인 seed 고정
    with pytest.raises(AssembleError):
        normalize_strike({"mode": "ratio", "ratio": 0})


def test_muni_fleet_and_missing_config(tmp_path):
    paths = _tree(tmp_path)
    assert muni_fleet("guro", paths) == 11
    assert load_paths(tmp_path / "없음.toml").work_root is None
    with pytest.raises(AssembleError):
        assemble("guro", None, list(EXP_WEEK), ["20261028"], paths=LocalPaths(), code_commit="c")


def test_load_paths_reads_toml(tmp_path):
    f = tmp_path / "p.toml"
    f.write_text('[paths]\nwork_root = "/w"\nsweep = "/s/{gu}"\n[citizen]\napi = "http://x:1/"\n[run]\npar = 8\n',
                 encoding="utf-8")
    p = load_paths(f)
    assert str(p.work_root) == "/w" and str(p.sweep_dir("guro")) == "/s/guro"
    assert p.citizen_api == "http://x:1" and p.par == 3                      # 병렬 3 상한
