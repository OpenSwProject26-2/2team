"""승객 발생 실현기(simulator.md §2): 소수 유량 → 정수 승객 + 도착 시각.

체계적 반올림(누적 유량 교차)만 구현한다. 난수 상태가 없고 정수 연산만 쓴다.
`u`는 OD별로 seed에서 SHA-256으로 정한다. 실행 전에 한 번 돌고, 운행안을 입력으로 받지 않는다.
"""

from __future__ import annotations

import hashlib
import json
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence

from . import io
from .models import DemandFlow, Passenger, Policy, SimError, Window

MICRO = 1_000_000
ARRIVAL_METHODS = ("systematic",)


def u_micro(seed: int | str, kind: str, origin_stop_ids: Sequence[str], dest_stop_ids: Sequence[str]) -> int:
    key = json.dumps([str(seed), kind, sorted(origin_stop_ids), sorted(dest_stop_ids)],
                     ensure_ascii=False, separators=(",", ":"))
    return int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:8], "big") % MICRO


def realize_od(flows_micro: Sequence[int], bin_starts: Sequence[int], bin_length_s: int, u: int) -> list[int]:
    """누적 유량이 u, u+MICRO, u+2·MICRO … 를 넘는 시각마다 승객 1명."""
    if not 0 <= u < MICRO:
        raise SimError(SimError.INPUT_INVALID, "u must be in [0, MICRO)")
    out: list[int] = []
    cum = 0
    k = 0
    for f, b in zip(flows_micro, bin_starts):
        while f > 0 and u + k * MICRO < cum + f:
            out.append(b + (u + k * MICRO - cum) * bin_length_s // f)
            k += 1
        cum += f
    return out


def to_micro(flow: Any) -> int:
    """유량(명) → 백만분의 1명 정수. float 덧셈을 거치지 않는다."""
    if isinstance(flow, bool):
        raise SimError(SimError.INPUT_INVALID, "flow must be a number")
    try:
        value = flow if isinstance(flow, Decimal) else Decimal(flow if isinstance(flow, (str, int)) else str(flow))
    except (InvalidOperation, ValueError, TypeError):
        raise SimError(SimError.INPUT_INVALID, "flow must be a number", {"flow": str(flow)}) from None
    if not value.is_finite() or value < 0:
        raise SimError(SimError.INPUT_INVALID, "flow must be finite and >= 0", {"flow": str(flow)})
    return int((value * MICRO).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def _od_of(row: DemandFlow, station_stops: Mapping[str, Sequence[str]]) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    if row.kind not in ("access", "egress"):
        raise SimError(SimError.INPUT_INVALID, "kind must be access or egress", {"kind": row.kind})
    if row.station_id not in station_stops or not station_stops[row.station_id]:
        raise SimError(SimError.INPUT_INVALID, "station has no stops", {"station_id": row.station_id})
    station = tuple(sorted(set(station_stops[row.station_id])))
    stop = (row.stop_id,)
    origin, dest = (stop, station) if row.kind == "access" else (station, stop)
    if set(origin) & set(dest):
        raise SimError(SimError.INPUT_INVALID, "origin and destination stops overlap",
                       {"stop_id": row.stop_id, "station_id": row.station_id})
    return row.kind, origin, dest


def realize_arrivals(rows: Iterable[DemandFlow], station_stops: Mapping[str, Sequence[str]], policy: Policy,
                     window: Window, seed: int | str, bin_length_s: int) -> list[Passenger]:
    if policy.arrival_method not in ARRIVAL_METHODS:
        raise SimError(SimError.OUT_OF_SCOPE, "unsupported arrival_method", {"arrival_method": policy.arrival_method})
    if bin_length_s <= 0:
        raise SimError(SimError.INPUT_INVALID, "bin_length_s must be > 0")
    flows: dict[tuple[str, tuple[str, ...], tuple[str, ...]], dict[int, int]] = {}
    for row in rows:
        od = _od_of(row, station_stops)
        bins = flows.setdefault(od, {})
        bins[row.bin_start_s] = bins.get(row.bin_start_s, 0) + to_micro(row.flow)
    keyed = sorted(flows.items(), key=lambda item: io.dumps_line([item[0][0], list(item[0][1]), list(item[0][2])]))
    out: list[Passenger] = []
    for od_seq, ((kind, origin, dest), bins) in enumerate(keyed, start=1):
        starts = sorted(bins)
        for a, b in zip(starts, starts[1:]):
            if b - a < bin_length_s:
                raise SimError(SimError.INPUT_INVALID, "time bins overlap", {"bin_start_s": b})
        times = realize_od([bins[b] for b in starts], starts, bin_length_s, u_micro(seed, kind, origin, dest))
        for k, t in enumerate(times, start=1):
            if not window.start_s <= t <= window.end_s:
                raise SimError(SimError.INPUT_INVALID, "realized arrival outside operating window",
                               {"arrival_s": t, "kind": kind})
            out.append(Passenger(f"p{od_seq:06d}-{k:05d}", kind, origin, dest, t, policy.max_wait_s))
    out.sort(key=lambda p: (p.arrival_s, p.passenger_id))
    return out


def arrivals_bytes(passengers: Sequence[Passenger]) -> bytes:
    ordered = sorted(passengers, key=lambda p: (p.arrival_s, p.passenger_id))
    return io.jsonl_bytes(io.passenger_to_dict(p) for p in ordered)


def write_arrivals(passengers: Sequence[Passenger], path: Path) -> str:
    return io.write_bytes(path, arrivals_bytes(passengers))


def input_checksum(arrivals_sha256: str, travel_time_sha256: str, scenario_sha256: str) -> str:
    return io.sha256_bytes(io.canonical_json({
        "arrivals": arrivals_sha256,
        "travel_times": travel_time_sha256,
        "scenario": scenario_sha256,
    }).encode("utf-8"))


def _rel(path: Path, base: Path) -> str:
    try:
        return PurePosixPath(Path(path).resolve().relative_to(Path(base).resolve())).as_posix()
    except ValueError:
        raise SimError(SimError.INPUT_INVALID, "input files must be inside the run_inputs directory") from None


def build_run_inputs(input_bundle_id: str, scenario_path: Path, arrivals_path: Path, travel_times_path: Path,
                     seed: int | str, bin_length_s: int, out_path: Path) -> dict:
    """run_inputs.json 내용을 만들어 쓴다. URI는 run_inputs.json이 있는 디렉터리 기준 상대 경로."""
    scenario = io.scenario_from_dict(io.read_json(scenario_path))
    passengers = io.read_jsonl(arrivals_path)
    base = Path(out_path).parent
    arrivals_sha = io.sha256_file(arrivals_path)
    tt_sha = io.sha256_file(travel_times_path)
    sc_sha = io.sha256_file(scenario_path)
    doc = {
        "input_bundle_id": input_bundle_id,
        "scenario_id": scenario.scenario_id,
        "seed": seed,
        "arrival_realization_uri": _rel(arrivals_path, base),
        "travel_time_realization_uri": _rel(travel_times_path, base),
        "arrival_meta": {
            "method": scenario.policy.arrival_method,
            "bin_length_s": bin_length_s,
            "passenger_count": len(passengers),
            "sha256": arrivals_sha,
        },
        "travel_time_sha256": tt_sha,
        "scenario_sha256": sc_sha,
        "checksum": input_checksum(arrivals_sha, tt_sha, sc_sha),
    }
    io.write_bytes(out_path, io.json_bytes(doc))
    return doc
