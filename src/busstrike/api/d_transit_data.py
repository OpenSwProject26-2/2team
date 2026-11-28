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


WEEKDAY_MEAN = ("20261026", "20261027", "20261028", "20261029", "20261030")   # 관측 수요 기본값: 실험 주간 월~금 평균


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
        self._real_base_cache = {}
        self._real_district_cache = {}
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

    def real_demand(
        self, snapshot: MapSnapshot, district: str, date: str, hour: str,
        strike_route_ids: tuple[str, ...] = (),
        disabled_modes: tuple[str, ...] = (),
    ) -> dict:
        """Observed k5 board/alight and a *boarding exposure* for selected bus routes.

        Exposure is not an estimate of transfer, spare capacity, or unmet demand.
        Keeping both tables in this response prevents a strike switch from changing
        the normal-day observation and avoids charging a passenger twice.
        """
        if not self.root:
            raise DataError("DATA_INCOMPLETE", "TRANSIT_DATA_ROOT가 연결되지 않았습니다.")
        if snapshot.source == "synthetic":
            raise DataError("INPUT_INVALID", "실측 집계에는 A GTFS가 필요합니다.")
        if district != "all" and district not in districts():
            raise DataError("INPUT_INVALID", "지원하지 않는 자치구입니다.")
        day = date.replace("-", "")
        mean_days = [d for d in WEEKDAY_MEAN if d in self.manifest.get("days", {})] if day == "mean" else []
        if (day != "mean" and day not in self.manifest.get("days", {})) or (day == "mean" and not mean_days) \
                or hour not in {"all", "morning", "morning_0730_estimate", *(f"{h:02d}" for h in range(24))}:
            raise DataError("OUT_OF_SCOPE", "관측일 또는 시간대가 자료 범위 밖입니다.")
        route_kinds = {r["route_id"]: r["kind"] for r in snapshot.routes}
        disabled = set(disabled_modes)
        if disabled - {"city", "village", "gyeonggi", "metro"}:
            raise DataError("INPUT_INVALID", "지원하지 않는 운행 중단 교통수단입니다.")
        strike = set(strike_route_ids)
        invalid = strike - {rid for rid, kind in route_kinds.items() if kind in {"city", "village", "gyeonggi", "unknown_bus"}}
        if invalid:
            raise DataError("INPUT_INVALID", "파업 노선 ID가 A GTFS 버스 노선과 일치하지 않습니다.", invalid_route_ids=sorted(invalid))
        reference = self.reference()
        seoul_gu_names = {feature["properties"]["sggnm"] for feature in districts().values()}
        if strike - reference["routes"].keys():
            raise DataError("DATA_VERSION_MISMATCH", "파업 노선 ID가 관측 노선 목록과 일치하지 않습니다.")
        # Parse each date/hour once. Districts and transport switches only filter
        # the already mapped stop/route counts; rescanning the source CSV on every
        # district change adds seconds of avoidable latency.
        key = (snapshot.snapshot_id, day, hour)
        polygon = districts()[district]["geometry"] if district != "all" else None
        def in_area(point):
            return polygon is None or contains(point, polygon)
        allowed_hours = {"06", "07", "08", "09"} if hour == "all" else {"07", "08"} if hour in {"morning", "morning_0730_estimate"} else {hour}
        def weight(source_hour: str) -> float:
            return 0.5 if hour == "morning_0730_estimate" and source_hour == "07" else 1.0
        a_stops = {s["stop_id"]: s for s in snapshot.stops if s["kind"] == "metro"}
        stations = {r["OUT_STN_NUM"].zfill(4): r for r in self._csv("reference/subway_stations.csv")}
        def one_day(day: str):
            key = (snapshot.snapshot_id, day, hour)
            bus_file = f"share_k5/seoul/{day}/stop_route_hourly.csv"
            metro_file = f"share_k5/seoul/{day}/subway_od_hourly.csv"
            if not self._exists(bus_file) or not self._exists(metro_file):
                raise DataError("DATA_INCOMPLETE", "선택 날짜의 버스·지하철 k5 집계가 없습니다.")
            cached_base = self._real_base_cache.get(key)
            if cached_base is not None:
                return cached_base
            route_board = defaultdict(lambda: [0, 0])
            metro = defaultdict(lambda: [0, 0])
            bus_unmatched = [0, 0]
            outside_a = [0, 0]
            unmatched = {"origin_rows": 0, "origin_passengers": 0, "destination_rows": 0, "destination_passengers": 0}
            for row in self._csv(bus_file):
                if row["H"].zfill(2) not in allowed_hours:
                    continue
                rid, sid = row["ROUTE_ID"], row["BSST_ID"]
                if rid not in route_kinds:
                    outside_a[0] += 1
                    outside_a[1] += int(row["BOARD"] or 0) * weight(row["H"].zfill(2))
                    continue
                stop = reference["stops"].get(sid)
                board, alight = int(row["BOARD"] or 0), int(row["ALIGHT"] or 0)
                if not stop:
                    bus_unmatched[0] += 1
                    bus_unmatched[1] += board * weight(row["H"].zfill(2))
                    continue
                try:
                    float(stop["lon"]), float(stop["lat"])
                except (ValueError, TypeError):
                    bus_unmatched[0] += 1
                    bus_unmatched[1] += board * weight(row["H"].zfill(2))
                    continue
                item = route_board[(rid, sid)]
                item[0] += board * weight(row["H"].zfill(2))
                item[1] += alight * weight(row["H"].zfill(2))
            for row in self._csv(metro_file):
                if row["RIDE_H"].zfill(2) not in allowed_hours:
                    continue
                count = int(row["PSNG"] or 0)
                for field, index, side in (("RIDE_STN_ID", 0, "origin"), ("ALGH_STN_ID", 1, "destination")):
                    raw = row[field]
                    sid = raw.zfill(4) if raw.isdigit() else raw
                    if sid not in stations or sid not in a_stops:
                        unmatched[f"{side}_rows"] += 1
                        unmatched[f"{side}_passengers"] += count * weight(row["RIDE_H"].zfill(2))
                        continue
                    metro[sid][index] += count * weight(row["RIDE_H"].zfill(2))
            if len(self._real_base_cache) >= 32:
                self._real_base_cache.clear()
            self._real_base_cache[key] = (dict(route_board), dict(metro), list(bus_unmatched), list(outside_a), dict(unmatched))
            return self._real_base_cache[key]
        mean_key = (snapshot.snapshot_id, "mean", hour)
        if day == "mean" and mean_key in self._real_base_cache:
            route_board, metro, bus_unmatched, outside_a, unmatched = self._real_base_cache[mean_key]
        elif day == "mean":
            parts = [one_day(d) for d in mean_days]
            n = len(parts)
            route_board, metro = defaultdict(lambda: [0, 0]), defaultdict(lambda: [0, 0])
            bus_unmatched, outside_a = [0, 0], [0, 0]
            unmatched = defaultdict(float)
            for rb, mt, bu, oa, um in parts:
                for k, (b, a) in rb.items():
                    route_board[k][0] += b / n
                    route_board[k][1] += a / n
                for k, (b, a) in mt.items():
                    metro[k][0] += b / n
                    metro[k][1] += a / n
                for i in (0, 1):
                    bus_unmatched[i] += bu[i] / n
                    outside_a[i] += oa[i] / n
                for k, v in um.items():
                    unmatched[k] += v / n
            route_board, metro, unmatched = dict(route_board), dict(metro), dict(unmatched)
            self._real_base_cache[mean_key] = (route_board, metro, bus_unmatched, outside_a, unmatched)
        else:
            route_board, metro, bus_unmatched, outside_a, unmatched = one_day(day)
        key = (snapshot.snapshot_id, day, hour)
        district_key = (*key, district)
        district_cache = getattr(self, "_real_district_cache", None)
        if district_cache is None:
            district_cache = self._real_district_cache = {}
        scoped = district_cache.get(district_key)
        if scoped is None:
            scoped_routes = {}
            for (rid, sid), counts in route_board.items():
                stop = reference["stops"][sid]
                if district == "all" and stop.get("gu") and stop["gu"] not in seoul_gu_names:
                    continue
                if in_area([float(stop["lon"]), float(stop["lat"])]):
                    scoped_routes[(rid, sid)] = counts
            scoped_metro = {sid: counts for sid, counts in metro.items()
                            if in_area(a_stops[sid]["coordinates"])}
            if len(district_cache) >= 64:
                district_cache.clear()
            scoped = (scoped_routes, scoped_metro)
            district_cache[district_key] = scoped
        scoped_routes, scoped_metro = scoped
        bus = defaultdict(lambda: [0, 0, 0, 0])
        bus_by_kind = defaultdict(lambda: defaultdict(int))
        for (rid, sid), (board, alight) in scoped_routes.items():
            item = bus[sid]
            item[0] += board
            item[1] += alight
            bus_by_kind[sid][route_kinds[rid]] += board
            if rid in strike or route_kinds[rid] in disabled:
                item[2] += board
                item[3] += alight
        def feature(sid, stop, board, alight, mode, affected=0):
            point = stop["coordinates"] if mode == "metro" else [float(stop["lon"]), float(stop["lat"])]
            name = stop["name"] if mode == "metro" else stop["BSST_NM"]
            return {"type": "Feature", "geometry": {"type": "Point", "coordinates": point},
                    "properties": {"stop_id": sid, "name": name, "mode": mode, "board": round(board, 1),
                                   "alight": round(alight, 1), "affected_board": round(affected, 1),
                                   "passenger_count": round(board, 1)}}
        bus_features = [feature(sid, reference["stops"][sid], *values[:2], "bus", values[2]) for sid, values in bus.items()]
        for item in bus_features:
            item["properties"]["board_by_a_route_kind"] = dict(bus_by_kind[item["properties"]["stop_id"]])
        metro_features = [feature(sid, a_stops[sid], *values, "metro", values[0] if "metro" in disabled else 0)
                          for sid, values in scoped_metro.items()]
        result = {"status": "available", "demand_kind": "observed_normal_and_strike_boarding_exposure",
                  "source": "share_k5/seoul stop_route_hourly.csv + subway_od_hourly.csv",
                  "date": day, "hour": hour, "district": district, "k": 5,
                  "mean_days": mean_days or None,
                   "time_window": "07:30~09:00" if hour == "morning_0730_estimate" else "07:00~09:00" if hour == "morning" else hour,
                   "time_window_status": "hourly_half_hour_estimate" if hour == "morning_0730_estimate" else "hourly_observed",
                  "strike_route_ids": sorted(strike), "disabled_modes": sorted(disabled), "bus_features": bus_features,
                  "metro_features": metro_features,
                  "totals": {"bus_board": sum(f["properties"]["board"] for f in bus_features),
                             "bus_alight": sum(f["properties"]["alight"] for f in bus_features),
                             "metro_board": sum(f["properties"]["board"] for f in metro_features),
                             "metro_alight": sum(f["properties"]["alight"] for f in metro_features),
                             "affected_board": sum(f["properties"]["affected_board"] for f in bus_features),
                             "affected_metro_board": sum(f["properties"]["affected_board"] for f in metro_features)},
                  "bus_board_by_a_route_kind": {kind: sum(values.get(kind, 0) for values in bus_by_kind.values())
                                                  for kind in sorted(set(route_kinds.values()) - {"metro"})},
                  "unmatched_metro": unmatched,
                  "unmatched_bus": {"rows": bus_unmatched[0], "boardings": bus_unmatched[1]},
                  "outside_a_routes": {"rows": outside_a[0], "boardings": outside_a[1]},
                  "unmet_status": "unavailable_no_capacity_or_passenger_rerouting",
                   "notice": "운행 중단 영향은 해당 수단·노선의 정상일 관측 승차 노출량입니다. 실제 전환·흡수·미충족 인원이 아닙니다."
                   + (" 07:30~09:00 값은 시간별 자료의 07시 50%와 08시 100%를 합친 추정치입니다."
                      if hour == "morning_0730_estimate" else "")}
        return result
