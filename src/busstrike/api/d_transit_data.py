"""Read-only adapter for the supplied transit dataset; never opens .keys or PC-only tables."""

from __future__ import annotations

import csv
import io
import json
import os
import zipfile
from collections import defaultdict
from pathlib import Path

from .d_geo import DataError, MapSnapshot
from .d_network import contains, districts


def discover_root() -> Path | None:
    candidates = [os.environ.get("TRANSIT_DATA_ROOT")]
    downloads = Path.home() / "Downloads"
    candidates += [
        downloads / "seoul_transit_data",
        downloads / "seoul_transit_data.Zip",
    ]
    return next((Path(p) for p in candidates if p and Path(p).exists()), None)


class TransitData:
    def __init__(self, root: Path | None = None):
        self.root = root or discover_root()
        self._zip = None
        if self.root and self.root.is_file():
            self._zip = zipfile.ZipFile(self.root)
        self.manifest = self._json("manifest.json") if self.root else None
        self._names = set(self._zip.namelist()) if self._zip else None
        self._reference_cache = None
        self._observed_cache = {}
        self._region_bundle = None
        self._residual_cache = {}

    def _region_zip(self):
        """Open the released C/B input bundle without copying it into the repository."""
        name = "bundles_v4/region_bundles_0610_v4.zip"
        if not self._exists(name):
            return None
        if self._region_bundle is None:
            with self._open(name) as source:
                self._region_bundle = zipfile.ZipFile(io.BytesIO(source.read()))
        return self._region_bundle

    def residual(self, region: str, variant: str, period: str, date: str) -> dict:
        """Aggregate M05 *input* demand by origin; this is not unmet demand."""
        variants = {"affected", "affected_maeul", "affected_gg", "affected_maeul_gg"}
        regions = {"guro", "dongjak", "gangseo", "gangdong"}
        dates = {
            "exp": [f"2026-10-{day:02d}" for day in range(26, 31)],
            "test": [f"2026-11-{day:02d}" for day in range(2, 7)],
        }
        if region not in regions or variant not in variants or period not in dates:
            raise DataError("INPUT_INVALID", "지역·시나리오·기간 값이 잘못되었습니다.")
        if date != "mean" and date not in dates[period]:
            raise DataError("INPUT_INVALID", "선택 기간에 없는 날짜입니다.")
        key = region, variant, period, date
        if key in self._residual_cache:
            return self._residual_cache[key]
        bundle = self._region_zip()
        unavailable = {
            "status": "unavailable", "source": "C/B region bundle demand_bins.csv",
            "features": [], "total": None, "minimum_origin_count": 5,
        }
        if bundle is None:
            return unavailable
        prefix = f"{region}_0610_v4{'_test' if period == 'test' else ''}/"
        stop_file = prefix + "network/stops.csv"
        chosen_dates = dates[period] if date == "mean" else [date]
        paths = [prefix + f"bundles/{variant}_{day.replace('-', '')}/demand_bins.csv" for day in chosen_dates]
        if stop_file not in bundle.namelist() or any(path not in bundle.namelist() for path in paths):
            return unavailable
        with bundle.open(stop_file) as raw:
            stops = {
                row["stop_id"]: row
                for row in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
            }
        counts = defaultdict(lambda: defaultdict(int))
        total = 0
        unmatched_count = 0
        unmatched_passengers = 0
        for path in paths:
            with bundle.open(path) as raw:
                for row in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig")):
                    amount = int(row["passenger_count"])
                    if amount < 0:
                        raise DataError("DATA_INVALID", "잔여 수요 입력에 음수가 있습니다.")
                    origin = row["origin"]
                    if origin not in stops:
                        unmatched_count += 1
                        unmatched_passengers += amount
                        total += amount
                        continue
                    hour = f"{int(row['bin_start_s']) // 3600:02d}"
                    counts[origin][hour] += amount
                    total += amount
        divisor = len(chosen_dates)
        features = []
        suppressed_hour_cells = 0
        for stop_id, hours in counts.items():
            stop = stops[stop_id]
            lat, lon = float(stop["stop_lat"]), float(stop["stop_lon"])
            if not (33 <= lat <= 39 and 124 <= lon <= 132):
                continue
            visible_hours = {
                hour: round(value / divisor, 1)
                for hour, value in sorted(hours.items())
                if value / divisor >= 5
            }
            suppressed_hour_cells += len(hours) - len(visible_hours)
            stop_total = sum(visible_hours.values())
            if stop_total < 5:
                continue
            features.append({
                "type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "stop_id": stop_id, "name": stop["stop_name"],
                    "passenger_count": round(stop_total, 1),
                    "hours": visible_hours,
                    "source_namespace": "C_region_network", "gu": stop.get("gu"),
                },
            })
        result = {
            "status": "available", "source": "C/B region bundle demand_bins.csv",
            "demand_kind": "pre_shuttle_residual", "region": region,
            "variant": variant, "period": period, "date": date,
            "features": features, "total": round(total / divisor, 1),
            "minimum_origin_count": 5, "coordinate_source": "C region network/stops.csv",
            "scope": "C region bundle (including its network buffer)",
            "unmatched_rows": unmatched_count,
            "unmatched_passengers": round(unmatched_passengers / divisor, 1),
            "suppressed_hour_cells": suppressed_hour_cells,
        }
        if len(self._residual_cache) >= 32:
            self._residual_cache.clear()
        self._residual_cache[key] = result
        return result

    def _open(self, name: str):
        if name.startswith(".") or "/." in name or not self.root:
            raise DataError("INPUT_INVALID", "허용되지 않은 데이터 경로입니다.")
        if self._zip:
            return self._zip.open(name)
        return (self.root / name).open("rb")

    def _exists(self, name: str) -> bool:
        return bool(
            self.root
            and (name in self._names if self._zip else (self.root / name).is_file())
        )

    def _json(self, name: str) -> dict:
        with self._open(name) as stream:
            return json.load(stream)

    def _csv(self, name: str):
        with self._open(name) as raw:
            yield from csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))

    def reference(self) -> dict:
        if self._reference_cache is not None:
            return self._reference_cache
        if not self.root:
            raise DataError(
                "DATA_INCOMPLETE", "seoul_transit_data가 연결되지 않았습니다."
            )
        stops = {r["BSST_ID"]: r for r in self._csv("reference/stops.csv")}
        routes = {r["ROUTE_ID"]: r for r in self._csv("reference/routes.csv")}
        gu = {r["slug"]: r for r in self._csv("reference/gu_codes.csv")}
        self._reference_cache = {"stops": stops, "routes": routes, "gu": gu}
        return self._reference_cache

    def status(self, snapshot: MapSnapshot | None = None) -> dict:
        if not self.root:
            return {
                "status": "unavailable",
                "reason": "TRANSIT_DATA_ROOT 미설정·기본 위치 미발견",
            }
        ref = self.reference()
        a_stops = {s["stop_id"] for s in snapshot.stops} if snapshot else set()
        a_routes = {r["route_id"] for r in snapshot.routes} if snapshot else set()
        return {
            "status": "ready",
            "root_type": "zip" if self._zip else "directory",
            "created": self.manifest.get("created"),
            "observed_dates": sorted(self.manifest.get("days", {})),
            "reference_stops": len(ref["stops"]),
            "reference_routes": len(ref["routes"]),
            "a_stop_id_matches": len(a_stops & ref["stops"].keys()),
            "a_route_id_matches": len(a_routes & ref["routes"].keys()),
            "districts": len(ref["gu"]),
            "geometry_source": "none_in_dataset",
            "simulation_results": "not_in_bundles_v4",
        }

    def observed(self, district: str, date: str, hour: str) -> dict:
        key = (district, date, hour)
        if key in self._observed_cache:
            return self._observed_cache[key]
        ref = self.reference()
        if district not in ref["gu"]:
            raise DataError("INPUT_INVALID", "지원하지 않는 자치구입니다.")
        day = date.replace("-", "")
        if day not in self.manifest.get("days", {}) or hour not in {
            "all",
            *(f"{h:02d}" for h in range(24)),
        }:
            raise DataError("OUT_OF_SCOPE", "관측일 또는 시간대가 자료 범위 밖입니다.")
        name = f"share_k5/seoul/{day}/stop_route_hourly.csv"
        if not self._exists(name):
            raise DataError("DATA_INCOMPLETE", "해당 날짜의 서울 k5 집계가 없습니다.")
        polygon = districts()[district]["geometry"]
        inside = set()
        for sid, stop in ref["stops"].items():
            try:
                point = [float(stop["lon"]), float(stop["lat"])]
            except (ValueError, TypeError):
                continue
            if contains(point, polygon):
                inside.add(sid)
        totals = defaultdict(lambda: [0, 0])
        for row in self._csv(name):
            if row["H"].zfill(2) not in (
                {"06", "07", "08", "09"} if hour == "all" else {hour}
            ):
                continue
            sid = row["BSST_ID"]
            if sid not in inside:
                continue
            totals[sid][0] += int(row["BOARD"] or 0)
            totals[sid][1] += int(row["ALIGHT"] or 0)
        features = []
        for sid, (board, alight) in totals.items():
            stop = ref["stops"].get(sid)
            if not stop:
                continue
            try:
                lon, lat = float(stop["lon"]), float(stop["lat"])
            except (ValueError, TypeError):
                continue
            if not 125 <= lon <= 128 or not 36 <= lat <= 39:
                continue
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [lon, lat]},
                    "properties": {
                        "stop_id": sid,
                        "name": stop["BSST_NM"],
                        "board": board,
                        "alight": alight,
                        "passenger_count": board,
                    },
                }
            )
        result = {
            "status": "available",
            "source": "share_k5 stop_route_hourly aggregated by stop",
            "demand_kind": "observed_normal_boardings",
            "k": 5,
            "not_equivalent_to": ["strike_residual", "M05_unserved"],
            "date": day,
            "hour": hour,
            "district": district,
            "features": features,
            "totals": {
                "board": sum(f["properties"]["board"] for f in features),
                "alight": sum(f["properties"]["alight"] for f in features),
            },
        }
        if len(self._observed_cache) >= 32:
            self._observed_cache.clear()
        self._observed_cache[key] = result
        return result
