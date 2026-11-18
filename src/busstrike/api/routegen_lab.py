"""routegen 노선 실험실 로컬 API(개발용). D 운영자 대시보드 서버에 붙여 쓴다.

정식 M08 계약이 아니다. 외부 주소에 바인딩하지 않는 `d_dashboard` 서버 안에서만 동작한다.

- GET  /lab, /lab.js                 화면
- GET  /api/lab/meta                 구·대수·변형·도로망 통계
- GET  /api/lab/layer/<name>         stops · stations · demand · bus_roads · strike_routes · absorbing_routes · results
- POST /api/lab/generate             {net, variant, fleet, params} → 후보 생성 + 배분(근사)
- POST /api/lab/simulate             {net, variant, fleet, date, routes:[{stops, vehicles}]} → 변환기 → M05 → M07 하루

데이터는 `experiments/routegen_lab/run_networks.py`·`build_lab_data.py`가 저장소 밖 폴더(`--lab-dir`)에 만든 것을 읽는다.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from threading import Lock

import shutil

from busstrike.planning.routegen import allocate as A
from busstrike.planning.routegen.absorb import Absorber, AbsorbParams, residual_rows
from busstrike.sim import io as sio
from busstrike.sim.arrivals import build_run_inputs, realize_bins, write_demand_bins
from busstrike.sim.models import DemandFlow
from busstrike.planning.routegen import data as D
from busstrike.planning.routegen import generate as G
from busstrike.sim.traveltime import TravelTimeTable

ROOT = Path(__file__).resolve().parents[3]
WEB = ROOT / "web" / "operator"
LAYERS = ("meta", "stops", "stations", "demand", "bus_roads", "strike_routes", "absorbing_routes", "results", "boundaries")
NETS = ("orig", "road")


class LabError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def vehicle_schedule(run_dir: Path) -> dict:
    """M05 실행 폴더(`run/`) → 차량별 하루 동선.

    회차마다 [첫 정류장 출발, 마지막 정류장(영업 끝) 도착, 공차 복귀 끝, 탑승 인원, 최대 재차 인원].
    탑승·재차는 사건 로그(`events.jsonl`)의 boarded·occupancy에서 센다.
    """
    run_dir = Path(run_dir)
    tt = json.loads((run_dir / "timetable.json").read_text(encoding="utf-8"))
    trips = {t["trip_id"]: t for t in tt["trips"]}
    boarded: dict[str, int] = {}
    maxocc: dict[str, int] = {}
    with open(run_dir / "events.jsonl", encoding="utf-8") as f:
        for line in f:
            e = json.loads(line)
            tid = (e.get("stable_id") or "").split(":", 1)[0]
            if tid not in trips:
                continue
            if e["kind"] == "boarded":
                boarded[tid] = boarded.get(tid, 0) + 1
            occ = e.get("occupancy")
            if isinstance(occ, int):
                maxocc[tid] = max(maxocc.get(tid, 0), occ)
    vehicles = []
    for d in tt["duties"]:
        cyc: dict[int, list] = {}
        for tid in d["trip_ids"]:
            t = trips[tid]
            st = t["stop_times"]
            c = cyc.setdefault(t["cycle_index"], [None, None, None, 0, 0])
            if t["in_service"]:
                c[0], c[1] = st[0]["sched_s"], st[-1]["sched_s"]
                c[3], c[4] = boarded.get(tid, 0), maxocc.get(tid, 0)
            else:
                c[2] = st[-1]["sched_s"]
        cycles = [v if v[2] is not None else v[:2] + [v[1]] + v[3:] for _, v in sorted(cyc.items()) if v[0] is not None]
        vehicles.append({"vehicle": d["vehicle_id"], "route": d["route_id"], "cycles": cycles})
    return {"routes": {r["route_id"]: {"period_s": r["period_s"], "vehicles": r["vehicle_ids"]} for r in tt["routes"]},
            "vehicles": vehicles}


VARIANT_FLAGS = {"affected": (False, False), "affected_maeul": (True, False), "affected_gg": (False, True),
                 "affected_maeul_gg": (True, True)}


def absorb_params(raw: dict | None) -> AbsorbParams:
    d = AbsorbParams()
    raw = raw or {}
    try:
        p = AbsorbParams(maeul_cap=int(raw.get("maeul_cap", d.maeul_cap)), gg_cap=int(raw.get("gg_cap", d.gg_cap)),
                         maeul_load_ratio=float(raw.get("maeul_load_ratio", d.maeul_load_ratio)),
                         gg_busy_ratio=float(raw.get("gg_busy_ratio", d.gg_busy_ratio)),
                         maeul_exact_stop=bool(raw.get("maeul_exact_stop", d.maeul_exact_stop)),
                         gg_exact_stop=bool(raw.get("gg_exact_stop", d.gg_exact_stop)))
    except (TypeError, ValueError) as error:
        raise LabError("INPUT_INVALID", f"흡수 가정값이 잘못되었다: {error}") from error
    if not (5 <= p.maeul_cap <= 120 and 5 <= p.gg_cap <= 150 and 0 <= p.maeul_load_ratio <= 2 and 0 <= p.gg_busy_ratio <= 1):
        raise LabError("INPUT_INVALID", "범위: 마을 정원 5~120, 경기 정원 5~150, 마을 기존 탑승 비율 0~2, 경기 혼잡률 0~1")
    return p


def make_bundle(src: Path, dst: Path, rows: list, tt_path: Path, fleets: list[int], tag: str) -> Path:
    """원본 묶음(src)의 시나리오에 `rows`(그 날짜의 셔틀 수요)로 만든 demand_bins와 `tt_path` 망을 붙인 묶음."""
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(tt_path, dst / "travel_times.json")
    for f in fleets:
        shutil.copyfile(src / f"scenario_f{f}.json", dst / f"scenario_f{f}.json")
    scen = sio.scenario_from_dict(sio.read_json(dst / f"scenario_f{fleets[0]}.json"))
    flows = [DemandFlow(r.kind, r.stop_id, r.station_stop_id, r.hour * 3600, f"{float(r.flow):.6f}") for r in rows
             if float(r.flow) > 0]
    seed = json.loads((src / f"run_inputs_f{fleets[0]}.json").read_text(encoding="utf-8"))["seed"]
    write_demand_bins(realize_bins(flows, scen.policy, scen.window, seed, 3600), dst / "demand_bins.csv")
    for f in fleets:
        ri = json.loads((src / f"run_inputs_f{f}.json").read_text(encoding="utf-8"))
        build_run_inputs(ri["input_bundle_id"] + "-" + tag, dst / f"scenario_f{f}.json", dst / "demand_bins.csv",
                         dst / "travel_times.json", ri["seed"], dst / f"run_inputs_f{f}.json")
    return dst


def stop_residual(rows: list, station_stops: set[str], days: int) -> dict[str, float]:
    """행 → 정류장별 하루 평균 셔틀 수요(역 쪽이 아닌 동네 정류장 기준)."""
    out: dict[str, float] = {}
    for r in rows:
        s = r.stop_id
        out[s] = out.get(s, 0.0) + float(r.flow) / days
    # FlowRow.stop_id는 언제나 동네 쪽 정류장이다(역 정류장은 station_stop_id). 그 정류장이 다른 역의 지정 정류장이어도 센다.
    return {k: round(v, 2) for k, v in sorted(out.items()) if v >= 0.05}


def _load_run_matrix():
    path = ROOT / "experiments" / "guro_exp" / "run_matrix.py"
    spec = importlib.util.spec_from_file_location("lab_run_matrix", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Lab:
    def __init__(self, lab_dir: Path, region: Path):
        self.dir = Path(lab_dir).resolve()
        self.layers = self.dir / "layers"
        self.meta = json.loads((self.layers / "meta.json").read_text(encoding="utf-8"))
        self.root = Path(region).resolve() / f"{self.meta['gu_key']}_0610_v4"
        self.lock = Lock()
        self._net: dict[str, D.Network] = {}
        self._rows = None
        self._demand: dict[str, D.Demand] = {}
        self._precise: dict = {}
        self._segpaths = None
        self._stops = None

    # ------------------------------------------------------------ 캐시
    def layer_bytes(self, name: str) -> bytes:
        if name not in LAYERS:
            raise LabError("INPUT_INVALID", f"레이어 이름을 모른다: {name}")
        return (self.layers / f"{name}.json").read_bytes()

    def network(self, net: str) -> D.Network:
        if net not in NETS:
            raise LabError("INPUT_INVALID", "net은 orig 또는 road다.")
        if net not in self._net:
            base = D.load_network(self.root)
            doc = json.loads((self.dir / "net" / f"{net}.json").read_text(encoding="utf-8"))
            tp = self.dir / "net" / "turns.json"
            turns = json.loads(tp.read_text(encoding="utf-8")) if tp.exists() else None
            self._net[net] = D.Network(TravelTimeTable.from_dict(doc), base.stops, base.stations, turns)
        return self._net[net]

    def rows(self):
        if self._rows is None:
            self._rows = D.load_flows(self.root)
        return self._rows

    def precise(self, variant: str, ap: AbsorbParams) -> tuple[list, dict]:
        """정밀 흡수 뒤 셔틀 수요 행과 요약(하루 평균)."""
        key = (variant, ap)
        if key not in self._precise:
            m, g = VARIANT_FLAGS[variant]
            res, summ = Absorber(self.root, ap).absorb(self.rows(), m, g)
            days = len({r.date for r in self.rows()})
            self._precise[key] = (residual_rows(self.rows(), res), {k: round(v / days, 1) for k, v in summ.items()})
        return self._precise[key]

    def demand(self, variant: str, absorb: str = "bundle", ap: AbsorbParams | None = None) -> D.Demand:
        if variant not in D.VARIANTS:
            raise LabError("INPUT_INVALID", f"변형을 모른다: {variant}")
        if absorb == "precise":
            rows, _ = self.precise(variant, ap or AbsorbParams())
            d = D.build_demand(rows, "affected")
            d.dates = tuple(sorted({r.date for r in self.rows()}))
            return d
        if variant not in self._demand:
            self._demand[variant] = D.build_demand(self.rows(), variant)
        return self._demand[variant]

    def coords(self, stops: list[str]) -> list[list[float]]:
        if self._segpaths is None:
            self._segpaths = json.loads((self.layers / "segpaths.json").read_text(encoding="utf-8"))
            self._stops = json.loads((self.layers / "stops.json").read_text(encoding="utf-8"))
        out: list[list[float]] = []
        for a, b in zip(stops, stops[1:]):
            p = self._segpaths.get(f"{a}>{b}") or [self._stops[a][:2], self._stops[b][:2]]
            out.extend(p if not out else p[1:] if out[-1] == p[0] else p)
        return out

    # ------------------------------------------------------------ 생성
    def generate(self, req: dict) -> dict:
        net_name, variant = req.get("net", "orig"), req.get("variant", "affected")
        fleet = int(req.get("fleet", self.meta["fleets"][0]))
        if not 1 <= fleet <= 200:
            raise LabError("INPUT_INVALID", "대수는 1~200이다.")
        prm = req.get("params") or {}
        d = G.Params()
        try:
            l_max = int(prm.get("l_max_min", 30))
            params = G.Params(radius_m=float(prm.get("radius_m", d.radius_m)),
                              max_stops=None if prm.get("max_stops") in (None, "", "none", 0) else int(prm["max_stops"]),
                              max_detour=float(prm.get("max_detour", d.max_detour)),
                              l_max_min=tuple(sorted({20, l_max})) if l_max > 20 else (l_max,),
                              home_gu=self.meta["gu"], distance_basis="network")
            cover = bool(req.get("cover", False))
        except (TypeError, ValueError) as error:
            raise LabError("INPUT_INVALID", f"매개변수가 잘못되었다: {error}") from error
        if not (500 <= params.radius_m <= 6000 and 1.0 <= params.max_detour <= 5.0 and 10 <= l_max <= 60):
            raise LabError("INPUT_INVALID", "범위: R 500~6000m, 우회율 1.0~5.0, 편도 10~60분")
        absorb = req.get("absorb", "bundle")
        if absorb not in ("bundle", "precise"):
            raise LabError("INPUT_INVALID", "absorb는 bundle 또는 precise다.")
        ap = absorb_params(req.get("absorb_params"))
        with self.lock:
            t0 = time.time()
            net = self.network(net_name)
            demand = self.demand(variant, absorb, ap)
            # 정책(정차·회차·창)만 쓰므로 대수와 무관하다. 묶음에 있는 첫 대수 시나리오를 읽는다.
            scen = D.read_scenario(self.root / "bundles" / f"{variant}_{demand.dates[0]}", self.meta["fleets"][0])
            res = G.generate(net, demand, scen, params)
            pool = G.pools(res.candidates, params.jaccard)["ours"][0]
            if not pool:
                return {"routes": [], "approx": 0, "pool_cover": 0, "demand_total": round(demand.total(), 1),
                        "seconds": round(time.time() - t0, 1), "candidates": 0}
            by = {c.route_id: c for c in pool}
            groups = {c.route_id: "·".join(c.hubs) for c in pool}
            weight = {"·".join(c.hubs): sum(res.hubs.get(h, 0.0) for h in c.hubs) for c in pool}
            m = A.Model([(c.route_id, c.cycle_s, c.cover_keys) for c in pool], demand.od,
                        A.AllocParams(cover_groups=cover), groups, weight)
            a = A.allocate(m, fleet)
            det = m.detail(a["n"])
            keys = set()
            for c in pool:
                keys.update(c.cover_keys)
            routes = []
            for i, (k, v) in enumerate(sorted(a["n"].items()), 1):
                c = by[k]
                routes.append({"id": f"r{i:02d}", "stops": list(c.stops), "vehicles": v, "kind": c.kind, "origin": c.origin,
                               "cycle_s": c.cycle_s, "hubs": [net.stations[h].name for h in c.hubs],
                               "approx": round(sum(det["by_route"][k]), 1), "coords": self.coords(list(c.stops))})
            extra = {}
            if absorb == "precise":
                rows, summ = self.precise(variant, ap)
                st_stops = {x for s_ in net.stations.values() for x in (s_.access_stop, s_.egress_stop) if x}
                extra = {"absorbed": summ, "stop_demand": stop_residual(rows, st_stops, len(demand.dates)),
                         "absorb_params": ap.__dict__}
            return {"net": net_name, "variant": variant, "fleet": fleet, "params": {**prm}, "cover": cover,
                    "covered_groups": a["covered_groups"], "absorb": absorb, **extra,
                    "routes": routes, "approx": round(a["value"], 1), "candidates": len(res.candidates),
                    "pool_cover": round(sum(demand.od_total(k) for k in keys), 1), "demand_total": round(demand.total(), 1),
                    "seconds": round(time.time() - t0, 1)}

    # ------------------------------------------------------------ 하루 시뮬레이션
    def simulate(self, req: dict) -> dict:
        net_name, variant = req.get("net", "orig"), req.get("variant", "affected")
        fleet = int(req.get("fleet", self.meta["fleets"][0]))
        date = str(req.get("date", self.meta["dates_exp"][2]))
        routes = req.get("routes") or []
        if net_name not in NETS or variant not in D.VARIANTS or not routes:
            raise LabError("INPUT_INVALID", "net·variant·routes가 필요하다.")
        absorb = req.get("absorb", "bundle")
        bundle = self.dir / net_name / variant / "exp" / "bundles" / f"{variant}_{date}"
        if not (bundle / f"scenario_f{fleet}.json").exists():
            raise LabError("DATA_INCOMPLETE", f"그 날짜·대수 묶음이 없다: {bundle.name} f{fleet}")
        tmp_bundle = None
        if absorb == "precise":
            rows, _ = self.precise(variant, absorb_params(req.get("absorb_params")))
            tmp_bundle = Path(tempfile.mkdtemp(prefix="lab-bundle-"))
            bundle = make_bundle(self.root / "bundles" / f"affected_{date}", tmp_bundle / f"{variant}_{date}",
                                 [r for r in rows if r.date == date], self.dir / "net" / f"{net_name}.json",
                                 self.meta["fleets"], "precise")
        if sum(int(r["vehicles"]) for r in routes) != fleet:
            raise LabError("INPUT_INVALID", "노선 대수 합이 대수와 다르다(변환기 S5).")
        plan = {"plan_id": "plan-lab", "revision": 1, "network_snapshot_id": self.meta["network_snapshot_id"],
                "routes": [{"route_id": f"r{i:02d}", "stops": list(r["stops"]), "vehicles": int(r["vehicles"])}
                           for i, r in enumerate(routes, 1)]}
        rm = _load_run_matrix()
        with tempfile.TemporaryDirectory(prefix="lab-sim-") as tmp:
            p = Path(tmp) / "plan.json"
            p.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
            job = {"label": "lab", "fleet": fleet, "plan": str(p), "bundle": bundle.name, "bundle_dir": str(bundle),
                   "variant": variant, "date": date, "dir": str(Path(tmp) / "run")}
            env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
            t0 = time.time()
            rec = rm.run_one(job, env)
            out = {"date": date, "verify_pass": rec.get("verify_pass"), "stage_failed": rec.get("stage_failed"),
                   "reason": rec.get("reason"), "seconds": round(time.time() - t0, 1)}
            if rec.get("verify_pass") is True:
                m = json.loads((Path(job["dir"]) / "run" / "metrics.json").read_text(encoding="utf-8"))
                out.update({"served": m["M1"]["served"], "total": m["M1"]["total"],
                            "rate": round(100 * m["M1"]["served"] / m["M1"]["total"], 2),
                            "unserved": m["M2"]["unserved_by_reason"], "refusals": m["M2"]["refusal_events"],
                            "schedule": vehicle_schedule(Path(job["dir"]) / "run")})
            if tmp_bundle:
                shutil.rmtree(tmp_bundle, ignore_errors=True)
            return out


def handle_get(lab: Lab, path: str):
    """(status, content_type, body) 또는 None(이 모듈 경로가 아님)."""
    if path == "/lab":
        return 200, "text/html; charset=utf-8", (WEB / "lab.html").read_bytes()
    if path == "/lab.js":
        return 200, "text/javascript; charset=utf-8", (WEB / "lab.js").read_bytes()
    if path == "/api/lab/meta":
        return 200, "application/json; charset=utf-8", lab.layer_bytes("meta")
    if path.startswith("/api/lab/layer/"):
        return 200, "application/json; charset=utf-8", lab.layer_bytes(path.rsplit("/", 1)[1])
    return None


def handle_post(lab: Lab, path: str, request: dict):
    if path == "/api/lab/generate":
        return lab.generate(request)
    if path == "/api/lab/simulate":
        return lab.simulate(request)
    return None
