"""Read the C team's dated strike modeling registry without treating it as live status."""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

from .d_geo import MapSnapshot

REGIONS = ("guro", "dongjak", "gangseo", "gangdong")


def strike_registry_view(root: Path | None, snapshot: MapSnapshot, district: str = "all") -> dict:
    archive = root / "bundles_v4" / "region_bundles_0610_v4.zip" if root else None
    if not archive or not archive.is_file():
        return {"status": "unavailable", "reason": "C strike registry bundle is missing", "routes": []}
    regions = REGIONS if district == "all" else (district,) if district in REGIONS else ()
    by_id = {route["route_id"]: route for route in snapshot.routes}
    rows = []
    with zipfile.ZipFile(archive) as bundle:
        for region in regions:
            member = f"{region}_0610_v4/registry/strike_route_registry.csv"
            if member not in bundle.namelist():
                continue
            for row in csv.DictReader(io.StringIO(bundle.read(member).decode("utf-8-sig"))):
                if row.get("strike_state") != "affected":
                    continue
                route = by_id.get(row["route_id"])
                rows.append({"region": region, "route_id": row["route_id"],
                             "route_name": row["route_name"], "operator_id": row.get("operator_id"),
                             "operator_name": row.get("operator_name"),
                             "valid_from": row.get("valid_from"), "valid_to": row.get("valid_to"),
                             "registry_match_status": row.get("match_status"),
                             "id_name_match": bool(route and route["short_name"] == row["route_name"]),
                             "operator_verified": False, "direction_verified": False,
                             "geometry_status": route.get("geometry_status") if route else None})
    return {"status": "historical_model_scenario", "source": str(archive),
            "matched_id_name": sum(row["id_name_match"] for row in rows),
            "unmatched_id_name": sum(not row["id_name_match"] for row in rows),
            "routes": rows}
