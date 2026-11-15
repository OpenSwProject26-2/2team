"""엔진 산출물 파일 읽기. 표준 라이브러리만 쓰고 엔진 코드는 쓰지 않는다.

입력 경로는 명세 §2를 따른다. 엔진을 호출해 얻은 값을 정답으로 쓰지 않는다.

일정은 엔진처럼 운행편별 (stops, arrivals, departures) 튜플 3개로 두지 않고
**방문(Visit) 평면 목록**으로 바꿔 둔다. 검사 대부분이 "이 방문에서 무슨 일이 있었나"를
묻기 때문이고, 엔진과 다른 자료구조를 쓰는 것이 명세 §1·§4의 요구다.
"""
import csv
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

SCENARIO_FILES = ('network.json', 'demand_bins.csv', 'scenario_manifest.json')
RUN_FILES = ('events.jsonl', 'passenger_outcomes.csv', 'metrics.json', 'measurement.json',
             'realization.jsonl')


class InputError(Exception):
    """입력 파일이 없거나 형식이 계약과 다르다. 검사 실패와 구분한다."""


# ---------- 자료형 ----------

@dataclass(frozen=True)
class Visit:
    """운행편 1회의 정류장 방문 1건. stable_id는 `<trip_id>:<index 8자리 0채움>`."""
    trip_id: str
    index: int
    stop: str
    arrival_s: int
    departure_s: int

    @property
    def stable_id(self):
        return f'{self.trip_id}:{self.index:08d}'


@dataclass(frozen=True)
class TripInfo:
    trip_id: str
    vehicle_id: str
    capacity: int
    return_s: int
    distance_m: int
    stops: tuple


@dataclass(frozen=True)
class Event:
    """사건 로그 1줄. line_no는 1부터(위반 보고에 쓴다)."""
    line_no: int
    time_s: int
    phase: int
    stable_id: str
    kind: str
    passenger_id: str
    vehicle_id: str
    occupancy: int

    @property
    def key(self):
        return (self.time_s, self.phase, self.stable_id)


@dataclass(frozen=True)
class Outcome:
    """passenger_outcomes.csv 1줄."""
    passenger_id: str
    origin: str
    destination: str
    time_s: int
    outcome: str
    reason: str
    physical: str
    vehicle_id: str
    boarding_s: int
    arrival_s: int
    refused: int


@dataclass(frozen=True)
class BinRow:
    """demand_bins.csv 1줄."""
    aggregate_id: str
    origin: str
    destination: str
    bin_start_s: int
    bin_width_s: int
    passenger_count: int


# ---------- 읽기 도우미 ----------

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def _int(value, field, where):
    """정수만 받는다. 엔진 계약이 정수 초·정수 인원이므로 실수·문자열을 묵인하지 않는다."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise InputError(f'{where}: {field}가 정수가 아니다: {value!r}')
    return value


def _opt(value):
    """CSV의 빈 칸을 None으로."""
    return None if value == '' else value


def _opt_int(value, field, where):
    if value in ('', None):
        return None
    try:
        return int(value)
    except ValueError:
        raise InputError(f'{where}: {field}가 정수가 아니다: {value!r}') from None


# ---------- 시나리오 ----------

class Scenario:
    """scenario/ 디렉터리. 일정·지역·노선 배정과 집계 수요 행."""

    def __init__(self, path):
        self.path = Path(path)
        for name in SCENARIO_FILES:
            if not (self.path / name).is_file():
                raise InputError(f'시나리오 파일 없음: {self.path / name}')
        raw = json.loads((self.path / 'network.json').read_text())
        self.manifest = json.loads((self.path / 'scenario_manifest.json').read_text())
        self.stop_area = dict(raw.get('stop_area') or {})
        self.route_of = dict(raw.get('route_of') or {})
        self.routes = {k: tuple(v) for k, v in (raw.get('routes') or {}).items()}
        self.stops = tuple(raw.get('stops') or ())

        self.trips = {}
        self.visits_by_trip = {}
        for entry in raw['trips']:
            where = f"network.json trips[{entry.get('trip_id')!r}]"
            stops = tuple(entry['stops'])
            arrivals, departures = tuple(entry['arrivals']), tuple(entry['departures'])
            if not (len(stops) == len(arrivals) == len(departures)):
                raise InputError(f'{where}: stops·arrivals·departures 길이가 다르다')
            trip = TripInfo(entry['trip_id'], entry['vehicle_id'],
                            _int(entry['capacity'], 'capacity', where),
                            _int(entry['return_s'], 'return_s', where),
                            _int(entry['distance_m'], 'distance_m', where), stops)
            if trip.trip_id in self.trips:
                raise InputError(f'{where}: trip_id 중복')
            self.trips[trip.trip_id] = trip
            self.visits_by_trip[trip.trip_id] = tuple(
                Visit(trip.trip_id, i, stops[i],
                      _int(arrivals[i], f'arrivals[{i}]', where),
                      _int(departures[i], f'departures[{i}]', where))
                for i in range(len(stops)))

        self.visits = {(v.trip_id, v.index): v
                       for visits in self.visits_by_trip.values() for v in visits}
        self.visit_by_stable_id = {v.stable_id: v for v in self.visits.values()}

        with open(self.path / 'demand_bins.csv', newline='', encoding='utf-8') as f:
            self.bin_rows = tuple(
                BinRow(r['aggregate_id'], r['origin'], r['destination'],
                       int(r['bin_start_s']), int(r['bin_width_s']), int(r['passenger_count']))
                for r in csv.DictReader(f))

    @property
    def file_digests(self):
        return {name: sha256_file(self.path / name) for name in SCENARIO_FILES}

    def visits_of_stop(self, trip_id, stop):
        """운행편 trip_id가 stop을 방문하는 index 목록(순환 노선 대비 복수 가능)."""
        return tuple(v.index for v in self.visits_by_trip.get(trip_id, ()) if v.stop == stop)


# ---------- 실행 결과 ----------

class Run:
    """run/ 디렉터리. 사건 로그·승객 결과·지표·측정 기록·발생 입력."""

    def __init__(self, path, require_realization=True):
        self.path = Path(path)
        needed = RUN_FILES if require_realization else tuple(
            n for n in RUN_FILES if n != 'realization.jsonl')
        for name in needed:
            if not (self.path / name).is_file():
                raise InputError(f'실행 결과 파일 없음: {self.path / name}')
        self.measurement = json.loads((self.path / 'measurement.json').read_text())
        self.metrics = json.loads((self.path / 'metrics.json').read_text())
        config = self.measurement.get('config') or {}
        for field in ('max_wait_s', 'max_journey_s', 'drain_s'):
            if field not in config:
                raise InputError(f'measurement.json config에 {field}가 없다')
        self.max_wait_s = _int(config['max_wait_s'], 'max_wait_s', 'measurement.json config')
        self.max_journey_s = _int(config['max_journey_s'], 'max_journey_s', 'measurement.json config')
        self.drain_s = _int(config['drain_s'], 'drain_s', 'measurement.json config')
        self.has_realization = (self.path / 'realization.jsonl').is_file()
        self._outcomes = None

    # -- 사건 로그: 크므로 항상 스트리밍으로 읽는다 --
    def iter_events(self):
        with open(self.path / 'events.jsonl', encoding='utf-8') as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise InputError(f'events.jsonl:{line_no}: JSON 아님 ({exc})') from None
                where = f'events.jsonl:{line_no}'
                missing = {'time_s', 'phase', 'stable_id', 'kind'} - raw.keys()
                if missing:
                    raise InputError(f'{where}: 필드 없음 {sorted(missing)}')
                yield Event(line_no,
                            _int(raw['time_s'], 'time_s', where),
                            _int(raw['phase'], 'phase', where),
                            raw['stable_id'], raw['kind'],
                            raw.get('passenger_id'), raw.get('vehicle_id'),
                            None if raw.get('occupancy') is None
                            else _int(raw['occupancy'], 'occupancy', where))

    # -- 승객 결과 --
    @property
    def outcomes(self):
        """passenger_id → Outcome. 승객 수는 규모 상한(1만) 안이라 메모리에 올린다."""
        if self._outcomes is None:
            self._outcomes = {}
            with open(self.path / 'passenger_outcomes.csv', newline='', encoding='utf-8') as f:
                for row_no, r in enumerate(csv.DictReader(f), 2):
                    where = f'passenger_outcomes.csv:{row_no}'
                    pid = r['passenger_id']
                    if pid in self._outcomes:
                        raise InputError(f'{where}: passenger_id 중복 {pid!r}')
                    self._outcomes[pid] = Outcome(
                        pid, r['origin'], r['destination'],
                        _opt_int(r['time_s'], 'time_s', where), r['outcome'],
                        _opt(r['reason']), _opt(r['physical']), _opt(r['vehicle_id']),
                        _opt_int(r['boarding_s'], 'boarding_s', where),
                        _opt_int(r['arrival_s'], 'arrival_s', where),
                        _opt_int(r['refused'], 'refused', where))
        return self._outcomes

    # -- 발생 입력 --
    def realization(self):
        """(메타 dict, [(passenger_id, origin, destination, time_s), ...])."""
        if not self.has_realization:
            raise InputError(f'realization.jsonl 없음: {self.path}')
        passengers = []
        with open(self.path / 'realization.jsonl', encoding='utf-8') as f:
            meta = json.loads(f.readline())
            for line_no, line in enumerate(f, 2):
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if not isinstance(row, list) or len(row) != 4:
                    raise InputError(f'realization.jsonl:{line_no}: [pid, origin, destination, time_s] 아님')
                passengers.append((row[0], row[1], row[2],
                                   _int(row[3], 'time_s', f'realization.jsonl:{line_no}')))
        return meta, passengers

    @property
    def file_digests(self):
        return {name: sha256_file(self.path / name)
                for name in RUN_FILES if (self.path / name).is_file()}


# ---------- 경로 해석 ----------

def resolve(run_dir, scenario_dir=None):
    """(Scenario, Run)을 만든다.

    scenario 위치는 ① --scenario 지정, ② <run>/../scenario, ③ <run> 옆의 scenario/,
    ④ measurement.json의 'scenario' 기록(상대경로는 run 기준) 순서로 찾는다.
    """
    run_path = Path(run_dir).resolve()
    if not run_path.is_dir():
        raise InputError(f'실행 결과 디렉터리가 아니다: {run_path}')
    # realization.jsonl은 --write-realization 없이 실행하면 없다. V1은 demand_bins.csv와
    # seed로 발생을 다시 만들 수 있으므로 필수로 두지 않는다(명세 §2의 (b) 경로).
    run = Run(run_path, require_realization=False)

    candidates = []
    if scenario_dir:
        candidates.append(Path(scenario_dir))
    candidates.append(run_path.parent / 'scenario')
    recorded = run.measurement.get('scenario')
    if recorded:
        candidates.extend([Path(recorded), (run_path / recorded)])

    for candidate in candidates:
        candidate = Path(candidate)
        if not candidate.is_absolute():
            candidate = (run_path / candidate).resolve()
        if all((candidate / n).is_file() for n in SCENARIO_FILES):
            return Scenario(candidate), run
    raise InputError('scenario 디렉터리를 찾지 못했다. --scenario로 지정하라. '
                     f'찾아본 곳: {[str(c) for c in candidates]}')
