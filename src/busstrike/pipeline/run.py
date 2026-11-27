"""파업 셔틀 파이프라인 진입점(docs/design/pipeline.md 2절). 운영 흐름 그대로 단계 1~8.

    python -m busstrike.pipeline --gu guro --strike 1.0 --fleet 11 --algos all \
        --floors none,0.50,0.52,0.54,0.56,0.58,0.60,0.62,0.64 --eval-days 2026-10-27..30 --out <결과 폴더>

단계(산출물은 모두 --out 아래, 끝난 단계·조합은 건너뛴다 = 이어 돌리기):
1 입력        run_config.json
2 수요        bundles/<망>/strike_<날짜>/ (교통카드 집계 → 파업 몫 → 시내버스·마을·경기버스 흡수 뒤 셔틀 대상, 생성기와 같은 수요)
3 후보 생성    cand/cand_<알고리즘>.pkl (walk_eval --cand-cache, 알고리즘마다 한 번)
4 배분        gen/<알고리즘>/<기준>/plans/eqmc07-f<대수>.json (allocate.Model, 형평 하한 = 기준)
5 시뮬레이션   eval/ (eval_rules = 변환기 → M05, 같은 plan(sha256)은 한 번만)
6 검증        eval/results.json의 verify_pass(M07). 통과한 실행만 7단계로
7 지표·파레토  metrics/<태그>/<날짜>.json, pareto.json
8 내보내기     export/<안 id>.json

결정론: 난수 없음(파업 범위 추첨은 고정 seed), 동률은 (값, ID) 순. 시뮬레이터·검증기·eval_rules는 그대로 부른다(고치지 않음).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path

from . import algos as AL
from . import pareto as PA

REPO = Path(__file__).resolve().parents[3]
LAB = REPO / "experiments" / "routegen_lab"
TEST_WEEK = {f"202611{d:02d}" for d in range(2, 7)}       # 테스트 주간(11/02~06)은 열지 않는다
EXP_WEEK = ("20261026", "20261027", "20261028", "20261029", "20261030")
RULE = "same_line-w400"                                   # 평가 규칙(설계와 같은 규칙)
STRIKE_SEED = 20261121


# ---------------------------------------------------------------- 1 입력
@dataclass
class RunConfig:
    gu: str
    strike: float                     # 파업 범위: 1.0 = 전면, 0~1 = 노선 비율(strike.draw_strike, 추첨 0번)
    fleet: int                        # 가용 버스 대수
    algos: list[str]                  # 알고리즘 ID(algos.py)
    floors: list[str]                 # 형평 기준: none | muni | 숫자(역 밖 절대값) | +x | off
    eval_days: list[str]              # 평가 날짜(YYYYMMDD)
    demand_period: str = "exp"        # 생성 수요 기간: exp = 실험 주간 묶음(10/26~30) 전체. 테스트 주간은 열지 않는다
    data_root: str = ""               # guro_exp_work(region_all, osm, routegen_v2/facilities, 망·지자체안 폴더)
    sweep: str = ""                   # 우리 안 망(net/road.json·turns.json)과 hub_sweep.json, exp 묶음
    muni: str = ""                    # 지자체안(eval_municipal 결과: plan.json, road_muni.json)
    params: str = ""                  # 구별 설정(gu_params_r1.json)
    service_start: str = "07:00"
    par: int = 2
    eval_groups: int = 3
    test_flows: bool = True           # True(예전 그대로) = 역 색인(정류장 → 역)에 테스트 주간 묶음의 승객 흐름도 넣는다.
                                      # False = 실험 주간 묶음만 읽는다(테스트 주간 파일을 열지 않음, 운영자 대시보드 실행 기본)
    created: dict = field(default_factory=dict)

    def record(self) -> dict:
        rec = asdict(self)
        if rec["test_flows"]:          # 예전 run_config.json과 같은 모양(이어 돌리기 입력 비교가 그대로 맞게)
            del rec["test_flows"]
        return rec


def parse_days(s: str) -> list[str]:
    """'2026-10-27..30' · '2026-10-28' · '20261027,20261028' → ['20261027', …]."""
    out: list[str] = []
    for part in filter(None, (x.strip() for x in s.split(","))):
        if ".." in part:
            a, b = part.split("..")
            d0 = date.fromisoformat(a) if "-" in a else date(int(a[:4]), int(a[4:6]), int(a[6:]))
            if "-" in b and len(b) == 10:
                d1 = date.fromisoformat(b)
            elif len(b) == 8:
                d1 = date(int(b[:4]), int(b[4:6]), int(b[6:]))
            else:
                d1 = d0.replace(day=int(b))
            d = d0
            while d <= d1:
                out.append(d.strftime("%Y%m%d"))
                d += timedelta(days=1)
        else:
            out.append(part.replace("-", ""))
    bad = sorted(set(out) & TEST_WEEK)
    if bad:
        raise SystemExit(f"테스트 주간(11/02~06)은 열지 않는다: {bad}")
    off = sorted(set(out) - set(EXP_WEEK))
    if off:
        raise SystemExit(f"평가 날짜는 실험 주간 묶음(10/26~30) 안에서: {off}")
    return out


def floor_args(floor: str) -> list[str]:
    """형평 기준 → walk_eval 인자. none = 전체·역 밖 하한 모두 끔, muni = 지자체안 값(예전 기본), 그 밖 = --far-floor 값
    (숫자 = 역 밖 형평 그 값 이상, 전체 하한은 지자체안 값)."""
    if floor == "none":
        return ["--equity-floor", "muni", "--far-floor", "none"]
    if floor == "muni":
        return ["--equity-floor", "muni"]
    if floor == "off" or floor.startswith("+"):
        float(floor[1:]) if floor.startswith("+") else None
        return ["--equity-floor", "muni", "--far-floor", floor]
    float(floor)
    return ["--equity-floor", "muni", "--far-floor", floor]


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _run(cmd: list, log: Path) -> int:
    t0 = time.time()
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, cwd=REPO,
                       env={**os.environ, "PYTHONPATH": str(REPO / "src")})
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(r.stdout + r.stderr + f"\n[{time.time() - t0:.0f}s rc={r.returncode}]\n", encoding="utf-8")
    return r.returncode


class Pipeline:
    def __init__(self, cfg: RunConfig, out: Path):
        self.cfg, self.out = cfg, out
        W = Path(cfg.data_root)
        self.W = W
        self.root = W / "region_all" / f"{cfg.gu}_0610_v4"
        self.test_root = W / "region_all" / f"{cfg.gu}_0610_v4_test" if cfg.test_flows else self.root
        self.sweep = Path(cfg.sweep)
        self.muni = Path(cfg.muni)
        self.osm = W / "osm" / f"{cfg.gu}_roads.json"
        self.fac = W / "routegen_v2" / "facilities" / "facilities_seoul.csv"
        self.params = Path(cfg.params) if cfg.params else LAB / "gu_params_r1.json"
        self.algos = AL.select(",".join(cfg.algos))

    # ------------------------------------------------------------ 1
    def write_config(self) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        p = self.out / "run_config.json"
        rec = self.cfg.record()
        if p.exists():
            old = json.loads(p.read_text(encoding="utf-8"))
            same = {k: v for k, v in old.items() if k not in ("created", "par", "eval_groups", "algos", "floors")}
            now = {k: v for k, v in rec.items() if k not in ("created", "par", "eval_groups", "algos", "floors")}
            if same != now:
                raise SystemExit(f"이 폴더의 run_config.json과 입력이 다르다(이어 돌리기는 같은 입력만): {p}")
            rec["algos"] = sorted(set(old["algos"]) | set(rec["algos"]), key=lambda x: list(AL.BY_ID).index(x))
            rec["floors"] = list(dict.fromkeys(old["floors"] + rec["floors"]))
            rec["created"] = old.get("created", {})
        else:
            rec["created"] = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "code": _git_head()}
        p.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")

    def region_dir(self) -> Path:
        """walk_eval --region. test_flows=False면 실험 주간 묶음 하나만 링크한 폴더(<out>/region_exp)를 준다:
        walk_eval은 <region>/<구>_0610_v4_test가 있으면 그 흐름을 역 색인에 더하므로, 링크하지 않아 열지 않게 한다."""
        if self.cfg.test_flows:
            return self.W / "region_all"
        d = self.out / "region_exp"
        link = d / self.root.name
        if not link.exists():
            d.mkdir(parents=True, exist_ok=True)
            link.symlink_to(self.root, target_is_directory=True)
        if (d / f"{self.cfg.gu}_0610_v4_test").exists():
            raise SystemExit(f"실험 주간 전용 폴더에 테스트 주간 묶음이 있다: {d}")
        return d

    # ------------------------------------------------------------ 2
    def bundles(self) -> Path:
        bd = self.out / "bundles"
        if (bd / "road").exists() and (bd / "muni07").exists():
            return bd
        sys.path[:0] = [str(REPO / "src"), str(LAB)]
        from busstrike.planning.routegen import gu_config, strike as K
        from strike_eval import scenario_bundles
        scenario_bundles(self.root, gu_config.load(LAB / "gu_params.json", self.cfg.gu),
                         K.draw_strike(self.root, self.cfg.strike, 0, STRIKE_SEED),
                         {"road": self.sweep / "net/road.json", "muni07": self.muni / "road_muni.json"},
                         [self.cfg.fleet], bd)
        return bd

    # ------------------------------------------------------------ 3·4
    def base_cmd(self, out: Path) -> list:
        c = self.cfg
        return [sys.executable, LAB / "walk_eval.py", "--sweep", self.sweep, "--region", self.region_dir(),
                "--osm", self.osm, "--gu", c.gu, "--muni", f"muni07={self.muni}", "--loop", "demand",
                "--service-start", c.service_start, "--fleets", c.fleet, "--multi", "--facilities", self.fac,
                "--strike-rate", c.strike, "--strike-draw", "0", "--no-run", "--gen-jobs", "2",
                "--only", "muni07,eqmc07", "--params", self.params, "--out", out]

    def plan_path(self, algo: AL.Algo, floor: str) -> Path:
        return self.out / "gen" / algo.id / _fdir(floor) / "plans" / f"eqmc07-f{self.cfg.fleet}.json"

    def gen_one(self, algo: AL.Algo, floor: str) -> Path:
        p = self.plan_path(algo, floor)
        if p.exists():
            return p
        out = p.parent.parent
        cache = self.out / "cand" / f"cand_{algo.id}.pkl"
        cmd = self.base_cmd(out) + algo.walk_eval_args() + floor_args(floor) + ["--cand-cache", cache, "--lean"]
        rc = _run(cmd, self.out / "gen" / algo.id / f"{_fdir(floor)}.log")
        if rc != 0 or not p.exists():
            raise RuntimeError(f"{algo.id} {floor} 생성·배분 실패 rc={rc} (로그 {out}.log)")
        return p

    def generate(self) -> None:
        """알고리즘마다 첫 기준을 먼저(후보 캐시를 만든다), 나머지 기준은 그 캐시로 배분만."""
        jobs_first = [(a, self.cfg.floors[0]) for a in self.algos]
        jobs_rest = [(a, f) for a in self.algos for f in self.cfg.floors[1:]]
        for phase in (jobs_first, jobs_rest):
            with ThreadPoolExecutor(self.cfg.par) as ex:
                futs = {ex.submit(self.gen_one, a, f): (a, f) for a, f in phase}
                for fu in futs:
                    a, f = futs[fu]
                    try:
                        fu.result()
                        print(f"[생성·배분] {a.id} {f} 끝", flush=True)
                    except Exception as e:  # noqa: BLE001 — 한 조합 실패가 나머지를 막지 않게(기록)
                        print(f"[생성·배분] {a.id} {f} 실패: {e}", flush=True)

    # ------------------------------------------------------------ 5·6
    def plan_index(self) -> list[dict]:
        rows = []
        for a in self.algos:
            for f in self.cfg.floors:
                p = self.plan_path(a, f)
                if p.exists():
                    rows.append({"algo": a.id, "floor": f, "plan": str(p), "sha": sha256(p)})
        (self.out / "plans_index.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        return rows

    def evaluate(self, rows: list[dict]) -> None:
        bd = self.bundles()
        ev = self.out / "eval"
        sp = self.out / "eval_spec.json"
        spec = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {
            "gu": self.cfg.gu, "root": str(self.root), "test_root": str(self.test_root),
            "nb": None, "nb_far": None, "osm": str(self.osm), "combos": [{"station_mode": "same_line", "walk_radius_m": 400}],
            "renumber": False, "equity": True, "keep_events": "", "plans": [],
            "strike": {"rate": self.cfg.strike, "draw": 0, "seed": STRIKE_SEED}, "equity_start": self.cfg.service_start,
            "_설명": "busstrike.pipeline 5·6단계(같은 plan sha256은 한 번)"}
        have = {p["label"] for p in spec["plans"]}
        want = {}
        for r in rows:
            want.setdefault(_tag(r["sha"]), (r["plan"], "road"))
        want["muni"] = (str(self.muni / "plan.json"), "muni07")
        for tag, (path, net) in want.items():
            if tag in have:
                continue
            spec["plans"].append({"tag": f"{tag}-f{self.cfg.fleet}", "label": tag, "fleet": self.cfg.fleet, "plan": path,
                                  "bundles": {"exp": str(bd / net)}, "dates": self.cfg.eval_days})
        sp.write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
        done = _results(ev)
        todo = [t for t in want if not all(any(x["label"] == t and x["date"] == d for x in done) for d in self.cfg.eval_days)]
        if todo:
            rc = _run([sys.executable, LAB / "eval_rules.py", "--spec", sp, "--out", ev, "--groups",
                       self.cfg.eval_groups, "--jobs", "2"], self.out / "eval.log")
            if rc:
                print(f"경고: eval_rules rc={rc}(M07 미통과·변환 실패가 있을 수 있음, eval.log)", flush=True)
        self.collect()

    def collect(self) -> None:
        """passenger_outcomes가 남은 실행마다 지표를 계산하고 큰 파일을 지운다(pareto_1125 run.py collect와 같은 규칙)."""
        from busstrike.planning.routegen import data as D
        from . import metrics as MX
        bd = self.bundles()
        ev = self.out / "eval"
        cand = MX.station_side(self.root)
        bp = self.out / "baseline.json"
        if not bp.exists():
            bp.write_text(json.dumps(MX.baseline(self.root, bd / "road"), separators=(",", ":")), encoding="utf-8")
        base = json.loads(bp.read_text(encoding="utf-8"))
        nb = json.loads((ev / "nb400.json").read_text(encoding="utf-8"))
        bj = self.out / "bands.json"
        if not bj.exists():
            net = D.load_network(self.root)
            info = D.check_root(self.root, "exp")
            st_stops = sorted({x for s in net.stations.values() for x in (s.access_stop, s.egress_stop) if x})
            home = {s for s, x in net.stops.items() if x.gu == info["gu"]}
            nbf = json.loads((ev / "nb800_home.json").read_text(encoding="utf-8"))
            bj.write_text(json.dumps({"st_stops": st_stops, "bands": MX.home_bands(home, nbf, set(st_stops))},
                                     ensure_ascii=False), encoding="utf-8")
        bdoc = json.loads(bj.read_text(encoding="utf-8"))
        bands, st_set = bdoc["bands"], set(bdoc["st_stops"])
        origs: dict = {}
        for po in sorted((ev / "runs").rglob("passenger_outcomes.csv")):
            rd = po.parent.parent
            d = rd.parent.name.split("_")[1]
            tag = rd.relative_to(ev / "runs").parts[0].rsplit("-f", 1)[0]
            mj = self.out / "metrics" / tag / f"{d}.json"
            if not mj.exists():
                if d not in origs:
                    origs[d] = MX.orig_rows(bd / "road" / f"strike_{d}", cand)
                gdir = rd.parents[2]
                walk_m = json.loads((gdir / f"walk_m_{d}.json").read_text(encoding="utf-8"))
                m = MX.run_metrics(rd, walk_m, origs[d], base, self.root, nb, bands, st_set)
                mj.parent.mkdir(parents=True, exist_ok=True)
                mj.write_text(json.dumps(m, separators=(",", ":")), encoding="utf-8")
            for name in ("events.jsonl", "realization.jsonl", "passenger_outcomes.csv"):
                (rd / "run" / name).unlink(missing_ok=True)

    # ------------------------------------------------------------ 7
    def point(self, tag: str, nb: dict, gu_stops: set[str]) -> tuple[dict | None, str | None]:
        """한 plan(태그)의 날짜 묶음 지표. 평가 날짜 모두 M07 통과해야 한다."""
        from . import metrics as MX
        rs = [r for r in _results(self.out / "eval") if r["label"] == tag and r["date"] in self.cfg.eval_days
              and r["rule"] == RULE]
        ok = [r for r in rs if r.get("verify_pass")]
        if len(ok) < len(self.cfg.eval_days):
            return None, f"M07 통과 {len(ok)}/{len(self.cfg.eval_days)}"
        ms = []
        for r in ok:
            mj = self.out / "metrics" / tag / f"{r['date']}.json"
            if not mj.exists():
                return None, "지표 파일 없음"
            ms.append(json.loads(mj.read_text(encoding="utf-8")))
        m = MX.aggregate(ok, ms, gu_stops, nb)
        if abs(m["check_total"]) > 1e-6 or abs(m.get("check_diag", 0)) > 1e-6:      # 합 확인(pareto_1125 report.py와 같음)
            return None, f"합 확인 불일치 {m['check_total']}, {m.get('check_diag')}"
        m["_pid"] = [x["pid_ratio"] for x in ms]
        m["_dates"] = [r["date"] for r in ok]
        m["m07"] = f"{len(ok)}/{len(rs)}"
        return m, None

    def pareto(self) -> dict:
        import csv
        nb = json.loads((self.out / "eval" / "nb400.json").read_text(encoding="utf-8"))
        from busstrike.planning.routegen import data as D
        gu_ko = D.check_root(self.root, "exp")["gu"]
        gu_stops = {r["stop_id"] for r in csv.DictReader((self.root / "network/stops.csv").open(encoding="utf-8"))
                    if r["gu"] == gu_ko}
        muni_m, why = self.point("muni", nb, gu_stops)
        cache: dict = {}
        rows = []
        excluded = []
        for r in json.loads((self.out / "plans_index.json").read_text(encoding="utf-8")):
            a = AL.BY_ID[r["algo"]]
            tag = _tag(r["sha"])
            if tag not in cache:
                cache[tag] = self.point(tag, nb, gu_stops)
            m, why_ = cache[tag]
            pp = Path(r["plan"])
            rep = json.loads((pp.parent / (pp.stem + ".plan_report.json")).read_text(encoding="utf-8"))
            eq = rep.get("equity") or {}
            short_far = float(eq.get("short_far") or 0.0)
            short_total = float(eq.get("short_total") or 0.0)
            row = {"algo": a.id, "floor": r["floor"], "id": f"{a.id}__{_fdir(r['floor'])}", "base": a.base,
                   "algo_name": a.name, "line_group": a.line_group, "fill": a.fill, "plan_sha256": r["sha"],
                   "floor_met": short_far <= 1e-6 and short_total <= 1e-6, "short_far": round(short_far, 1),
                   "short_total": round(short_total, 1), "eval_tag": tag}
            if m is None:
                excluded.append({"id": row["id"], "why": why_})
                row.update({k: None for k in ("served_rate", "equity_far", "isolation", "time_ratio_rode",
                                              "time_ratio_common", "refused_unserved", "reasons", "routes",
                                              "cycle_sum_min")})
            else:
                row.update(_metric_fields(m, muni_m))
            rows.append(row)
        plans = PA.merge_same_plan(rows)
        for p in plans:
            p["id"] = p["id"].split("__")[0] + "__" + "+".join(_fdir(f) for f in p["floors"])
            p["export_path"] = f"export/{p['id']}.json" if p.get("served_rate") is not None else None
        muni = None if muni_m is None else {"id": "muni", **_metric_fields(muni_m, muni_m)}
        doc = PA.document(self.cfg.gu, self.cfg.fleet, self.cfg.strike, self.cfg.eval_days,
                          None if muni_m is None else round(muni_m["total_sim"], 2), plans, muni)
        if muni is None:
            doc["muni_excluded"] = why
        doc["excluded"] = excluded
        (self.out / "pareto.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        return doc

    # ------------------------------------------------------------ 8
    def export(self, doc: dict) -> None:
        from . import export as EX
        idx = {r["sha"]: r for r in json.loads((self.out / "plans_index.json").read_text(encoding="utf-8"))}
        todo = [p for p in doc["plans"] if p.get("export_path") and not (self.out / p["export_path"]).exists()]
        if not todo:
            return
        ex = EX.Exporter(self.root, self.osm, self.sweep / "net" / "road.json")
        for p in todo:
            src = Path(idx[p["plan_sha256"]]["plan"])
            EX.write(ex, p["id"], src, src.parent / (src.stem + ".plan_report.json"), self.out / p["export_path"])

    def run(self, stages: set[str]) -> None:
        self.write_config()
        if "demand" in stages:
            self.bundles()
        if "gen" in stages:
            self.generate()
        rows = self.plan_index()
        if "eval" in stages:
            self.evaluate(rows)
        if "pareto" in stages:
            doc = self.pareto()
            if "export" in stages:
                self.export(doc)


def _metric_fields(m: dict, muni_m: dict | None) -> dict:
    import statistics as st
    common = None
    if muni_m is not None:
        mp = dict(zip(muni_m["_dates"], muni_m["_pid"]))
        xs = [v for d, pr in zip(m["_dates"], m["_pid"]) for k, v in pr.items() if k in mp.get(d, {})]
        common = round(st.median(xs), 4) if xs else None
    r4 = lambda x: None if x is None else round(x, 4)  # noqa: E731
    return {"served_rate": r4(m["fill_rate"]), "equity_far": r4(m["eq_far"]), "isolation": r4(m["isolation"]),
            "time_ratio_rode": r4(m.get("vs_bus_median")), "time_ratio_common": common,
            "refused_unserved": r4(m.get("refused_unserved")),
            "reasons": {"noconn": r4(m.get("noconn")), "order": r4(m.get("order")), "wait": r4(m.get("wait")),
                        "refused": r4(m.get("refused_unserved"))},
            "routes": m.get("routes"), "cycle_sum_min": m.get("cycle_sum_min"), "m07": m.get("m07")}


def _fdir(floor: str) -> str:
    return "floor_" + floor.replace("+", "plus").replace(".", "p")


def _tag(sha: str) -> str:
    return "p" + sha[:12]


def _results(ev: Path) -> list[dict]:
    p = ev / "results.json"
    return json.loads(p.read_text(encoding="utf-8"))["runs"] if p.exists() else []


def _git_head() -> str | None:
    try:
        return subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    except OSError:
        return None


STAGES = ("demand", "gen", "eval", "pareto", "export")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m busstrike.pipeline", description=__doc__.split("\n")[0])
    ap.add_argument("--gu", required=True)
    ap.add_argument("--strike", type=float, default=1.0, help="파업 범위: 1.0 = 전면, 0~1 = 노선 비율")
    ap.add_argument("--fleet", type=int, required=True, help="가용 버스 대수")
    ap.add_argument("--algos", default="all", help=f"all 또는 쉼표로 이은 ID: {', '.join(AL.BY_ID)}")
    ap.add_argument("--floors", default="muni", help="형평 기준(쉼표): none, muni, 숫자(역 밖 형평 절대값), +x, off")
    ap.add_argument("--eval-days", required=True, help="예: 2026-10-27..30 또는 2026-10-28")
    ap.add_argument("--demand-period", default="exp", choices=("exp",), help="생성 수요 기간(실험 주간 묶음만)")
    ap.add_argument("--data-root", type=Path, required=True, help="데이터 폴더(region_all·osm·routegen_v2 등이 있는 곳)")
    ap.add_argument("--sweep", type=Path, required=True, help="우리 안 망 폴더(net/road.json, hub_sweep.json, exp/bundles)")
    ap.add_argument("--muni", type=Path, required=True, help="지자체안 폴더(plan.json, road_muni.json)")
    ap.add_argument("--params", type=Path, default=None, help="구별 설정(기본 experiments/routegen_lab/gu_params_r1.json)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--stages", default=",".join(STAGES), help=f"돌릴 단계(쉼표): {', '.join(STAGES)}")
    ap.add_argument("--par", type=int, default=2, help="동시에 돌릴 생성·배분 수")
    ap.add_argument("--eval-groups", type=int, default=3)
    ap.add_argument("--no-test-flows", action="store_true",
                    help="역 색인에 테스트 주간 흐름을 넣지 않는다(실험 주간 묶음만 연다). eval_rules에는 test_root = 실험 주간 묶음을 준다"
                         "(같은 흐름을 두 번 더해도 역 고르기는 비율로 정해져 결과가 같다)")
    a = ap.parse_args(argv)
    floors = [x.strip() for x in a.floors.split(",") if x.strip()]
    for f in floors:
        floor_args(f)
    cfg = RunConfig(gu=a.gu, strike=a.strike, fleet=a.fleet, algos=[x.id for x in AL.select(a.algos)], floors=floors,
                    eval_days=parse_days(a.eval_days), demand_period=a.demand_period, data_root=str(a.data_root.resolve()),
                    sweep=str(a.sweep.resolve()), muni=str(a.muni.resolve()),
                    params=str(a.params.resolve()) if a.params else "", par=a.par, eval_groups=a.eval_groups,
                    test_flows=not a.no_test_flows)
    stages = set(filter(None, a.stages.split(",")))
    bad = stages - set(STAGES)
    if bad:
        raise SystemExit(f"알 수 없는 단계: {sorted(bad)}")
    Pipeline(cfg, a.out.resolve()).run(stages)
    return 0
