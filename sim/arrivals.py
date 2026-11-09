"""R5 승객 발생: OD×시간 bin 집계를 승객별 정수 초 발생 시각으로 펼친다.

규칙: 각 행의 인원을 [bin_start_s, bin_start_s + bin_width_s) 안의 정수 초에 균등하게 발생시킨다.
bin 폭은 항상 호출자가 정한다. 초 단위 시각은 합성 가정이며 실측 정밀도가 아니다.
결정성: (생성기, seed, 집계 ID, 순번)의 SHA-256으로 오프셋을 정하므로 행 순서·Python 버전과
무관하게 같은 결과가 나온다. seed가 달라도 결과가 같을 수 있다(폭 1초, 0명 등).
"""
from dataclasses import dataclass, asdict
import hashlib
import json
from .models import Passenger, SimulationError, integer
from .run_inputs import digest

GENERATOR = 'uniform_bin_sha256_v1'
_SPAN = 1 << 64


@dataclass(frozen=True)
class BinRow:
    """OD×시간 bin 집계 1행: bin 안에 passenger_count명이 발생한다."""
    aggregate_id: str
    origin: str
    destination: str
    bin_start_s: int
    bin_width_s: int
    passenger_count: int


@dataclass(frozen=True)
class ArrivalRealization:
    """생성된 승객 목록과 출처. checksum으로 같은 realization인지 확인한다."""
    generator: str
    seed: str
    source_checksum: str
    rows: tuple[BinRow, ...]
    passengers: tuple[Passenger, ...]
    checksum: str

    @property
    def total(self): return len(self.passengers)


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise SimulationError('missing identifier')


def check_rows(rows):
    """집계 행을 ID 순으로 정렬하고 ID 중복·빈 값·같은 출도착·정수 범위를 검사한다."""
    rows = tuple(sorted(rows, key=lambda r: r.aggregate_id))
    seen = set()
    for r in rows:
        if not isinstance(r, BinRow): raise SimulationError('expected BinRow')
        for value in (r.aggregate_id, r.origin, r.destination): _text(value)
        if r.aggregate_id in seen: raise SimulationError('duplicate aggregate ID')
        seen.add(r.aggregate_id)
        if r.origin == r.destination: raise SimulationError('origin equals destination')
        integer(r.bin_start_s); integer(r.bin_width_s, 1); integer(r.passenger_count)
    return rows


def source_checksum(rows):
    """집계 행 전체의 checksum."""
    return digest([asdict(r) for r in check_rows(rows)])


def passenger_id(aggregate_id, index):
    """승객 ID ["집계 ID", 순번]. run_inputs.expand와 같은 형식이다."""
    return json.dumps([aggregate_id, index], separators=(',', ':'))


def _offsets(seed, row):
    # 승객마다 bin 안 오프셋(0..폭-1)을 만든다. 2^64를 폭으로 나눈 나머지 구간은 버리고
    # 다시 뽑아(rejection sampling) 모든 초가 정확히 같은 확률이 되게 한다.
    width = row.bin_width_s
    limit = _SPAN - _SPAN % width
    base = hashlib.sha256(json.dumps([GENERATOR, seed, row.aggregate_id]).encode() + b'\0')
    for index in range(row.passenger_count):
        counter = 0
        while True:
            h = base.copy()
            h.update(index.to_bytes(8, 'big') + counter.to_bytes(4, 'big'))
            x = int.from_bytes(h.digest()[:8], 'big')
            if x < limit:
                yield x % width
                break
            counter += 1


def realization_checksum(generator, seed, source, passengers):
    """생성기·seed·집계 checksum과 승객 목록(순서 포함)의 checksum."""
    h = hashlib.sha256(json.dumps([generator, seed, source], separators=(',', ':')).encode())
    for p in passengers:
        h.update(b'\n' + json.dumps([p.passenger_id, p.origin, p.destination, p.time_s],
                                     separators=(',', ':')).encode())
    return h.hexdigest()


def generate(rows, seed):
    """집계 행과 seed로 realization을 만든다. 승객 순서는 집계 ID 순, 그 안에서 순번 순."""
    _text(seed)
    rows = check_rows(rows)
    passengers = []
    for r in rows:
        for index, offset in enumerate(_offsets(seed, r)):
            passengers.append(Passenger(passenger_id(r.aggregate_id, index), r.origin,
                                        r.destination, r.bin_start_s + offset))
    passengers = tuple(passengers)
    source = source_checksum(rows)
    return ArrivalRealization(GENERATOR, seed, source, rows, passengers,
                              realization_checksum(GENERATOR, seed, source, passengers))


def validate_realization(realization, rows=None, *, expected_checksum=None, regenerate=False):
    """미리 만든 realization이 집계 행과 맞는지 검사하고 맞으면 그대로 돌려준다.

    검사: 행 일치, 승객마다 (집계 ID, 순번) 하나에 대응, OD 일치, 시각이 bin 안, 행별 인원
    정확, checksum 일치. expected_checksum을 주면 그 값과도 비교하고, regenerate=True면
    같은 seed로 다시 생성한 결과와도 비교한다. bin 안이면 다른 방식으로 만든 것도 받는다.
    """
    declared = check_rows(realization.rows)
    if rows is not None and check_rows(rows) != declared:
        raise SimulationError('realization rows differ from expected aggregate rows')
    if source_checksum(declared) != realization.source_checksum:
        raise SimulationError('realization source checksum mismatch')
    by_id = {r.aggregate_id: r for r in declared}
    seen, counts = set(), dict.fromkeys(by_id, 0)
    for p in realization.passengers:
        try:
            key, index = json.loads(p.passenger_id)
        except (TypeError, ValueError) as exc:
            raise SimulationError('unparseable passenger ID') from exc
        row = by_id.get(key)
        if row is None or type(index) is not int or not 0 <= index < row.passenger_count:
            raise SimulationError('passenger outside aggregate rows')
        if p.passenger_id in seen or p.passenger_id != passenger_id(key, index):
            raise SimulationError('duplicate or non-canonical passenger ID')
        seen.add(p.passenger_id)
        if (p.origin, p.destination) != (row.origin, row.destination):
            raise SimulationError('passenger OD differs from aggregate row')
        integer(p.time_s)
        if not row.bin_start_s <= p.time_s < row.bin_start_s + row.bin_width_s:
            raise SimulationError('passenger time outside bin')
        counts[key] += 1
    if any(counts[k] != r.passenger_count for k, r in by_id.items()):
        raise SimulationError('aggregate passenger count not preserved')
    actual = realization_checksum(realization.generator, realization.seed,
                                  realization.source_checksum, realization.passengers)
    if actual != realization.checksum:
        raise SimulationError('realization checksum mismatch')
    if expected_checksum is not None and actual != expected_checksum:
        raise SimulationError('realization differs from pinned checksum')
    if regenerate:
        if realization.generator != GENERATOR:
            raise SimulationError('cannot regenerate unknown generator')
        if generate(declared, realization.seed).checksum != actual:
            raise SimulationError('realization differs from regenerated output')
    return realization


def write_realization(realization, stream):
    """JSONL로 저장: 첫 줄 메타데이터, 이후 승객마다 [ID, 출발, 도착, time_s] 한 줄."""
    stream.write(json.dumps({'generator': realization.generator, 'seed': realization.seed,
        'source_checksum': realization.source_checksum, 'checksum': realization.checksum,
        'rows': [asdict(r) for r in realization.rows], 'passenger_count': realization.total},
        sort_keys=True, separators=(',', ':')) + '\n')
    for p in realization.passengers:
        stream.write(json.dumps([p.passenger_id, p.origin, p.destination, p.time_s],
                                separators=(',', ':')) + '\n')


def read_realization(stream, rows=None, *, expected_checksum=None):
    """write_realization 파일을 읽고 validate_realization으로 검사한다."""
    meta = json.loads(stream.readline())
    passengers = tuple(Passenger(*json.loads(line)) for line in stream if line.strip())
    if len(passengers) != meta['passenger_count']:
        raise SimulationError('realization file passenger count mismatch')
    realization = ArrivalRealization(meta['generator'], meta['seed'], meta['source_checksum'],
        tuple(BinRow(**r) for r in meta['rows']), passengers, meta['checksum'])
    return validate_realization(realization, rows, expected_checksum=expected_checksum)
