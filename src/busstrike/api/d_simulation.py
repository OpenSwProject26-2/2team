"""Read-only adapter for the published routegen v4 report and optional run artifacts."""

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
PERIODS = {
    "exp": [f"2026-10-{d:02d}" for d in range(26, 31)],
    "test": [f"2026-11-{d:02d}" for d in range(2, 7)],
}


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


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
        self, results_root: Path | None = None, inputs_root: Path | None = None
    ):
        self.results_root = results_root.resolve() if results_root else None
        self.inputs_root = inputs_root.resolve() if inputs_root else None
        self.report = report_rows()
        self.reasons = reason_rows()

    def options(self) -> dict:
        return {
            "regions": [
                {"id": k, "name": v[0], "fleets": v[1], "center": v[2]}
                for k, v in REGIONS.items()
            ],
            "variants": list(VARIANTS),
            "plans": ["ours", "sp"],
            "periods": PERIODS,
            "report_source": "experiments/reports/routegen_v4.md §3, §6 (PR #41)",
            "artifacts_connected": {k: self._manifest(k).exists() for k in REGIONS},
        }

    def _manifest(self, region: str) -> Path:
        return (
            (self.results_root / region / "manifest.json")
            if self.results_root
            else Path("__unconnected__")
        )

    def _selection(self, q: dict) -> tuple:
        region = q.get("region", ["guro"])[0]
        variant = q.get("variant", ["affected"])[0]
        plan = q.get("plan", ["ours"])[0]
        period = q.get("period", ["exp"])[0]
        date = q.get("date", ["mean"])[0]
        try:
            fleet = int(q.get("fleet", [str(REGIONS[region][1][0])])[0])
        except (KeyError, ValueError) as error:
            raise DataError(
                "INPUT_INVALID", "지역 또는 차량 대수가 올바르지 않습니다."
            ) from error
        if (
            region not in REGIONS
            or fleet not in REGIONS[region][1]
            or variant not in VARIANTS
            or plan not in {"ours", "sp"}
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

    def runs(self, q: dict) -> dict:
        """Return B's recorded attempts for the selected conditions, without synthetic runs."""
        region, fleet, variant, plan, period, _ = self._selection(q)
        index = (
            self.results_root
            / region
            / ("runs" if period == "exp" else "runs_test")
            / variant
            / "runs.json"
            if self.results_root
            else None
        )
        records = _read(index).get("runs", []) if index and index.is_file() else []
        rows = []
        for rec in records:
            if rec.get("label") != plan or rec.get("fleet") != fleet:
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
        row = (
            self.report[(region, fleet, variant, plan, period)].copy()
            if date == "mean"
            else None
        )
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
                "reason": "published_five_day_mean",
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
        }:
            for sid, counts in art.get("spatial_outcomes", {}).items():
                stop = stops.get(sid)
                if stop and counts["total"] >= 5:
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
                                **counts,
                                "hours": {
                                    hour: values
                                    for hour, values in counts.get("hours", {}).items()
                                    if values["total"] >= 5
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
        return {
            "selection": {
                "region": region,
                "fleet": fleet,
                "variant": variant,
                "plan": plan,
                "period": period,
                "date": date,
            },
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
            "map_status": "정류장 좌표만 표시 · 도로 선형 미검증"
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
        for path in paths:
            if path.is_file():
                tt = path.parent / "travel_times.json"
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
            return {
                "region": r,
                "fleet": f,
                "plan": p,
                "period": t,
                **self.report[(r, f, variant, p, t)],
            }

        return {
            "scope": "같은 시나리오의 기간별 5일 평균; 지역·차량 대수별 수요 분모는 서로 다름",
            "regions": [
                item(r, fleet if r == region else REGIONS[r][1][0], plan, period)
                for r in REGIONS
            ],
            "fleets": [item(region, f, plan, period) for f in REGIONS[region][1]],
            "plans": [item(region, fleet, p, period) for p in ("ours", "sp")],
            "periods": [item(region, fleet, plan, t) for t in PERIODS],
        }

    def comparison_table(self, q: dict) -> dict:
        region, fleet, variant, _, period, date = self._selection(q)
        rows = []
        for plan in ("ours", "sp"):
            params = {
                "region": [region],
                "fleet": [str(fleet)],
                "variant": [variant],
                "plan": [plan],
                "period": [period],
                "date": [date],
            }
            item = self.selection(params)
            rows.append(
                {
                    "plan": plan,
                    "method": "C routegen 노선·배분 최적화"
                    if plan == "ours"
                    else "C 최단경로 피더 기준안",
                    **item,
                }
            )
        a, b = rows
        same_total = (
            a["metrics"] is not None
            and b["metrics"] is not None
            and a["metrics"]["total"] == b["metrics"]["total"]
        )
        same_input = date == "mean" or (
            a["execution"].get("input_bundle_id")
            and a["execution"]["input_bundle_id"]
            == b["execution"].get("input_bundle_id")
            and a.get("scenario_id")
            and a["scenario_id"] == b.get("scenario_id")
            and a.get("network_snapshot_id")
            and a["network_snapshot_id"] == b.get("network_snapshot_id")
        )
        comparable = bool(same_total and same_input)
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
            "source": "report_v4_5day_mean" if date == "mean" else "M05_metrics_day",
            "rows": rows,
        }

    def seoul_summary(self, q: dict, selected: dict | None = None) -> dict:
        _, _, variant, plan, period, date = self._selection(q)
        districts = []
        for name in SEOUL_DISTRICTS:
            region = next((key for key, val in REGIONS.items() if val[0] == name), None)
            if region is None:
                districts.append({"name": name, "region": None, "status": "not_run"})
                continue
            fleet = REGIONS[region][1][0]
            if date == "mean":
                row = self.report[(region, fleet, variant, plan, period)]
                other = self.report[
                    (region, fleet, variant, "sp" if plan == "ours" else "ours", period)
                ]
                status = "report_only"
                verification = "640회 집계 통과·V12/V15/V16 미검증"
            else:
                params = {
                    "region": [region],
                    "fleet": [str(fleet)],
                    "variant": [variant],
                    "plan": [plan],
                    "period": [period],
                    "date": [date],
                }
                result = self.selection(params)
                row = result["metrics"]
                params["plan"] = ["sp" if plan == "ours" else "ours"]
                other = self.selection(params)["metrics"]
                status = "run" if row else result["outcome"]["status"]
                verification = result["verification"]
            districts.append(
                {
                    "name": name,
                    "region": region,
                    "status": status,
                    "fleet": fleet,
                    "fleet_assumed": False,
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
                    "보고서 5일 평균" if date == "mean" else f"M05 일별 원본 {date}"
                )
                + " · 실행된 구만 합산 · 구별 작은 차량 조건",
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
            payload = {"selected": chosen, "history": history}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        return payload
