"""구별 실험 결과(hub_sweep2/<구>)에 운영자 대시보드가 읽는 목록 `manifest.json`(schema routegen-results/1)을 쓴다.

    python3 experiments/routegen_lab/write_results_manifest.py --sweep-root <hub_sweep2> --region-root <region_all> [--gu guro,...]

- 안: 생성 설정(base·hub135·hub135_s12) × 대수, 지자체안(muni, 그 대수에서만, 비교 기준 baseline=true).
- 파일 위치는 결과 폴더 기준 상대 경로. 실행 기록의 절대 경로(dir)는 쓰지 않는다.
- root = 구 묶음 폴더(정류장 이름·좌표), network_file = 노선안이 쓴 도로 망(망 ID 확인용).
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

SCHEMA = "routegen-results/1"
NAMES = {"base": ("우리 안 · 기본(허브 540·정차 8)", "C routegen: 허브 역 권역 노선 생성 + 배분 최적화(도로 망·정밀 흡수)"),
         "hub135": ("우리 안 · 허브 135", "C routegen: 허브 기준 135명/일로 낮춤"),
         "hub135_s12": ("우리 안 · 허브 135·정차 12", "C routegen: 허브 135명/일, 정차 상한 12"),
         "muni": ("지자체 셔틀안(2026-01 실운행)", "구청 노선도 전사 → 정류장 ID, 무정차 구간은 도로 최단 경로")}


def center(root: Path, gu: str) -> list[float]:
    pts = [(float(r["stop_lon"]), float(r["stop_lat"])) for r in csv.DictReader(open(root / "network" / "stops.csv", encoding="utf-8"))
           if r.get("gu") == gu]
    return [round(sum(p[0] for p in pts) / len(pts), 4), round(sum(p[1] for p in pts) / len(pts), 4)] if pts else None


def one(sw: Path, region_root: Path) -> dict | None:
    hs_path = sw / "hub_sweep.json"
    if not hs_path.exists():
        return None
    hs = json.loads(hs_path.read_text(encoding="utf-8"))
    slug = sw.name
    root = region_root / "ref" / f"{slug}_0610_v4"
    root = root if root.is_dir() else region_root / f"{slug}_0610_v4"
    plans = []
    snap = None
    for p in hs["plans"]:
        cfg = p.get("config")
        f = Path(p["file"])
        doc = json.loads(f.read_text(encoding="utf-8"))
        snap = snap or doc["network_snapshot_id"]
        rel = f.relative_to(sw)
        name, method = NAMES.get(cfg, (cfg, ""))
        plans.append({"id": cfg, "name": name, "method": method, "baseline": False, "fleet": p["fleet"],
                      "variant": "precise_affected_maeul_gg", "plan_id": doc["plan_id"], "plan_file": str(rel),
                      "run_label": p.get("label", "ours"),
                      "runs": {"exp": f"{cfg}/runs_exp/runs.json", "test": f"{cfg}/runs_test/runs.json"}})
    mj = sw / "muni" / "muni.json"
    if mj.exists():
        m = json.loads(mj.read_text(encoding="utf-8"))
        doc = json.loads((sw / "muni" / "plan.json").read_text(encoding="utf-8"))
        name, method = NAMES["muni"]
        plans.append({"id": "muni", "name": name, "method": method, "baseline": True, "fleet": m["fleet"],
                      "variant": "precise_affected_maeul_gg", "plan_id": doc["plan_id"], "plan_file": "muni/plan.json",
                      "run_label": "muni", "runs": {"exp": "muni/runs_exp/runs.json", "test": "muni/runs_test/runs.json"}})
    order = ["hub135_s12", "hub135", "base", "muni"]
    plans.sort(key=lambda x: (order.index(x["id"]) if x["id"] in order else 9, x["fleet"]))
    return {"schema": SCHEMA, "region": slug, "gu": hs["gu"], "center": center(root, hs["gu"]),
            "network_snapshot_id": snap, "root": str(root.resolve()), "network_file": "net/road.json",
            "fleets": hs["fleets"], "variants": ["precise_affected_maeul_gg"], "plans": plans,
            "absorbed": hs.get("absorbed"), "affected_total": hs.get("affected_total"),
            "absorbed_test": hs.get("absorbed_test"), "affected_total_test": hs.get("affected_total_test"),
            "notes": "도로 망(OSM 버스 도로) · 마을·경기 정밀 흡수 뒤 잔여 수요 · 지자체안은 1월 실운행 대수에서만"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sweep-root", required=True, type=Path)
    ap.add_argument("--region-root", required=True, type=Path)
    ap.add_argument("--gu", default="")
    args = ap.parse_args()
    only = set(filter(None, args.gu.split(",")))
    n = 0
    for sw in sorted(p for p in args.sweep_root.iterdir() if p.is_dir()):
        if only and sw.name not in only:
            continue
        man = one(sw, args.region_root)
        if man:
            (sw / "manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
            n += 1
            print(sw.name, len(man["plans"]), "plans")
    print(f"{n}개 구 manifest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
