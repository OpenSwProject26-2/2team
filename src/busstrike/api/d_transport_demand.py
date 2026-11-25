"""Optional, precomputed spatial demand for transport removal scenarios.

Only aggregate stop/hour results are accepted. No demand is inferred from GTFS
route counts or map visibility. Every disabled-mode combination is an exact key.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from .d_geo import DataError

SCHEMA = "d-transport-demand/1"
MODES = frozenset({"city", "village", "gyeonggi", "metro"})
HOURS = frozenset({"06", "07", "08", "09"})
KINDS = frozenset({"M05_unserved", "pre_shuttle_residual"})


def _nonnegative(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DataError("INPUT_INVALID", f"{label}은 0 이상의 숫자여야 합니다.")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise DataError("INPUT_INVALID", f"{label}은 유한한 0 이상의 숫자여야 합니다.")
    return result


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DataError("INPUT_INVALID", f"{label}은 비어 있지 않은 문자열이어야 합니다.")
    return value.strip()


def _modes(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or any(not isinstance(x, str) for x in value):
        raise DataError("INPUT_INVALID", "disabled_modes는 비어 있지 않은 교통수단 배열이어야 합니다.")
    if len(set(value)) != len(value) or not set(value) <= MODES:
        raise DataError("INPUT_INVALID", "disabled_modes에 중복 또는 지원하지 않는 수단이 있습니다.")
    return tuple(sorted(value))


def _feature(stop: object, kind: str, index: int) -> dict:
    if not isinstance(stop, dict):
        raise DataError("INPUT_INVALID", f"stops[{index}]는 객체여야 합니다.")
    allowed = {"stop_id", "name", "lon", "lat", "hours"}
    if set(stop) - allowed:
        raise DataError("INPUT_INVALID", f"stops[{index}]에 허용되지 않은 필드가 있습니다.")
    stop_id = _text(stop.get("stop_id"), f"stops[{index}].stop_id")
    name = _text(stop["name"], f"stops[{index}].name") if "name" in stop else stop_id
    lon = _nonnegative(stop.get("lon"), f"stops[{index}].lon")
    lat = _nonnegative(stop.get("lat"), f"stops[{index}].lat")
    if not 125 <= lon <= 130 or not 35 <= lat <= 40:
        raise DataError("INPUT_INVALID", f"stops[{index}] 좌표가 수도권 범위를 벗어났습니다.")
    hours = stop.get("hours")
    if not isinstance(hours, dict) or not hours or not set(hours) <= HOURS:
        raise DataError("INPUT_INVALID", f"stops[{index}].hours는 06~09 시간칸 객체여야 합니다.")
    values: dict[str, dict | float] = {}
    if kind == "M05_unserved":
        total = unserved = 0.0
        for hour, cell in hours.items():
            if not isinstance(cell, dict) or set(cell) != {"total", "unserved"}:
                raise DataError("INPUT_INVALID", f"stops[{index}].hours[{hour}]에 total·unserved가 필요합니다.")
            count = _nonnegative(cell["total"], f"stops[{index}].hours[{hour}].total")
            missed = _nonnegative(cell["unserved"], f"stops[{index}].hours[{hour}].unserved")
            if missed > count:
                raise DataError("INPUT_INVALID", "미충족 인원은 평가 수요보다 클 수 없습니다.")
            values[hour] = {"total": count, "unserved": missed}
            total += count
            unserved += missed
        props = {"total": total, "unserved": unserved, "hours": values}
    else:
        total = 0.0
        for hour, cell in hours.items():
            count = _nonnegative(cell, f"stops[{index}].hours[{hour}]")
            values[hour] = count
            total += count
        props = {"passenger_count": total, "hours": values}
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {"stop_id": stop_id, "name": name, **props},
    }


class TransportDemandStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.scenarios: dict[tuple, dict] = {}
        if path is None:
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise DataError("DATA_INCOMPLETE", "교통수단별 수요 파일을 읽을 수 없습니다.") from error
        if not isinstance(payload, dict) or payload.get("schema") != SCHEMA or not isinstance(payload.get("scenarios"), list):
            raise DataError("INPUT_INVALID", f"교통수단별 수요 파일에는 schema={SCHEMA}와 scenarios 배열이 필요합니다.")
        for index, item in enumerate(payload["scenarios"]):
            if not isinstance(item, dict):
                raise DataError("INPUT_INVALID", f"scenarios[{index}]는 객체여야 합니다.")
            allowed = {"region", "fleet", "plan", "period", "date", "disabled_modes", "kind", "source", "stops"}
            if set(item) - allowed:
                raise DataError("INPUT_INVALID", f"scenarios[{index}]에 허용되지 않은 필드가 있습니다.")
            region = _text(item.get("region"), "region")
            period = _text(item.get("period"), "period")
            if period not in {"exp", "test"}:
                raise DataError("INPUT_INVALID", "period는 exp 또는 test여야 합니다.")
            date = _text(item.get("date"), "date")
            fleet = item.get("fleet")
            if isinstance(fleet, bool) or not isinstance(fleet, int) or fleet <= 0:
                raise DataError("INPUT_INVALID", "fleet은 양의 정수여야 합니다.")
            plan = _text(item.get("plan"), "plan")
            modes = _modes(item.get("disabled_modes"))
            kind = item.get("kind")
            if kind not in KINDS:
                raise DataError("INPUT_INVALID", "kind는 M05_unserved 또는 pre_shuttle_residual이어야 합니다.")
            source = item.get("source")
            if not isinstance(source, dict) or set(source) - {"network_snapshot_id", "input_bundle_id", "run_id", "generated_at", "method"}:
                raise DataError("INPUT_INVALID", "source에는 허용된 출처 필드만 넣을 수 있습니다.")
            _text(source.get("network_snapshot_id"), "source.network_snapshot_id")
            _text(source.get("input_bundle_id"), "source.input_bundle_id")
            if kind == "M05_unserved":
                _text(source.get("run_id"), "source.run_id")
            stops = item.get("stops")
            if not isinstance(stops, list):
                raise DataError("INPUT_INVALID", "stops 배열이 필요합니다.")
            features = [_feature(stop, kind, i) for i, stop in enumerate(stops)]
            stop_ids = [feature["properties"]["stop_id"] for feature in features]
            if len(stop_ids) != len(set(stop_ids)):
                raise DataError("INPUT_INVALID", "한 시나리오에 중복 stop_id가 있습니다.")
            key = (region, fleet, plan, period, date, modes)
            if key in self.scenarios:
                raise DataError("INPUT_INVALID", "동일 조건의 교통수단별 수요 시나리오가 중복됩니다.")
            self.scenarios[key] = {"status": "available", "kind": kind, "source": source, "features": features,
                                   "selection": {"region": region, "fleet": fleet, "plan": plan, "period": period,
                                                 "date": date, "disabled_modes": list(modes)}}

    def get(self, query: dict[str, list[str]]) -> dict:
        raw_modes = query.get("disabled_modes", [""])[0]
        modes = _modes(raw_modes.split(","))
        region = _text(query.get("region", [None])[0], "region")
        plan = _text(query.get("plan", [None])[0], "plan")
        period = _text(query.get("period", [None])[0], "period")
        date = _text(query.get("date", [None])[0], "date")
        try:
            fleet = int(query.get("fleet", [""])[0])
        except (TypeError, ValueError) as error:
            raise DataError("INPUT_INVALID", "fleet은 양의 정수여야 합니다.") from error
        if fleet <= 0:
            raise DataError("INPUT_INVALID", "fleet은 양의 정수여야 합니다.")
        selection = {"region": region, "fleet": fleet, "plan": plan, "period": period,
                     "date": date, "disabled_modes": list(modes)}
        if self.path is None:
            return {"status": "not_configured", "selection": selection, "message": "--transport-demand 파일이 연결되지 않았습니다."}
        result = self.scenarios.get((region, fleet, plan, period, date, modes))
        if result is None:
            return {"status": "missing", "selection": selection, "message": "이 교통수단 조합·날짜·운행안의 정류장별 결과가 없습니다."}
        expected = query.get("network_snapshot_id", [""])[0]
        actual = result["source"]["network_snapshot_id"]
        if expected and expected != actual:
            return {"status": "snapshot_mismatch", "selection": selection,
                    "message": "교통망 스냅샷이 달라 이 수요 결과를 표시하지 않습니다."}
        return result
