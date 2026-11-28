"""Read-only adapter for the published routegen v4 report and optional run artifacts.

결과 폴더(`results_root/<구>/manifest.json`)는 두 형식을 읽는다.
- v4(PR #41): 구·대수·변형·안(ours/sp)이 아래 상수로 정해져 있고, 5일 평균은 보고서 md에서 읽는다.
- `schema: "routegen-results/1"`: 구·대수·변형·안 목록과 파일 위치를 manifest가 알려 준다. 5일 평균은 날짜별
  M05 `metrics.json`에서 계산한다(보고서를 읽지 않는다). 형식은 `RESULTS_SCHEMA` 주석을 본다.
"""

from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from threading import Lock

from .d_geo import DataError

ROOT = Path(__file__).resolve().parents[3]
REPORT = ROOT / "experiments" / "reports" / "routegen_v4.md"
REGIONS = {
    "guro": ("구로구", [11, 13], [126.887, 37.495]),
    "dongjak": ("동작구", [20, 40], [126.939, 37.512]),
    "gangseo": ("강서구", [20, 40], [126.849, 37.551]),
    "gangdong": ("강동구", [35, 70], [127.124, 37.530]),
}
SEOUL_DISTRICTS = (
    "강남구",
    "강동구",
    "강북구",
    "강서구",
    "관악구",
    "광진구",
    "구로구",
    "금천구",
    "노원구",
    "도봉구",
    "동대문구",
    "동작구",
    "마포구",
    "서대문구",
    "서초구",
    "성동구",
    "성북구",
    "송파구",
    "양천구",
    "영등포구",
    "용산구",
    "은평구",
    "종로구",
    "중구",
    "중랑구",
)
VARIANTS = ("affected", "affected_maeul", "affected_gg", "affected_maeul_gg")
RESULTS_SCHEMA = "routegen-results/1"
# manifest(routegen-results/1) 예:
# {"schema": "routegen-results/1", "region": "guro", "gu": "구로구", "center": [126.87, 37.49],
#  "network_snapshot_id": "...", "root": "<구 묶음 폴더(stops.csv)>", "network_file": "<결과 폴더 기준 travel_times 경로>",
#  "fleets": [11, 16], "variants": ["precise_affected_maeul_gg"],
#  "plans": [{"id": "hub135_s12", "name": "우리 안 · 허브 135·정차 12", "method": "...", "baseline": false,
#             "fleet": 11, "variant": "precise_affected_maeul_gg", "plan_id": "...", "plan_file": "<상대 경로>",
#             "run_label": "ours", "runs": {"exp": "<상대 경로>/runs.json", "test": "..."}}]}
PERIODS = {
    "exp": [f"2026-10-{d:02d}" for d in range(26, 31)],
    "test": [f"2026-11-{d:02d}" for d in range(2, 7)],
}


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_index(path: Path) -> dict:
    """실행 색인(runs.json). 쓰는 중이거나 깨진 파일은 없는 것으로 본다(대시보드를 멈추지 않는다)."""
    try:
        return _read(path)
    except (OSError, ValueError):
        return {}


@lru_cache(maxsize=128)
def _hourly(path: Path) -> list[dict]:
    """Aggregate M05 outcomes in memory; never return passenger identifiers."""
    hours = defaultdict(lambda: {"total": 0, "served": 0, "unserved": 0, "censored": 0})
    with path.open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            hour = int(row["time_s"]) // 3600
            hours[hour]["total"] += 1
            if row["outcome"] in {"served", "unserved", "censored"}:
                hours[hour][row["outcome"]] += 1
    return [
        {
            "hour": hour,
            **counts,
            "rate_percent": 100 * counts["served"] / counts["total"]
            if counts["total"] and not counts["censored"]
            else None,
        }
        for hour, counts in sorted(hours.items())
    ]


@lru_cache(maxsize=128)
def _spatial_outcomes(path: Path) -> dict[str, dict]:
    """Aggregate B outcomes by origin stop; never return passenger IDs or raw rows."""
    counts = defaultdict(
        lambda: {"total": 0, "served": 0, "unserved": 0, "censored": 0}
    )
    with path.open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            origin = row.get("origin")
            if not origin:
                continue
            entry = counts[origin]
            entry["total"] += 1
            hour = int(row.get("time_s") or 0) // 3600
            hourly = entry.setdefault("hours", {})
            hour_entry = hourly.setdefault(
                str(hour).zfill(2),
                {"total": 0, "served": 0, "unserved": 0, "censored": 0},
            )
            hour_entry["total"] += 1
            if row.get("outcome") in {"served", "unserved", "censored"}:
                entry[row["outcome"]] += 1
                hour_entry[row["outcome"]] += 1
    return dict(counts)


def report_rows() -> dict[tuple, dict]:
    """Parse only section 3's M05 five-day means, preserving published precision."""
    doc = (
        REPORT.read_text(encoding="utf-8")
        .split("## 3. 4개 구 × 대수 × 실험/테스트", 1)[1]
        .split("## 4.", 1)[0]
    )
    rows = {}
    region = None
    for line in doc.splitlines():
        if line.startswith("### "):
            region = next((key for key, val in REGIONS.items() if val[0] in line), None)
        if not region or not line.startswith("| `affected"):
            continue
        cells = [cell.strip() for cell in line.strip("| ").split("|")]
        if len(cells) != 11:
            continue
        variant, fleet, label = cells[:3]
        variant = variant.strip("`")
        if variant not in VARIANTS:
            continue
        plan = {"우리 안": "ours", "피더안": "sp"}.get(label)
        if plan is None:
            continue
        for period, rate_col, total_col, served_col in (
            ("exp", 4, 7, 9),
            ("test", 5, 8, 10),
        ):
            rows[(region, int(fleet), variant, plan, period)] = {
                "rate_percent": float(re.match(r"([\d.]+)%", cells[rate_col]).group(1)),
                "total": float(cells[total_col].replace(",", "")),
                "served": float(cells[served_col].replace(",", "")),
                "route_count": int(cells[3]),
                "source": "report_v4_5day_mean",
            }
    return rows


def reason_rows() -> dict[tuple, dict]:
    doc = (
        REPORT.read_text(encoding="utf-8")
        .split("**미충족 사유 분해**", 1)[1]
        .split("**", 1)[0]
    )
    rows = {}
    for line in doc.splitlines():
        cells = [cell.strip() for cell in line.strip("| ").split("|")]
        if len(cells) != 10:
            continue
        region = next((key for key, val in REGIONS.items() if val[0] == cells[0]), None)
        plan = {"우리 안": "ours", "피더안": "sp"}.get(cells[2])
        if region and plan:
            vals = [float(cells[i].replace(",", "")) for i in range(4, 9)]
            rows[(region, int(cells[1]), plan)] = dict(
                zip(
                    (
                        "no_connection",
                        "service_end",
                        "wait",
                        "journey",
                        "refusal_events",
                    ),
                    vals,
                )
            )
    return rows


class SimulationStore:
    def __init__(
        self, results_root: Path | None = None, inputs_root: Path | None = None,
        lab_layers: Path | None = None,
    ):
        self.lab_layers = lab_layers.resolve() if lab_layers else None   # <lab_all>/<구>/layers: 결과 없는 구도 lab 지도
        self._lab_cache: dict[str, dict] = {}
        self.results_root = results_root.resolve() if results_root else None
        self.inputs_root = inputs_root.resolve() if inputs_root else None
        self.report = report_rows()
        self.reasons = reason_rows()
        self._mean_cache: dict[tuple, dict | None] = {}

    # ------------------------------------------------------------ 결과 목록(manifest)
    def _catalog(self, region: str) -> dict | None:
        """routegen-results/1 manifest(없거나 v4 형식이면 None)."""
        path = self._manifest(region)
        if not path.is_file():
            return None
        man = _read(path)
        return man if man.get("schema") == RESULTS_SCHEMA else None

    def _catalog_regions(self) -> list[str]:
        if not self.results_root or not self.results_root.is_dir():
            return []
        return sorted(p.parent.name for p in self.results_root.glob("*/manifest.json")
                      if _read(p).get("schema") == RESULTS_SCHEMA)

    def _region(self, region: str) -> dict | None:
        """구 정보: 이름·대수·중심·안 목록·변형. manifest가 있으면 그것, 없으면 v4 상수."""
        man = self._catalog(region)
        if man:
            plans, seen = [], set()
            for p in man.get("plans", []):
                if p["id"] not in seen:
                    seen.add(p["id"])
                    plans.append({k: p.get(k) for k in ("id", "name", "method", "baseline")})
            return {"id": region, "name": man.get("gu", region), "fleets": man.get("fleets", []),
                    "center": man.get("center"), "plans": plans, "variants": man.get("variants", []),
                    "source": "manifest"}
        if region in REGIONS:
            name, fleets, center = REGIONS[region]
            return {"id": region, "name": name, "fleets": fleets, "center": center,
                    "plans": [{"id": "ours", "name": "우리 안", "method": "C routegen 노선·배분 최적화", "baseline": False},
                              {"id": "sp", "name": "최단경로 피더안", "method": "C 최단경로 피더 기준안", "baseline": True}],
                    "variants": list(VARIANTS), "source": "report_v4"}
        return None

    def _lab(self, region: str) -> dict | None:
        """노선 실험실 구별 레이어(lab_all/<구>/layers). 25개 구 모두 있어 결과 없는 구도 같은 지도를 그린다."""
        if not self.lab_layers or not region.isidentifier():
            return None
        if region in self._lab_cache:
            return self._lab_cache[region]
        d = self.lab_layers / region / "layers"
        if not (d / "demand.json").is_file():
            self._lab_cache[region] = None
            return None
        def get(name):
            f = d / f"{name}.json"
            return _read(f) if f.is_file() else None
        demand, stops = get("demand") or {}, get("stops") or {}
        keep = ["affected", "affected_maeul", "affected_gg", "affected_maeul_gg",
                "precise_affected_maeul", "precise_affected_gg", "precise_affected_maeul_gg"]
        variants = {k: demand[k] for k in keep if k in demand}
        used = set().union(*[set(v) for v in variants.values()]) if variants else set()
        out = {"source": f"routegen lab layers ({d})", "lab_only": True,
               "absorb_demand": {"unit": "하루 인원(실험 주간 월~금 평균)", "variants": variants,
                                 "stops": {sid: stops[sid][:3] for sid in used if sid in stops},
                                 "note": "precise_* = 마을·경기버스가 남는 정원만큼 태운 뒤 남는 수요(lab 정밀 흡수 기본 가정)"},
               "stations": get("stations") or [], "bus_roads": get("bus_roads"),
               "absorbing_routes": get("absorbing_routes") or [], "strike_routes": get("strike_routes") or []}
        self._lab_cache[region] = out
        return out

    def _layers(self, region: str) -> dict | None:
        """write_dashboard_layers.py가 만든 구별 보조 자료(노선 도로 좌표·흡수 조건별 수요). 없으면 lab 레이어, 그것도 없으면 None."""
        man = self._catalog(region)
        name = (man or {}).get("dashboard_layers")
        if not name or not self.results_root:
            return self._lab(region)
        path = self.results_root / region / name
        if not path.is_file():
            return self._lab(region)
        cache = getattr(self, "_layers_cache", None)
        if cache is None:
            cache = self._layers_cache = {}
        stamp = path.stat().st_mtime
        hit = cache.get(region)
        if hit and hit[0] == stamp:
            return hit[1]
        data = _read(path)
        if data.get("network_snapshot_id") and man.get("network_snapshot_id") and data["network_snapshot_id"] != man["network_snapshot_id"]:
            data = None
        cache[region] = (stamp, data)
        return data

    def absorb_demand(self, q: dict) -> dict:
        """정류장별 파업 잔여 수요(흡수 없음·마을·경기·둘 다). 지도 원 크기를 운행 조건에 맞춰 바꾸는 데 쓴다."""
        region = q.get("region", [""])[0]
        layers = self._layers(region)
        if not layers or not layers.get("absorb_demand", {}).get("variants"):
            return {"status": "unavailable", "region": region,
                    "message": "이 구의 흡수 조건별 정류장 수요(dashboard_layers.json)가 연결되지 않았습니다."}
        ad = layers["absorb_demand"]
        variants = ad["variants"]
        features = []
        for sid, (lon, lat, name) in ad["stops"].items():
            values = {k: v.get(sid, 0) for k, v in variants.items()}
            if not any(values.values()):
                continue
            features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
                             "properties": {"stop_id": sid, "name": name, **values}})
        totals = {k: round(sum(v.values()), 1) for k, v in variants.items()}
        return {"status": "available", "region": region, "unit": ad.get("unit"), "note": ad.get("note"),
                "variants": sorted(variants), "totals": totals, "features": features,
                "source": layers.get("source")}

    def transit_routes(self, q: dict) -> dict:
        """이 구를 지나는 마을버스·경기버스(흡수 노선)와 파업 시내버스 노선의 좌표(lab 레이어). 지도에 어디 지나는지 그린다."""
        region = q.get("region", [""])[0]
        lab = self._lab(region)
        if not lab:
            return {"status": "unavailable", "region": region, "message": "이 구의 lab 노선 레이어가 연결되지 않았습니다."}
        def line(r, mode):
            coords = [[round(x, 5), round(y, 5)] for x, y in r.get("coords") or []]
            return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords},
                    "properties": {"mode": mode, "id": r.get("id"), "name": r.get("name"), "kind": r.get("type"),
                                   "stops": len(r.get("stops") or [])}} if len(coords) >= 2 else None
        feats = [line(r, "village" if r.get("mode") == "maeul" else "gyeonggi") for r in lab["absorbing_routes"]]
        feats += [line(r, "city") for r in lab["strike_routes"]]
        feats = [f for f in feats if f]
        count = {m: len({f["properties"]["id"] for f in feats if f["properties"]["mode"] == m}) for m in ("village", "gyeonggi", "city")}
        return {"status": "available", "region": region, "type": "FeatureCollection", "features": feats, "counts": count,
                "note": "마을·경기 = 파업 승객을 남는 정원만큼 태우는 흡수 노선(lab 가정) · 시내 = 이 시나리오에서 파업하는 노선"}

    def bus_roads(self, q: dict) -> dict:
        """routegen lab의 버스 통행 가능 도로(OSM 도로 중 버스가 다닐 수 있는 구간)."""
        region = q.get("region", [""])[0]
        feat = (self._layers(region) or {}).get("bus_roads")
        if not feat:
            return {"status": "unavailable", "region": region,
                    "message": "이 구의 버스 통행 가능 도로(dashboard_layers.json bus_roads)가 연결되지 않았습니다."}
        return {"status": "available", "region": region, "type": "FeatureCollection", "features": [feat],
                "edges": (feat.get("properties") or {}).get("edges")}

    def _regions(self) -> list[dict]:
        ids = list(REGIONS) + [r for r in self._catalog_regions() if r not in REGIONS]
        return [x for x in (self._region(r) for r in ids) if x]

    def _plan_info(self, region: str, plan: str) -> dict:
        info = self._region(region) or {}
        return next((p for p in info.get("plans", []) if p["id"] == plan), {"id": plan, "name": plan})

    def _baseline(self, region: str, plan: str) -> str | None:
        """비교 기준안(같은 구에서 baseline 표시된 안, 없으면 다른 첫 안)."""
        plans = (self._region(region) or {}).get("plans", [])
        base = next((p["id"] for p in plans if p.get("baseline") and p["id"] != plan), None)
        return base or next((p["id"] for p in plans if p["id"] != plan), None)

    def _mean_row(self, region: str, fleet: int, variant: str, plan: str, period: str) -> dict | None:
        """5일 평균: v4는 보고서, manifest는 날짜별 M05 metrics 평균."""
        man = self._catalog(region)
        if not man:
            return self.report.get((region, fleet, variant, plan, period))
        base = self.results_root / region
        index, _ = self._runs_index(region, fleet, variant, plan, period)
        try:
            stamp = index.stat().st_mtime if index else None
        except OSError:
            stamp = None
        key = (region, fleet, variant, plan, period, stamp)
        if key in self._mean_cache:             # 실행 색인이 바뀌지 않았으면 다시 읽지 않는다
            return self._mean_cache[key]
        self._mean_cache[key] = row = self._mean_compute(base, man, fleet, variant, plan, period)
        return row

    def _mean_compute(self, base: Path, man: dict, fleet: int, variant: str, plan: str, period: str) -> dict | None:
        days = []
        for date in PERIODS[period]:
            art = self._catalog_artifacts(base, man, fleet, variant, plan, period, date, light=True)
            m1 = (art.get("metrics") or {}).get("M1")
            rec = art.get("run_record", {})
            if isinstance(m1, dict) and m1.get("total") and rec.get("sim_rc") in (0, None) \
                    and rec.get("stage_failed") is None:
                days.append((m1, (art.get("metrics") or {}).get("M2", {}), art.get("verifier") or {}))
        if not days:
            return None
        n = len(days)
        total = sum(d[0]["total"] for d in days) / n
        served = sum(d[0]["served"] for d in days) / n
        reasons: dict[str, float] = defaultdict(float)
        for _, m2, _ in days:
            for k, v in (m2.get("unserved_by_reason") or {}).items():
                reasons[k] += v / n
            reasons["refusal_events"] += (m2.get("refusal_events") or 0) / n
        art = self._catalog_artifacts(base, man, fleet, variant, plan, period, "mean", light=True)
        return {"total": round(total, 2), "served": round(served, 2), "unserved": round(total - served, 2),
                "rate_percent": 100 * served / total if total else None, "days": n,
                "m07_pass_days": sum(1 for d in days if d[2].get("pass") is True),
                "route_count": len(art.get("plan", {}).get("routes", [])), "reasons": dict(reasons),
                "source": "M05_5day_mean"}

    def options(self) -> dict:
        regions = self._regions()
        return {
            "regions": regions,
            "variants": sorted({v for r in regions for v in r["variants"]}, key=lambda v: (v not in VARIANTS, v)),
            "plans": sorted({p["id"] for r in regions for p in r["plans"]}),
            "periods": PERIODS,
            "report_source": "experiments/reports/routegen_v4.md §3, §6 (PR #41) · manifest 형식은 날짜별 M05 원본",
            "artifacts_connected": {r["id"]: self._manifest(r["id"]).exists() for r in regions},
        }

    def _manifest(self, region: str) -> Path:
        return (
            (self.results_root / region / "manifest.json")
            if self.results_root
            else Path("__unconnected__")
        )

    def _selection(self, q: dict) -> tuple:
        region = q.get("region", ["guro"])[0]
        info = self._region(region)
        if info is None:
            raise DataError("INPUT_INVALID", "지역 또는 차량 대수가 올바르지 않습니다.")
        variant = q.get("variant", [info["variants"][0] if info["variants"] else "affected"])[0]
        plan = q.get("plan", [info["plans"][0]["id"] if info["plans"] else "ours"])[0]
        period = q.get("period", ["exp"])[0]
        date = q.get("date", ["mean"])[0]
        try:
            fleet = int(q.get("fleet", [str(info["fleets"][0])])[0])
        except (IndexError, ValueError) as error:
            raise DataError(
                "INPUT_INVALID", "지역 또는 차량 대수가 올바르지 않습니다."
            ) from error
        if (
            fleet not in info["fleets"]
            or variant not in info["variants"]
            or plan not in {p["id"] for p in info["plans"]}
            or period not in PERIODS
            or (date != "mean" and date not in PERIODS[period])
        ):
            raise DataError(
                "INPUT_INVALID", "시뮬레이션 선택 조건이 올바르지 않습니다."
            )
        return region, fleet, variant, plan, period, date

    def _artifacts(
        self, region: str, fleet: int, variant: str, plan: str, period: str, date: str
    ) -> dict:
        base = self.results_root / region if self.results_root else None
        if not base or not (base / "manifest.json").is_file():
            return {}
        man = _read(base / "manifest.json")
        if man.get("schema") == RESULTS_SCHEMA:
            return self._catalog_artifacts(base, man, fleet, variant, plan, period, date)
        selected = next(
            (
                p
                for p in man.get("plans", [])
                if p.get("fleet") == fleet
                and p.get("variant") == variant
                and p.get("label") == plan
            ),
            None,
        )
        if not selected:
            return {"manifest": man}
        # The producer stores absolute paths. Resolve files inside the supplied result root instead.
        plan_file = base / "plans" / f"{selected['plan_id']}.json"
        candidate_file = base / "candidates" / f"{variant}.json"
        out = {"manifest": man, "selection": selected}
        if plan_file.is_file():
            out["plan"] = _read(plan_file)
            if out["plan"].get("network_snapshot_id") != man.get("network_snapshot_id"):
                raise DataError(
                    "DATA_VERSION_MISMATCH", "계획과 manifest의 교통망 ID가 다릅니다."
                )
        if candidate_file.is_file():
            out["candidates"] = _read(candidate_file)
        if date == "mean":
            return out
        index = (
            base / ("runs" if period == "exp" else "runs_test") / variant / "runs.json"
        )
        if not index.is_file():
            return out
        rec = next(
            (
                r
                for r in _read(index).get("runs", [])
                if r.get("label") == plan
                and r.get("fleet") == fleet
                and str(r.get("date")) == date.replace("-", "")
            ),
            None,
        )
        if rec is None:
            return out
        run_dir = (
            index.parent / plan / f"{variant}_{date.replace('-', '')}" / f"f{fleet}"
        )
        out["run_record"] = rec
        for key, file in (
            ("metrics", run_dir / "run" / "metrics.json"),
            ("verifier", run_dir / "verifier_report.json"),
            ("plan_m05", run_dir / "plan_m05.json"),
        ):
            if file.is_file():
                out[key] = _read(file)
        outcomes = run_dir / "run" / "passenger_outcomes.csv"
        if outcomes.is_file():
            out["hourly"] = _hourly(outcomes)
            out["spatial_outcomes"] = _spatial_outcomes(outcomes)
        return out

    def _catalog_artifacts(self, base: Path, man: dict, fleet: int, variant: str, plan: str,
                           period: str, date: str, light: bool = False) -> dict:
        """routegen-results/1: manifest의 안 항목이 계획·실행 색인 위치를 알려 준다."""
        entry = next((p for p in man.get("plans", []) if p.get("id") == plan and p.get("fleet") == fleet
                      and p.get("variant") == variant), None)
        if entry is None:
            return {"manifest": man}
        out = {"manifest": man, "selection": {**entry, "routes": {}}}
        plan_file = base / entry["plan_file"] if entry.get("plan_file") else None
        if plan_file and plan_file.is_file():
            out["plan"] = _read(plan_file)
            if out["plan"].get("network_snapshot_id") != man.get("network_snapshot_id"):
                raise DataError("DATA_VERSION_MISMATCH", "계획과 manifest의 교통망 ID가 다릅니다.")
        if date == "mean":
            if not light:
                self._mean_outcomes(base, man, fleet, variant, plan, period, out)
            return out
        index = base / entry["runs"][period] if entry.get("runs", {}).get(period) else None
        if not index or not index.is_file():
            return out
        label = entry.get("run_label", plan)
        rec = next((r for r in _read_index(index).get("runs", []) if r.get("label") == label and r.get("fleet") == fleet
                    and str(r.get("date")) == date.replace("-", "")), None)
        if rec is None:
            return out
        # 실행 기록의 dir은 만든 PC의 절대 경로라 쓰지 않고, 색인 폴더 안에서 찾는다.
        run_dir = index.parent / label / (rec.get("bundle") or f"{variant}_{date.replace('-', '')}") / f"f{fleet}"
        out["run_record"] = rec
        for key, file in (("metrics", run_dir / "run" / "metrics.json"), ("verifier", run_dir / "verifier_report.json"),
                          ("plan_m05", run_dir / "plan_m05.json")):
            if file.is_file():
                out[key] = _read(file)
        outcomes = run_dir / "run" / "passenger_outcomes.csv"
        if outcomes.is_file() and not light:      # 5일 평균 계산에는 승객 결과를 읽지 않는다
            out["hourly"] = _hourly(outcomes)
            out["spatial_outcomes"] = _spatial_outcomes(outcomes)
        return out

    def _mean_outcomes(self, base: Path, man: dict, fleet: int, variant: str, plan: str, period: str,
                       out: dict) -> None:
        """평일(월~금) 하루 평균: 승객별 결과가 있는 날짜마다 정류장·시간대별로 세어 날짜 수로 나눈다."""
        days = []
        for date in PERIODS[period]:
            art = self._catalog_artifacts(base, man, fleet, variant, plan, period, date)
            rec = art.get("run_record", {})
            if "spatial_outcomes" in art and rec.get("sim_rc") in (0, None) and rec.get("stage_failed") is None:
                days.append((date, art))
        if not days:
            return
        n = len(days)
        fields = ("total", "served", "unserved", "censored")
        spatial: dict[str, dict] = {}
        for _, art in days:
            for sid, c in art["spatial_outcomes"].items():
                e = spatial.setdefault(sid, {**{k: 0.0 for k in fields}, "hours": {}, "count_sum": 0})
                e["count_sum"] += c["total"]
                for k in fields:
                    e[k] += c[k] / n
                for h, hc in c.get("hours", {}).items():
                    he = e["hours"].setdefault(h, {**{k: 0.0 for k in fields}, "count_sum": 0})
                    he["count_sum"] += hc["total"]
                    for k in fields:
                        he[k] += hc[k] / n
        hourly: dict[int, dict] = {}
        for _, art in days:
            for h in art.get("hourly") or []:
                e = hourly.setdefault(h["hour"], {k: 0.0 for k in fields})
                for k in fields:
                    e[k] += h[k] / n
        out["spatial_outcomes"] = spatial
        out["hourly"] = [{"hour": h, **{k: round(v, 2) for k, v in c.items()},
                          "rate_percent": 100 * c["served"] / c["total"] if c["total"] and not c["censored"] else None}
                         for h, c in sorted(hourly.items())]
        out["mean_days"] = [d for d, _ in days]

    def _runs_index(self, region: str, fleet: int, variant: str, plan: str, period: str) -> tuple[Path | None, str]:
        """(실행 색인 경로, 기록의 label)."""
        man = self._catalog(region)
        if man:
            entry = next((p for p in man.get("plans", []) if p.get("id") == plan and p.get("fleet") == fleet
                          and p.get("variant") == variant), None)
            if entry and entry.get("runs", {}).get(period):
                return self.results_root / region / entry["runs"][period], entry.get("run_label", plan)
            return None, plan
        if not self.results_root:
            return None, plan
        return self.results_root / region / ("runs" if period == "exp" else "runs_test") / variant / "runs.json", plan

    def runs(self, q: dict) -> dict:
        """Return B's recorded attempts for the selected conditions, without synthetic runs."""
        region, fleet, variant, plan, period, _ = self._selection(q)
        index, label = self._runs_index(region, fleet, variant, plan, period)
        records = _read_index(index).get("runs", []) if index and index.is_file() else []
        rows = []
        for rec in records:
            if rec.get("label") != label or rec.get("fleet") != fleet:
                continue
            raw_date = str(rec.get("date", ""))
            if len(raw_date) != 8 or not raw_date.isdigit():
                continue
            date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
            if date not in PERIODS[period]:
                continue
            result = self.selection(
                {
                    "region": [region],
                    "fleet": [str(fleet)],
                    "variant": [variant],
                    "plan": [plan],
                    "period": [period],
                    "date": [date],
                }
            )
            rows.append(
                {
                    "date": date,
                    "status": result["outcome"]["status"],
                    "reason": result["outcome"].get("reason"),
                    "input_bundle_id": rec.get("input_bundle_id"),
                    "plan_id": result["plan_id"],
                    "revision": result["revision"],
                    "network_snapshot_id": result["network_snapshot_id"],
                    "conversion_rc": rec.get("conversion_rc"),
                    "sim_rc": rec.get("sim_rc"),
                    "verify_rc": rec.get("verify_rc"),
                    "stage_failed": rec.get("stage_failed"),
                }
            )
        return {
            "selection": {
                "region": region,
                "fleet": fleet,
                "variant": variant,
                "plan": plan,
                "period": period,
            },
            "source": "runs.json" if index and index.is_file() else "unavailable",
            "runs": rows,
            "run_id_available": False,
        }

    def selection(self, q: dict) -> dict:
        region, fleet, variant, plan, period, date = key = self._selection(q)
        art = self._artifacts(*key)
        mean = self._mean_row(region, fleet, variant, plan, period) if date == "mean" else None
        row = dict(mean) if mean else None
        mean_reasons = row.pop("reasons", None) if row else None
        record = art.get("run_record", {})
        failed = record.get("stage_failed") in {"conversion", "simulation"} or (
            record.get("conversion_rc") not in (None, 0)
            or record.get("sim_rc") not in (None, 0)
        )
        metrics = None if failed else art.get("metrics")
        if metrics and isinstance(metrics.get("M1"), dict):
            m1 = metrics["M1"]
            rate = (
                m1.get("rate")
                if "rate" in m1
                else (m1["served"] / m1["total"] if m1.get("total") else None)
            )
            row = {
                "total": m1.get("total"),
                "served": m1.get("served"),
                "unserved": m1.get("unserved"),
                "rate_percent": 100 * rate if rate is not None else None,
                "censored": m1.get("censored"),
                "source": "M05_metrics_day",
                "route_count": len(art.get("plan", {}).get("routes", [])),
            }
        if row:
            if row.get("unserved") is None:
                row["unserved"] = round(row["total"] - row["served"], 2)
            row["fleet"] = fleet
        reasons = None
        if metrics:
            m2 = metrics.get("M2", {})
            if m2.get("unserved_by_reason") is not None:
                reasons = {
                    **m2["unserved_by_reason"],
                    "refusal_events": m2.get("refusal_events"),
                }
        if reasons is None and date == "mean" and mean_reasons is not None:
            reasons = mean_reasons
        if (
            reasons is None
            and period == "exp"
            and date == "mean"
            and variant == "affected"
        ):
            reasons = self.reasons.get((region, fleet, plan))
        verifier = None if failed else art.get("verifier")
        if verifier:
            verification = {
                k: verifier.get(k)
                for k in (
                    "pass",
                    "fully_verified",
                    "failed_checks",
                    "not_run_checks",
                    "not_verified_checks",
                    "generated_at",
                    "summary",
                )
            }
            verification["source"] = "verifier_report.json"
        elif date == "mean" and row and row.get("source") == "M05_5day_mean":
            verification = {
                "pass": row["m07_pass_days"] == row["days"],
                "fully_verified": False,
                "failed_checks": [],
                "not_run_checks": [],
                "not_verified_checks": ["V12", "V15", "V16"],
                "source": "verifier_report.json 5일 집계",
                "note": f"{row['days']}일 중 {row['m07_pass_days']}일 M07 통과",
            }
        else:
            verification = {
                "pass": None,
                "fully_verified": False,
                "failed_checks": [],
                "not_run_checks": [],
                "not_verified_checks": ["V12", "V15", "V16"],
                "source": "report_v4_aggregate" if date == "mean" else "unavailable",
                "note": "보고서에는 640/640 실행 통과. 선택 실행의 검증 파일은 미연결.",
            }
        plan_doc = art.get("plan", {})
        if date == "mean":
            outcome = {
                "status": "report_only",
                "reason": "five_day_mean_from_runs" if row and row.get("source") == "M05_5day_mean"
                else "published_five_day_mean",
                "error": None,
            }
        elif failed:
            outcome = {
                "status": "calculation_failed",
                "reason": record.get("reason")
                or record.get("stage_failed")
                or "B run failed",
                "error": {
                    "code": "JOB_FAILED",
                    "message": record.get("reason") or "B run failed",
                    "details": {
                        "stage_failed": record.get("stage_failed"),
                        "conversion_rc": record.get("conversion_rc"),
                        "sim_rc": record.get("sim_rc"),
                        "verify_rc": record.get("verify_rc"),
                    },
                    "request_id": None,
                },
            }
        elif not record or not metrics or not isinstance(metrics.get("M1"), dict):
            outcome = {
                "status": "data_incomplete",
                "reason": "runs.json or M05 metrics missing",
                "error": None,
            }
        elif not verifier and record.get("verify_rc") not in (None, 0):
            outcome = {
                "status": "verification_failed",
                "reason": record.get("reason") or "M07 failed",
                "error": None,
            }
        elif not verifier:
            outcome = {
                "status": "verification_unknown",
                "reason": "verifier_report.json missing",
                "error": None,
            }
        elif verifier.get("pass") is True:
            outcome = {"status": "passed", "reason": None, "error": None}
        elif verifier.get("pass") is False or record.get("verify_rc") not in (None, 0):
            outcome = {
                "status": "verification_failed",
                "reason": "M07 failed",
                "error": None,
            }
        else:
            outcome = {
                "status": "verification_unknown",
                "reason": "M07 pass field missing",
                "error": None,
            }
        for field in ("plan_id", "revision", "network_snapshot_id"):
            if field in q:
                actual = (
                    plan_doc.get("plan_id", art.get("selection", {}).get("plan_id"))
                    if field == "plan_id"
                    else plan_doc.get("revision")
                    if field == "revision"
                    else plan_doc.get(
                        "network_snapshot_id",
                        art.get("manifest", {}).get("network_snapshot_id"),
                    )
                )
                if actual is None:
                    raise DataError("DATA_INCOMPLETE", f"{field} is unavailable")
                if str(q[field][0]) != str(actual):
                    raise DataError(
                        "DATA_VERSION_MISMATCH",
                        f"{field} differs from the result",
                        expected=q[field][0],
                        actual=actual,
                    )
        candidates = {
            c["route_id"]: c for c in art.get("candidates", {}).get("candidates", [])
        }
        allocated = art.get("selection", {}).get("routes", {})
        stops = self._stops(region, art.get("manifest", {}))
        spatial = []
        if not failed and outcome["status"] in {
            "passed",
            "verification_unknown",
            "verification_failed",
            "report_only",
        }:
            for sid, counts in art.get("spatial_outcomes", {}).items():
                stop = stops.get(sid)
                if stop and counts.get("count_sum", counts["total"]) >= 5:
                    spatial.append(
                        {
                            "type": "Feature",
                            "geometry": {
                                "type": "Point",
                                "coordinates": [stop["lon"], stop["lat"]],
                            },
                            "properties": {
                                "stop_id": sid,
                                "name": stop["name"],
                                **{k: round(v, 2) for k, v in counts.items() if k not in ("hours", "count_sum")},
                                "hours": {
                                    hour: {k: round(v, 2) for k, v in values.items() if k != "count_sum"}
                                    for hour, values in counts.get("hours", {}).items()
                                    if values.get("count_sum", values["total"]) >= 5
                                },
                                "passenger_count": counts["unserved"],
                                "demand_label": "M05 미충족",
                            },
                        }
                    )
        routes = []
        features = []
        for entry in plan_doc.get("routes", []):
            info = allocated.get(entry["route_id"], {})
            candidate = candidates.get(info.get("candidate"), {})
            stop_ids = entry.get("stops", [])
            route = {
                "route_id": entry["route_id"],
                "candidate_id": info.get("candidate"),
                "vehicles": entry.get("vehicles"),
                "stop_ids": stop_ids,
                "kind": info.get("kind", candidate.get("kind")),
                "origin": info.get("origin", candidate.get("origin")),
                "stops": [{"id": sid, **stops.get(sid, {})} for sid in stop_ids],
                "geometry_status": "stop_points_only_unverified_road",
            }
            routes.append(route)
            for order, sid in enumerate(stop_ids, 1):
                stop = stops.get(sid)
                if stop:
                    features.append(
                        {
                            "type": "Feature",
                            "geometry": {
                                "type": "Point",
                                "coordinates": [stop["lon"], stop["lat"]],
                            },
                            "properties": {
                                "route_id": entry["route_id"],
                                "stop_id": sid,
                                "name": stop["name"],
                                "order": order,
                                "vehicles": entry.get("vehicles"),
                            },
                        }
                    )
        layers = self._layers(region)
        road = ((layers or {}).get("route_geometry") or {}).get(plan) or {}
        station_of = {}
        for st in (layers or {}).get("stations", []):
            for sid in (st.get("access"), st.get("egress")):
                if sid:
                    station_of[sid] = st.get("name")
        route_lines = []
        for route in routes:
            for s_ in route["stops"]:
                if s_["id"] in station_of:
                    s_["station"] = station_of[s_["id"]]
            g = road.get(route["route_id"])
            if not g:
                continue
            route["geometry_status"] = "bus_road_network" if not g.get("gaps") else "bus_road_network_partial"
            route["road_gaps"] = g.get("gaps", 0)
            for line in g.get("lines", []):
                route_lines.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": line},
                                    "properties": {"route_id": route["route_id"], "part": "service", "vehicles": route.get("vehicles")}})
            if g.get("closing"):
                route_lines.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": g["closing"]},
                                    "properties": {"route_id": route["route_id"], "part": "closing", "vehicles": route.get("vehicles")}})
        info = self._plan_info(region, plan)
        base_id = self._baseline(region, plan)
        base_info = self._plan_info(region, base_id) if base_id else {}
        baseline_lines = []
        if base_id and base_info.get("baseline"):
            for rid, g in (((layers or {}).get("route_geometry") or {}).get(base_id) or {}).items():
                for line in g.get("lines", []) + ([g["closing"]] if g.get("closing") else []):
                    baseline_lines.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": line},
                                           "properties": {"route_id": rid, "plan": base_id}})
        return {
            "baseline_lines": {"type": "FeatureCollection", "features": baseline_lines,
                               "plan": base_id if baseline_lines else None,
                               "plan_name": (base_info.get("name") or base_id) if baseline_lines else None},
            "mean_days": art.get("mean_days"),
            "route_lines": {"type": "FeatureCollection", "features": route_lines,
                            "source": "routegen lab segpaths + 버스 도로 최단경로(bus_only)" if route_lines else None},
            "selection": {
                "region": region,
                "fleet": fleet,
                "variant": variant,
                "plan": plan,
                "period": period,
                "date": date,
            },
            "plan_name": info.get("name") or plan,
            "method": info.get("method"),
            "metrics": row,
            "reasons": reasons,
            "hourly": None if failed else art.get("hourly"),
            "verification": verification,
            "outcome": outcome,
            "execution": {
                key: art.get("run_record", {}).get(key)
                for key in (
                    "conversion_rc",
                    "sim_rc",
                    "verify_rc",
                    "stage_failed",
                    "input_bundle_id",
                    "reason",
                    "seconds",
                    "engine_env",
                    "policy",
                    "provenance",
                    "verification_rerun",
                )
            },
            "run_id": record.get("run_id"),
            "candidates": [
                {key: c.get(key) for key in ("route_id", "kind", "origin", "stops")}
                for c in candidates.values()
            ],
            "routes": routes,
            "geojson": {"type": "FeatureCollection", "features": features},
            "spatial_outcomes": {
                "status": "available" if spatial else "unavailable",
                "source": "M05 passenger_outcomes.csv origin aggregation",
                "features": spatial,
                "minimum_origin_count": 5,
            },
            "network_snapshot_id": plan_doc.get(
                "network_snapshot_id",
                art.get("manifest", {}).get("network_snapshot_id"),
            ),
            "plan_id": plan_doc.get("plan_id", art.get("selection", {}).get("plan_id")),
            "revision": plan_doc.get("revision"),
            "scenario_id": art.get("plan_m05", {}).get("scenario_id"),
            "map_status": ("버스 통행 도로 위 경로(routegen 도로망) · 순환 닫는 구간 포함"
                           if route_lines else "정류장 좌표만 표시 · 도로 선형 미검증")
            if features
            else "C팀 정류장 좌표·노선 원본 미연결",
            "demand_map_status": "OD·정류장별 M05 수송 결과 미연결",
        }

    def _stops(self, region: str, manifest: dict) -> dict:
        paths = []
        if self.inputs_root:
            paths.append(
                self.inputs_root / f"{region}_0610_v4" / "network" / "stops.csv"
            )
            paths.append(self.inputs_root / region / "network" / "stops.csv")
        if manifest.get("root"):
            paths.append(Path(manifest["root"]) / "network" / "stops.csv")
        net_file = (self.results_root / region / manifest["network_file"]
                    if manifest.get("network_file") and self.results_root else None)
        for path in paths:
            if path.is_file():
                tt = net_file if net_file and net_file.is_file() else path.parent / "travel_times.json"
                if not tt.is_file() or _read(tt).get(
                    "network_snapshot_id"
                ) != manifest.get("network_snapshot_id"):
                    continue
                with path.open(encoding="utf-8", newline="") as stream:
                    return {
                        r["stop_id"]: {
                            "name": r["stop_name"],
                            "lat": float(r["stop_lat"]),
                            "lon": float(r["stop_lon"]),
                        }
                        for r in csv.DictReader(stream)
                    }
        return {}

    def comparison(self, q: dict) -> dict:
        region, fleet, variant, plan, period, _ = self._selection(q)

        def item(r, f, p, t):
            row = self._mean_row(r, f, variant, p, t)
            if row is None:
                return None
            row = {k: v for k, v in row.items() if k != "reasons"}
            return {"region": r, "fleet": f, "plan": p, "plan_name": self._plan_info(r, p).get("name"),
                    "period": t, **row}

        regions = []
        for info in self._regions():
            r = info["id"]
            if variant not in info["variants"] or not info["fleets"]:
                continue
            ids = [x["id"] for x in info["plans"]]
            p = plan if plan in ids else next((x["id"] for x in info["plans"] if not x.get("baseline")), ids[0])
            got = item(r, fleet if r == region and fleet in info["fleets"] else info["fleets"][0], p, period)
            if got:
                regions.append(got)
        info = self._region(region)
        return {
            "scope": "같은 시나리오의 기간별 5일 평균; 지역·차량 대수별 수요 분모는 서로 다름",
            "regions": regions,
            "fleets": [x for x in (item(region, f, plan, period) for f in info["fleets"]) if x],
            "plans": [x for x in (item(region, fleet, p["id"], period) for p in info["plans"]) if x],
            "periods": [x for x in (item(region, fleet, plan, t) for t in PERIODS) if x],
        }

    def comparison_table(self, q: dict) -> dict:
        region, fleet, variant, _, period, date = self._selection(q)
        rows = []
        for p in self._region(region)["plans"]:
            params = {
                "region": [region],
                "fleet": [str(fleet)],
                "variant": [variant],
                "plan": [p["id"]],
                "period": [period],
                "date": [date],
            }
            item = self.selection(params)
            if item["metrics"] is None and item["plan_id"] is None:
                continue                        # 이 대수·변형에서 만들지 않은 안(예: 지자체안은 실운행 대수만)
            rows.append({"plan": p["id"], "baseline": bool(p.get("baseline")), **item})
        mets = [r["metrics"] for r in rows]
        same_total = len(rows) > 1 and all(m is not None for m in mets) and len({m["total"] for m in mets}) == 1
        same_input = date == "mean" or (
            len(rows) > 1
            and all(r["execution"].get("input_bundle_id") for r in rows)
            and len({r["execution"]["input_bundle_id"] for r in rows}) == 1
            and all(r.get("scenario_id") for r in rows)
            and len({r["scenario_id"] for r in rows}) == 1
            and all(r.get("network_snapshot_id") for r in rows)
            and len({r["network_snapshot_id"] for r in rows}) == 1
        )
        comparable = bool(same_total and same_input)
        sources = {r["metrics"]["source"] for r in rows if r["metrics"]}
        return {
            "selection": {
                "region": region,
                "fleet": fleet,
                "variant": variant,
                "period": period,
                "date": date,
            },
            "comparable": comparable,
            "reason": "같은 지역·날짜·차량·시나리오·잔여 수요"
            if comparable
            else "공통 입력 묶음·시나리오·교통망 또는 평가 분모를 확인할 수 없음",
            "source": sources.pop() if len(sources) == 1 else ("M05_metrics_day" if date != "mean" else "mixed"),
            "rows": rows,
        }

    def seoul_summary(self, q: dict, selected: dict | None = None) -> dict:
        _, _, variant, plan, period, date = self._selection(q)
        infos = {x["name"]: x for x in self._regions()}
        districts = []
        for name in SEOUL_DISTRICTS:
            info = infos.get(name)
            if info is None or variant not in info["variants"] or not info["fleets"]:
                districts.append({"name": name, "region": info["id"] if info else None, "status": "not_run"})
                continue
            region, fleet = info["id"], info["fleets"][0]
            ids = [x["id"] for x in info["plans"]]
            p = plan if plan in ids else next((x["id"] for x in info["plans"] if not x.get("baseline")), ids[0])
            base = self._baseline(region, p)
            params = {
                "region": [region],
                "fleet": [str(fleet)],
                "variant": [variant],
                "plan": [p],
                "period": [period],
                "date": [date],
            }
            result = self.selection(params)
            row = result["metrics"]
            other = None
            if base:
                params["plan"] = [base]
                other = self.selection(params)["metrics"]
            if date == "mean":
                status = "report_only" if row else "not_run"
                verification = result["verification"].get("note") or "640회 집계 통과·V12/V15/V16 미검증"
            else:
                status = "run" if row else result["outcome"]["status"]
                verification = result["verification"]
            districts.append(
                {
                    "name": name,
                    "region": region,
                    "status": status,
                    "fleet": fleet,
                    "fleet_assumed": False,
                    "plan": p,
                    "baseline_plan": base,
                    "metrics": row,
                    "improvement_pp": round(
                        row["rate_percent"] - other["rate_percent"], 2
                    )
                    if row
                    and other
                    and row["rate_percent"] is not None
                    and other["rate_percent"] is not None
                    else None,
                    "selected": bool(selected and selected.get("region") == region),
                    "verification": verification,
                }
            )
        rows = [
            d["metrics"]
            for d in districts
            if d.get("metrics") and d["metrics"].get("rate_percent") is not None
        ]
        total = sum(r["total"] for r in rows)
        served = sum(r["served"] for r in rows)
        return {
            "districts": districts,
            "aggregate": {
                "region_count": len(rows),
                "total": round(total, 2),
                "served": round(served, 2),
                "rate_percent": round(100 * served / total, 2) if total else None,
                "scope": (
                    "5일 평균" if date == "mean" else f"M05 일별 원본 {date}"
                )
                + " · 실행된 구만 합산 · 구별 첫 차량 조건",
            },
        }


class SelectionStore:
    """Local simulation selection pointer, separate from real-world approval."""

    def __init__(self, path: Path | None = None):
        self.path = path or ROOT / "data" / "d_selection.json"
        self.lock = Lock()

    def read(self) -> dict:
        with self.lock:
            try:
                return _read(self.path)
            except FileNotFoundError:
                return {"selected": None, "history": []}

    def choose(self, simulation: SimulationStore, request: dict) -> dict:
        if not isinstance(request, dict):
            raise DataError("INPUT_INVALID", "선택 요청은 JSON 객체여야 합니다.")
        params = {
            key: [str(request[key])]
            for key in ("region", "fleet", "variant", "plan", "period", "date")
            if key in request
        }
        if len(params) != 6:
            raise DataError(
                "INPUT_INVALID", "지역·차량·시나리오·안·기간·날짜가 필요합니다."
            )
        result = simulation.selection(params)
        if not result["plan_id"] or result["revision"] is None or not result["metrics"]:
            raise DataError(
                "DATA_INCOMPLETE",
                "원본 계획과 M05 날짜별 결과가 없어 선택할 수 없습니다.",
            )
        if result.get("outcome", {}).get("status") not in (None, "passed"):
            raise DataError(
                "PLAN_INFEASIBLE",
                "M05/M07 success is required for selection",
                outcome=result["outcome"],
            )
        if result["selection"]["date"] == "mean":
            raise DataError("INPUT_INVALID", "선택에는 개별 평가 날짜가 필요합니다.")
        verifier = result["verification"]
        if verifier["source"] != "verifier_report.json" or verifier["pass"] is not True:
            raise DataError(
                "PLAN_INFEASIBLE", "M07 통과 원본이 없어 선택할 수 없습니다."
            )
        if verifier.get("failed_checks") or verifier.get("not_run_checks"):
            raise DataError(
                "PLAN_INFEASIBLE",
                "실패하거나 미실행된 검사가 있는 안은 선택할 수 없습니다.",
            )
        if result["execution"].get("sim_rc") != 0:
            raise DataError("PLAN_INFEASIBLE", "M05 실행 성공을 확인할 수 없습니다.")
        for key in ("plan_id", "revision", "network_snapshot_id"):
            if request.get(key) != result[key]:
                raise DataError(
                    "DATA_VERSION_MISMATCH", f"{key}가 현재 산출물과 다릅니다."
                )
        chosen = {
            **result["selection"],
            "plan_id": result["plan_id"],
            "revision": result["revision"],
            "network_snapshot_id": result["network_snapshot_id"],
            "scenario_id": result.get("scenario_id"),
            "input_bundle_id": result["execution"].get("input_bundle_id"),
            "verification": {
                "pass": True,
                "fully_verified": verifier.get("fully_verified"),
                "not_verified_checks": verifier.get("not_verified_checks"),
            },
            "status": "selected",
            "approved_for_service": False,
            "selected_at": datetime.now(timezone.utc).isoformat(),
        }
        with self.lock:
            try:
                prior = _read(self.path)
            except FileNotFoundError:
                prior = {"selected": None, "history": []}
            history = prior.get("history", [])
            history.append(chosen)
            payload = {**prior, "selected": chosen, "history": history}   # 파레토 확정(pareto_selected) 등 다른 키는 그대로
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        return payload

    def record_pareto(self, entry: dict) -> dict:
        """충족률·형평성 곡선에서 고른 안의 확정(d_confirm)을 같은 기록에 남긴다.
        기존 v4 선택(selected)은 건드리지 않고 pareto_selected와 history(kind=pareto_confirm)에 더한다."""
        rec = {"kind": "pareto_confirm", **entry, "approved_for_service": False}
        with self.lock:
            try:
                prior = _read(self.path)
            except FileNotFoundError:
                prior = {"selected": None, "history": []}
            history = prior.get("history", [])
            history.append(rec)
            payload = {**prior, "pareto_selected": rec, "history": history}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload
