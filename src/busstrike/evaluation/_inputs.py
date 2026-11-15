"""검증기 자체 입력 파서·구간 시간 조회(simulator.md §0.3, §1.5, §3.5, §6, §8.1).

`busstrike.sim`을 import하지 않는다. 설계 문서만 보고 따로 구현한 것이라, 시뮬레이터 파서의
버그를 함께 갖지 않는다. 입력 문서의 형식 오류는 `ValidatorInputError`로 멈춘다(위반이 아니라
검증을 시작할 수 없는 상태). 이벤트 로그의 형식 오류만은 위반(V01)으로 보고한다.
"""

from __future__ import annotations

import bisect
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCENARIO_FILE = "scenario.json"
RUN_INPUTS_FILE = "run_inputs.json"
PLAN_FILE = "plan.json"
RUN_FILE = "run.json"
DEFAULT_TIMETABLE_FILE = "timetable.json"
DEFAULT_EVENTS_FILE = "events.jsonl"
DEFAULT_METRICS_FILE = "metrics.json"


class ValidatorInputError(Exception):
    """입력 파일이 없거나 읽을 수 없어 검증을 시작할 수 없다."""


# ---------------------------------------------------------------- 직렬화·해시(§0.2)

def canonical_json(x: Any) -> str:
    return json.dumps(x, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def json_bytes(x: Any) -> bytes:
    return (canonical_json(x) + "\n").encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _read(path: Path) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise ValidatorInputError(f"cannot read {Path(path).name}") from exc


def _parse_json(data: bytes, name: str) -> Any:
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValidatorInputError(f"{name} is not valid JSON") from exc


def _need(raw: Any, key: str, where: str) -> Any:
    if not isinstance(raw, dict) or key not in raw:
        raise ValidatorInputError(f"{where}: missing field {key}")
    return raw[key]


def _need_int(raw: Any, key: str, where: str) -> int:
    v = _need(raw, key, where)
    if not is_int(v):
        raise ValidatorInputError(f"{where}: {key} must be int")
    return v


def _need_str(raw: Any, key: str, where: str) -> str:
    v = _need(raw, key, where)
    if not isinstance(v, str) or not v:
        raise ValidatorInputError(f"{where}: {key} must be non-empty string")
    return v


def _need_list(raw: Any, key: str, where: str) -> list:
    v = _need(raw, key, where)
    if not isinstance(v, list):
        raise ValidatorInputError(f"{where}: {key} must be a list")
    return v


def _str_tuple(raw: Any, key: str, where: str) -> tuple[str, ...]:
    v = _need_list(raw, key, where)
    if any(not isinstance(x, str) or not x for x in v):
        raise ValidatorInputError(f"{where}: {key} must be a list of strings")
    return tuple(v)


# ---------------------------------------------------------------- 입력 모델

@dataclass(frozen=True)
class Vehicle:
    vehicle_id: str
    seat_capacity: int
    standing_capacity: int
    available_start_s: int
    available_end_s: int
    initial_stop_id: str | None
    end_stop_id: str | None

    @property
    def capacity(self) -> int:
        return self.seat_capacity + self.standing_capacity


@dataclass(frozen=True)
class Policy:
    max_journey_s: int
    dwell_base_s: int
    dwell_per_boarding_s: int
    dwell_per_alighting_s: int
    turnaround_s: int
    cycle_rest_s: int


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    network_snapshot_id: str
    start_s: int
    end_s: int
    vehicles: dict[str, Vehicle]
    policy: Policy


@dataclass(frozen=True)
class Passenger:
    passenger_id: str
    kind: str
    origin_stop_ids: tuple[str, ...]
    dest_stop_ids: tuple[str, ...]
    arrival_s: int
    max_wait_s: int

    @property
    def deadline_s(self) -> int:
        return self.arrival_s + self.max_wait_s


@dataclass(frozen=True)
class Segment:
    distance_m: int
    bin_starts: tuple[int, ...]
    travel_s: tuple[int, ...]


class TravelTimes:
    """구간 시간 조회(§3.5): 상류 출발 시각 t에 대해 `bin_start_s ≤ t`인 마지막 시간대 값."""

    def __init__(self, network_snapshot_id: str, segments: dict[tuple[str, str], Segment]):
        self.network_snapshot_id = network_snapshot_id
        self.segments = segments

    def has(self, a: str, b: str) -> bool:
        return (a, b) in self.segments

    def lookup(self, a: str, b: str, t: int) -> int | None:
        seg = self.segments.get((a, b))
        if seg is None:
            return None
        i = bisect.bisect_right(seg.bin_starts, t) - 1
        if i < 0:
            return None
        return seg.travel_s[i]

    def distance(self, a: str, b: str) -> int | None:
        seg = self.segments.get((a, b))
        return None if seg is None else seg.distance_m


@dataclass(frozen=True)
class Leg:
    in_service: bool
    stop_ids: tuple[str, ...]


@dataclass(frozen=True)
class PlanRoute:
    route_id: str
    legs: tuple[Leg, ...]
    vehicle_count: int
    vehicle_ids: tuple[str, ...] | None


@dataclass(frozen=True)
class Plan:
    plan_id: str
    scenario_id: str
    revision: int
    routes: dict[str, PlanRoute]


@dataclass(frozen=True)
class Trip:
    trip_id: str
    route_id: str
    vehicle_id: str
    cycle_index: int
    leg_index: int
    in_service: bool
    stop_seqs: tuple[int, ...]
    stop_ids: tuple[str, ...]
    sched_s: tuple[int, ...]

    @property
    def last_seq(self) -> int:
        return len(self.stop_ids) - 1


@dataclass(frozen=True)
class Duty:
    vehicle_id: str
    route_id: str
    trip_ids: tuple[str, ...]
    start_s: int
    end_s: int


@dataclass(frozen=True)
class TimetableRoute:
    route_id: str
    period_s: int | None
    vehicle_ids: tuple[str, ...]


@dataclass(frozen=True)
class Timetable:
    plan_id: str
    plan_revision: int
    origin: str
    routes: tuple[TimetableRoute, ...]
    trips: tuple[Trip, ...]
    duties: tuple[Duty, ...]
    trips_by_id: dict[str, Trip] = field(default_factory=dict)


@dataclass
class RunFiles:
    """검증 대상 한 실행의 입력·출력(파일 바이트와 해석 결과)."""

    paths: dict[str, Path]
    raw: dict[str, bytes]
    scenario: Scenario
    run_inputs: dict
    passengers: dict[str, Passenger]
    travel_times: TravelTimes
    plan: Plan
    timetable: Timetable
    metrics: Any
    run: dict
    event_lines: list[bytes]


# ---------------------------------------------------------------- 파서

def parse_scenario(raw: Any) -> Scenario:
    w = "scenario.json"
    window = _need(raw, "operating_window", w)
    fleet = _need(raw, "fleet", w)
    vehicles: dict[str, Vehicle] = {}
    for item in _need_list(fleet, "vehicles", w + " fleet"):
        vid = _need_str(item, "vehicle_id", w + " vehicle")
        if vid in vehicles:
            raise ValidatorInputError(f"{w}: duplicate vehicle_id {vid}")
        init, end = item.get("initial_stop_id"), item.get("end_stop_id")
        vehicles[vid] = Vehicle(
            vehicle_id=vid,
            seat_capacity=_need_int(item, "seat_capacity", w),
            standing_capacity=_need_int(item, "standing_capacity", w),
            available_start_s=_need_int(item, "available_start_s", w),
            available_end_s=_need_int(item, "available_end_s", w),
            initial_stop_id=init if isinstance(init, str) else None,
            end_stop_id=end if isinstance(end, str) else None,
        )
    pol = _need(raw, "policy", w)
    policy = Policy(
        max_journey_s=_need_int(pol, "max_journey_s", w),
        dwell_base_s=_need_int(pol, "dwell_base_s", w),
        dwell_per_boarding_s=_need_int(pol, "dwell_per_boarding_s", w),
        dwell_per_alighting_s=_need_int(pol, "dwell_per_alighting_s", w),
        turnaround_s=_need_int(pol, "turnaround_s", w),
        cycle_rest_s=_need_int(pol, "cycle_rest_s", w),
    )
    return Scenario(
        scenario_id=_need_str(raw, "scenario_id", w),
        network_snapshot_id=_need_str(raw, "network_snapshot_id", w),
        start_s=_need_int(window, "start_s", w),
        end_s=_need_int(window, "end_s", w),
        vehicles=vehicles,
        policy=policy,
    )


def parse_passenger(raw: Any, where: str) -> Passenger:
    kind = _need_str(raw, "kind", where)
    return Passenger(
        passenger_id=_need_str(raw, "passenger_id", where),
        kind=kind,
        origin_stop_ids=_str_tuple(raw, "origin_stop_ids", where),
        dest_stop_ids=_str_tuple(raw, "dest_stop_ids", where),
        arrival_s=_need_int(raw, "arrival_s", where),
        max_wait_s=_need_int(raw, "max_wait_s", where),
    )


def parse_arrivals(data: bytes) -> dict[str, Passenger]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidatorInputError("arrivals.jsonl is not UTF-8") from exc
    out: dict[str, Passenger] = {}
    for n, line in enumerate(text.splitlines(), start=1):
        if not line:
            continue
        try:
            raw = json.loads(line)
        except ValueError as exc:
            raise ValidatorInputError(f"arrivals.jsonl line {n} is not JSON") from exc
        p = parse_passenger(raw, f"arrivals.jsonl line {n}")
        if p.passenger_id in out:
            raise ValidatorInputError(f"arrivals.jsonl: duplicate passenger_id {p.passenger_id}")
        out[p.passenger_id] = p
    return out


def parse_travel_times(raw: Any) -> TravelTimes:
    w = "travel_times.json"
    segments: dict[tuple[str, str], Segment] = {}
    for item in _need_list(raw, "segments", w):
        key = (_need_str(item, "from_stop_id", w), _need_str(item, "to_stop_id", w))
        profile = _need_list(item, "profile", w)
        bins, travels = [], []
        for row in profile:
            if not (isinstance(row, list) and len(row) == 2 and is_int(row[0]) and is_int(row[1])):
                raise ValidatorInputError(f"{w}: profile rows must be [int, int]")
            bins.append(row[0])
            travels.append(row[1])
        if bins != sorted(bins) or len(set(bins)) != len(bins):
            raise ValidatorInputError(f"{w}: profile bin_start_s must be strictly increasing")
        if key in segments:
            raise ValidatorInputError(f"{w}: duplicate segment {key}")
        segments[key] = Segment(_need_int(item, "distance_m", w), tuple(bins), tuple(travels))
    return TravelTimes(_need_str(raw, "network_snapshot_id", w), segments)


def parse_plan(raw: Any) -> Plan:
    """운행안 출처(`source`)는 읽지 않는다(§0.2 공정성)."""
    w = "plan.json"
    routes: dict[str, PlanRoute] = {}
    for r in _need_list(raw, "routes", w):
        rid = _need_str(r, "route_id", w)
        legs = []
        for leg in _need_list(r, "legs", w):
            ins = _need(leg, "in_service", w)
            if not isinstance(ins, bool):
                raise ValidatorInputError(f"{w}: leg.in_service must be bool")
            legs.append(Leg(ins, _str_tuple(leg, "stop_ids", w)))
        vids = r.get("vehicle_ids")
        if rid in routes:
            raise ValidatorInputError(f"{w}: duplicate route_id {rid}")
        routes[rid] = PlanRoute(rid, tuple(legs), _need_int(r, "vehicle_count", w),
                                None if vids is None else _str_tuple(r, "vehicle_ids", w))
    return Plan(_need_str(raw, "plan_id", w), _need_str(raw, "scenario_id", w), _need_int(raw, "revision", w), routes)


def parse_timetable(raw: Any) -> Timetable:
    w = "timetable.json"
    routes = []
    for r in _need_list(raw, "routes", w):
        period = r.get("period_s") if isinstance(r, dict) else None
        if period is not None and not is_int(period):
            raise ValidatorInputError(f"{w}: period_s must be int or null")
        routes.append(TimetableRoute(_need_str(r, "route_id", w), period, _str_tuple(r, "vehicle_ids", w)))
    trips = []
    for t in _need_list(raw, "trips", w):
        sts = _need_list(t, "stop_times", w)
        ins = _need(t, "in_service", w)
        if not isinstance(ins, bool):
            raise ValidatorInputError(f"{w}: in_service must be bool")
        if len(sts) < 2:
            raise ValidatorInputError(f"{w}: trip needs at least 2 stop_times")
        trips.append(Trip(
            trip_id=_need_str(t, "trip_id", w),
            route_id=_need_str(t, "route_id", w),
            vehicle_id=_need_str(t, "vehicle_id", w),
            cycle_index=_need_int(t, "cycle_index", w),
            leg_index=_need_int(t, "leg_index", w),
            in_service=ins,
            stop_seqs=tuple(_need_int(s, "stop_seq", w) for s in sts),
            stop_ids=tuple(_need_str(s, "stop_id", w) for s in sts),
            sched_s=tuple(_need_int(s, "sched_s", w) for s in sts),
        ))
    duties = [Duty(_need_str(d, "vehicle_id", w), _need_str(d, "route_id", w), _str_tuple(d, "trip_ids", w),
                   _need_int(d, "start_s", w), _need_int(d, "end_s", w)) for d in _need_list(raw, "duties", w)]
    origin = _need_str(raw, "origin", w)
    by_id: dict[str, Trip] = {}
    for t in trips:
        by_id.setdefault(t.trip_id, t)   # 중복은 V14가 보고한다
    return Timetable(_need_str(raw, "plan_id", w), _need_int(raw, "plan_revision", w), origin,
                     tuple(routes), tuple(trips), tuple(duties), by_id)


def _uri(base: Path, value: Any, name: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValidatorInputError(f"missing URI {name}")
    return base / value


def load_run(run_dir: Path, *, scenario_path: Path | None = None, run_inputs_path: Path | None = None,
             plan_path: Path | None = None) -> RunFiles:
    """한 실행의 아홉 파일을 읽는다(§8.1).

    `run.json`·`timetable.json`·`events.jsonl`·`metrics.json`은 `run_dir`에서(URI는 `run.json` 기준),
    `scenario.json`·`run_inputs.json`·`plan.json`은 따로 주지 않으면 `run_dir`에서 찾는다.
    `arrivals.jsonl`·`travel_times.json`은 `run_inputs.json`의 URI(그 파일 디렉터리 기준)를 따른다.
    """
    run_dir = Path(run_dir)
    paths: dict[str, Path] = {
        "run": run_dir / RUN_FILE,
        "scenario": Path(scenario_path) if scenario_path else run_dir / SCENARIO_FILE,
        "run_inputs": Path(run_inputs_path) if run_inputs_path else run_dir / RUN_INPUTS_FILE,
        "plan": Path(plan_path) if plan_path else run_dir / PLAN_FILE,
    }
    raw: dict[str, bytes] = {k: _read(p) for k, p in paths.items()}
    run = _parse_json(raw["run"], RUN_FILE)
    if not isinstance(run, dict):
        raise ValidatorInputError("run.json must be an object")
    run_inputs = _parse_json(raw["run_inputs"], RUN_INPUTS_FILE)
    if not isinstance(run_inputs, dict):
        raise ValidatorInputError("run_inputs.json must be an object")
    base = paths["run_inputs"].parent
    paths["arrivals"] = _uri(base, run_inputs.get("arrival_realization_uri"), "arrival_realization_uri")
    paths["travel_times"] = _uri(base, run_inputs.get("travel_time_realization_uri"), "travel_time_realization_uri")
    paths["timetable"] = run_dir / (run.get("timetable_uri") or DEFAULT_TIMETABLE_FILE)
    paths["events"] = run_dir / (run.get("event_log_uri") or DEFAULT_EVENTS_FILE)
    paths["metrics"] = run_dir / (run.get("metrics_uri") or DEFAULT_METRICS_FILE)
    for k in ("arrivals", "travel_times", "timetable", "events", "metrics"):
        raw[k] = _read(paths[k])

    return RunFiles(
        paths=paths,
        raw=raw,
        scenario=parse_scenario(_parse_json(raw["scenario"], SCENARIO_FILE)),
        run_inputs=run_inputs,
        passengers=parse_arrivals(raw["arrivals"]),
        travel_times=parse_travel_times(_parse_json(raw["travel_times"], "travel_times.json")),
        plan=parse_plan(_parse_json(raw["plan"], PLAN_FILE)),
        timetable=parse_timetable(_parse_json(raw["timetable"], "timetable.json")),
        metrics=_parse_json(raw["metrics"], "metrics.json"),
        run=run,
        event_lines=raw["events"].split(b"\n"),
    )
