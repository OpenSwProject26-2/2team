"""승객 발생(simulator.md §2, m07-output-contract.md §4.1).

두 단계로 나눈다. 둘 다 난수 상태가 없고 정수 연산과 SHA-256만 쓴다. 실행 전에 한 번 돌고 운행안을
입력으로 받지 않으므로 모든 안이 같은 승객을 받는다.

1. `realize_bins`: 소수 유량 → 시간대별 정수 인원(`demand_bins.csv` 행). 체계적 반올림(누적 유량 교차)으로
   시간대마다 몇 명인지 정한다. `u`는 OD별로 seed에서 SHA-256으로 정한다.
2. `generate`: `demand_bins.csv` 행 + seed → 승객 1명씩. M07 생성기 `uniform_bin_sha256_v1`을
   계약 문서 §4.1의 글로 구현한다(검증기 코드를 import하거나 복사하지 않는다).
"""

from __future__ import annotations

import hashlib
import json
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence

from . import io
from .models import BinRow, DemandFlow, Passenger, Policy, SimError, Window

MICRO = 1_000_000
ARRIVAL_METHODS = ("systematic",)
GENERATOR = "uniform_bin_sha256_v1"
_WORD = 1 << 64


# ---------------------------------------------------------------- 1. 유량 → 시간대별 인원

def u_micro(seed: int | str, kind: str, origin_stop_ids: Sequence[str], dest_stop_ids: Sequence[str]) -> int:
    key = json.dumps([str(seed), kind, sorted(origin_stop_ids), sorted(dest_stop_ids)],
                     ensure_ascii=False, separators=(",", ":"))
    return int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:8], "big") % MICRO


def realize_od(flows_micro: Sequence[int], bin_starts: Sequence[int], bin_length_s: int, u: int) -> list[int]:
    """누적 유량이 u, u+MICRO, u+2·MICRO … 를 넘는 시각마다 승객 1명(§2.2). 시간대별 인원의 근거."""
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


def bin_counts(flows_micro: Sequence[int], u: int) -> list[int]:
    """시간대별 인원: 문턱 u + k·MICRO가 그 시간대의 누적 구간 [cum, cum+f)에 드는 개수."""
    if not 0 <= u < MICRO:
        raise SimError(SimError.INPUT_INVALID, "u must be in [0, MICRO)")
    out: list[int] = []
    cum = 0
    for f in flows_micro:
        # 문턱 개수 = #{k ≥ 0 : u + k·MICRO < cum + f} − #{k ≥ 0 : u + k·MICRO < cum}
        def below(x: int) -> int:
            return 0 if x <= u else (x - u - 1) // MICRO + 1
        out.append(below(cum + f) - below(cum))
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


def _od_of(row: DemandFlow) -> tuple[str, str, str]:
    if row.kind not in ("access", "egress"):
        raise SimError(SimError.INPUT_INVALID, "kind must be access or egress", {"kind": row.kind})
    if not isinstance(row.stop_id, str) or not row.stop_id or not isinstance(row.station_stop_id, str) \
            or not row.station_stop_id:
        raise SimError(SimError.INPUT_INVALID, "stop_id and station_stop_id must be non-empty strings")
    origin, dest = (row.stop_id, row.station_stop_id) if row.kind == "access" else (row.station_stop_id, row.stop_id)
    if origin == dest:
        raise SimError(SimError.INPUT_INVALID, "origin and destination stops are the same", {"stop_id": origin})
    return row.kind, origin, dest


def realize_bins(rows: Iterable[DemandFlow], policy: Policy, window: Window, seed: int | str,
                 bin_length_s: int) -> list[BinRow]:
    """소수 유량 행 → `demand_bins.csv` 행(인원 0인 시간대는 뺀다).

    aggregate_id = `od{OD 순번:06d}-t{bin_start_s:06d}`. OD 순번은 `[kind, origin, destination]` JSON 순.
    시간대 [start, start+길이)가 운영시간 [W_s, W_e] 밖으로 나가면 INPUT_INVALID(발생 시각이 시간대 안
    어디든 될 수 있으므로).
    """
    if policy.arrival_method not in ARRIVAL_METHODS:
        raise SimError(SimError.OUT_OF_SCOPE, "unsupported arrival_method", {"arrival_method": policy.arrival_method})
    if bin_length_s <= 0:
        raise SimError(SimError.INPUT_INVALID, "bin_length_s must be > 0")
    flows: dict[tuple[str, str, str], dict[int, int]] = {}
    for row in rows:
        od = _od_of(row)
        bins = flows.setdefault(od, {})
        bins[row.bin_start_s] = bins.get(row.bin_start_s, 0) + to_micro(row.flow)
    out: list[BinRow] = []
    for od_seq, ((kind, origin, dest), bins) in enumerate(sorted(flows.items(), key=lambda i: io.dumps_line(list(i[0]))),
                                                          start=1):
        starts = sorted(bins)
        for a, b in zip(starts, starts[1:]):
            if b - a < bin_length_s:
                raise SimError(SimError.INPUT_INVALID, "time bins overlap", {"bin_start_s": b})
        counts = bin_counts([bins[b] for b in starts], u_micro(seed, kind, [origin], [dest]))
        for start, count in zip(starts, counts):
            if count == 0:
                continue
            if start < window.start_s or start + bin_length_s - 1 > window.end_s:
                raise SimError(SimError.INPUT_INVALID, "time bin with passengers lies outside operating window",
                               {"bin_start_s": start, "kind": kind})
            out.append(BinRow(f"od{od_seq:06d}-t{start:06d}", origin, dest, start, bin_length_s, count))
    return out


# ---------------------------------------------------------------- 2. M07 생성기 uniform_bin_sha256_v1

def passenger_id(aggregate_id: str, index: int) -> str:
    return json.dumps([aggregate_id, index], separators=(",", ":"))


def bin_offset(seed: Any, aggregate_id: str, index: int, width: int) -> int:
    """계약 §4.1: base = json.dumps([generator, seed, aggregate_id])(기본 구분자) + NUL 바이트(읽기 B)."""
    if width < 1:
        raise SimError(SimError.INPUT_INVALID, "bin_width_s must be >= 1")
    prefix = json.dumps([GENERATOR, seed, aggregate_id]).encode() + b"\x00" + index.to_bytes(8, "big")
    limit = _WORD - _WORD % width
    c = 0
    while True:
        x = int.from_bytes(hashlib.sha256(prefix + c.to_bytes(4, "big")).digest()[:8], "big")
        if x < limit:
            return x % width
        c += 1


def generate(rows: Sequence[BinRow], seed: Any) -> list[Passenger]:
    """승객 목록. 순서는 aggregate_id 오름차순, 그 안에서 index 순(M07 realization 순서)."""
    out: list[Passenger] = []
    for r in sorted(rows, key=lambda r: r.aggregate_id):
        for i in range(r.passenger_count):
            out.append(Passenger(passenger_id(r.aggregate_id, i), r.origin, r.destination,
                                 r.bin_start_s + bin_offset(seed, r.aggregate_id, i, r.bin_width_s)))
    return out


def source_checksum(rows: Sequence[BinRow]) -> str:
    payload = [{"aggregate_id": r.aggregate_id, "bin_start_s": r.bin_start_s, "bin_width_s": r.bin_width_s,
                "destination": r.destination, "origin": r.origin, "passenger_count": r.passenger_count}
               for r in sorted(rows, key=lambda r: r.aggregate_id)]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _row_json(p: Passenger) -> str:
    return json.dumps([p.passenger_id, p.origin, p.destination, p.arrival_s], separators=(",", ":"))


def realization_checksum(seed: Any, source: str, passengers: Sequence[Passenger]) -> str:
    h = hashlib.sha256(json.dumps([GENERATOR, seed, source], separators=(",", ":")).encode())
    for p in passengers:
        h.update(b"\n" + _row_json(p).encode())
    return h.hexdigest()


def realization_bytes(rows: Sequence[BinRow], seed: Any, passengers: Sequence[Passenger]) -> tuple[bytes, str]:
    """`realization.jsonl` 바이트와 checksum. `passengers`는 `generate` 순서여야 한다."""
    source = source_checksum(rows)
    checksum = realization_checksum(seed, source, passengers)
    meta = {"checksum": checksum, "generator": GENERATOR, "passenger_count": len(passengers),
            "rows": [{"aggregate_id": r.aggregate_id, "bin_start_s": r.bin_start_s, "bin_width_s": r.bin_width_s,
                      "destination": r.destination, "origin": r.origin, "passenger_count": r.passenger_count}
                     for r in sorted(rows, key=lambda r: r.aggregate_id)],
            "seed": seed, "source_checksum": source}
    lines = [io.canonical_json(meta)] + [_row_json(p) for p in passengers]
    return ("\n".join(lines) + "\n").encode("utf-8"), checksum


# ---------------------------------------------------------------- 입력 묶음(run_inputs.json)

def write_demand_bins(rows: Iterable[BinRow], path: Path) -> str:
    return io.write_bytes(path, io.demand_bins_bytes(rows))


def input_checksum(demand_bins_sha256: str, seed: Any, travel_time_sha256: str, scenario_sha256: str) -> str:
    return io.sha256_bytes(io.canonical_json({
        "demand_bins": demand_bins_sha256,
        "seed": seed,
        "travel_times": travel_time_sha256,
        "scenario": scenario_sha256,
    }).encode("utf-8"))


def _rel(path: Path, base: Path) -> str:
    try:
        return PurePosixPath(Path(path).resolve().relative_to(Path(base).resolve())).as_posix()
    except ValueError:
        raise SimError(SimError.INPUT_INVALID, "input files must be inside the run_inputs directory") from None


def build_run_inputs(input_bundle_id: str, scenario_path: Path, demand_bins_path: Path, travel_times_path: Path,
                     seed: int | str, out_path: Path, method: str = "systematic") -> dict:
    """run_inputs.json 내용을 만들어 쓴다. URI는 run_inputs.json이 있는 디렉터리 기준 상대 경로.

    `method`는 `demand_bins.csv`를 어떻게 만들었는지의 기록값(`systematic` = §2 실현기, `precomputed` = C 수요).
    """
    if isinstance(seed, bool) or not isinstance(seed, (int, str)):
        raise SimError(SimError.INPUT_INVALID, "seed must be int or string")
    scenario = io.scenario_from_dict(io.read_json(scenario_path))
    rows = io.read_demand_bins(demand_bins_path)
    base = Path(out_path).parent
    bins_sha = io.sha256_file(demand_bins_path)
    tt_sha = io.sha256_file(travel_times_path)
    sc_sha = io.sha256_file(scenario_path)
    doc = {
        "input_bundle_id": input_bundle_id,
        "scenario_id": scenario.scenario_id,
        "seed": seed,
        "demand_bins_uri": _rel(demand_bins_path, base),
        "travel_time_realization_uri": _rel(travel_times_path, base),
        "arrival_meta": {
            "method": method,
            "generator": GENERATOR,
            "passenger_count": sum(r.passenger_count for r in rows),
            "sha256": bins_sha,
        },
        "travel_time_sha256": tt_sha,
        "scenario_sha256": sc_sha,
        "checksum": input_checksum(bins_sha, seed, tt_sha, sc_sha),
    }
    io.write_bytes(out_path, io.json_bytes(doc))
    return doc
