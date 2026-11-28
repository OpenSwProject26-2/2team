"""충족률·형평성 곡선(파이프라인 pareto.json)과 안별 지도용 운행안(export/<plan id>.json)을 구별로 읽는다.

<pareto_root>/<구>/pareto.json, <pareto_root>/<구>/export/*.json (factorial_1126 같은 파이프라인 출력).
- 계산하지 않는다. pareto.json을 그대로 주고, 있으면 두 가지만 덧붙인다:
  - plans[].rep: demo/representative.json의 대표안 역할(탑승 우선·균형·형평 우선), plan_sha256으로 맞춤
  - muni.export_path: export/muni.json이 있으면(지자체안 점을 눌러 노선을 볼 수 있게)
- export는 pareto.json에 있는 안(과 지자체안)만 준다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REGION = re.compile(r"^[a-z_]+$")


class ParetoStore:
    def __init__(self, root: str | Path | None):
        self.root = Path(root).resolve() if root else None
        self._cache: dict[str, tuple[float, dict]] = {}

    def _dir(self, region: str) -> Path | None:
        if not self.root or not REGION.match(region or ""):
            return None
        d = self.root / region
        return d if (d / "pareto.json").is_file() else None

    def regions(self) -> list[str]:
        if not self.root or not self.root.is_dir():
            return []
        return sorted(p.name for p in self.root.iterdir() if REGION.match(p.name) and (p / "pareto.json").is_file())

    def pareto(self, region: str) -> dict:
        d = self._dir(region)
        if d is None:
            return {"status": "unavailable", "region": region}
        mtime = (d / "pareto.json").stat().st_mtime
        hit = self._cache.get(region)
        if hit and hit[0] == mtime:
            return hit[1]
        doc = json.loads((d / "pareto.json").read_text(encoding="utf-8"))
        rep_file = d / "demo" / "representative.json"
        if rep_file.is_file():
            roles: dict[str, list[str]] = {}
            for x in json.loads(rep_file.read_text(encoding="utf-8")).get("representative", []):
                if x.get("plan_sha256") and x.get("role"):
                    roles.setdefault(x["plan_sha256"], []).append(x["role"])
            for p in doc.get("plans", []):
                if p.get("plan_sha256") in roles:
                    p["rep"] = "·".join(roles[p["plan_sha256"]])
        if doc.get("muni") and not doc["muni"].get("export_path") and (d / "export" / "muni.json").is_file():
            doc["muni"]["export_path"] = "export/muni.json"
        out = {"status": "available", "region": region, "source": str(d), **doc}
        self._cache[region] = (mtime, out)
        return out

    def export(self, region: str, plan_id: str) -> dict:
        doc = self.pareto(region)
        if doc.get("status") != "available":
            return {"status": "unavailable", "region": region}
        paths = {p["id"]: p.get("export_path") for p in doc.get("plans", [])}
        if doc.get("muni"):
            paths[doc["muni"].get("id", "muni")] = doc["muni"].get("export_path")
        rel = paths.get(plan_id)
        if not rel:
            return {"status": "unavailable", "region": region, "plan_id": plan_id}
        base = (self.root / region).resolve()
        f = (base / rel).resolve()
        if base not in f.parents or not f.is_file():
            return {"status": "unavailable", "region": region, "plan_id": plan_id}
        return {"status": "available", **json.loads(f.read_text(encoding="utf-8"))}
