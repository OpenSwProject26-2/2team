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

from busstrike.planning.routegen import allocate as A
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

    def demand(self, variant: str) -> D.Demand:
        if variant not in D.VARIANTS:
            raise LabError("INPUT_INVALID", f"변형을 모른다: {variant}")
        if self._rows is None:
            self._rows = D.load_flows(self.root)
        if variant not in self._demand:
            self._demand[variant] = D.build_demand(self._rows, variant)
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
        with self.lock:
            t0 = time.time()
            net = self.network(net_name)
            demand = self.demand(variant)
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
            return {"net": net_name, "variant": variant, "fleet": fleet, "params": {**prm}, "cover": cover,
                    "covered_groups": a["covered_groups"],
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
        bundle = self.dir / net_name / variant / "exp" / "bundles" / f"{variant}_{date}"
        if not (bundle / f"scenario_f{fleet}.json").exists():
            raise LabError("DATA_INCOMPLETE", f"그 날짜·대수 묶음이 없다: {bundle.name} f{fleet}")
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
