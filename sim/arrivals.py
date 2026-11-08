"""R5 arrival realization: OD x time-bin aggregates -> per-passenger integer time_s.

Policy (R5, plan v0.2 §3/§7): each aggregate row's passengers are generated
uniformly over the integer seconds [bin_start_s, bin_start_s + bin_width_s).
The bin width is always an explicit input; there is no hidden default.
The tag time is not treated as a stop arrival time and second-level offsets are
a synthetic assumption, not observed precision.

Determinism: offsets come from a counter-based SHA-256 stream keyed by
(generator, seed, aggregate_id, index). Results do not depend on row order,
Python's `random` implementation, or the Python version. Exact uniformity is
kept with rejection sampling. A different seed is *not* guaranteed to give a
different realization (e.g. width 1, zero demand, or a collision).
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
    aggregate_id: str
    origin: str
    destination: str
    bin_start_s: int
    bin_width_s: int
    passenger_count: int


@dataclass(frozen=True)
class ArrivalRealization:
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
    return digest([asdict(r) for r in check_rows(rows)])


def passenger_id(aggregate_id, index):
    """Same ID scheme as run_inputs.expand so exact and R5 modes stay comparable."""
    return json.dumps([aggregate_id, index], separators=(',', ':'))


def _offsets(seed, row):
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
    h = hashlib.sha256(json.dumps([generator, seed, source], separators=(',', ':')).encode())
    for p in passengers:
        h.update(b'\n' + json.dumps([p.passenger_id, p.origin, p.destination, p.time_s],
                                     separators=(',', ':')).encode())
    return h.hexdigest()


def generate(rows, seed):
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
    """Accept a pre-generated realization only if it is consistent with its bins.

    Checks: declared rows (and `rows`, if given) match; every passenger maps to
    exactly one (aggregate_id, index) slot with the row's OD and a time inside
    the bin; per-row counts are exact; checksum recomputes; optional pinned
    checksum; optional regeneration equality for the declared generator.
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
    """JSONL: one metadata line, then one [id, origin, destination, time_s] line per passenger."""
    stream.write(json.dumps({'generator': realization.generator, 'seed': realization.seed,
        'source_checksum': realization.source_checksum, 'checksum': realization.checksum,
        'rows': [asdict(r) for r in realization.rows], 'passenger_count': realization.total},
        sort_keys=True, separators=(',', ':')) + '\n')
    for p in realization.passengers:
        stream.write(json.dumps([p.passenger_id, p.origin, p.destination, p.time_s],
                                separators=(',', ':')) + '\n')


def read_realization(stream, rows=None, *, expected_checksum=None):
    meta = json.loads(stream.readline())
    passengers = tuple(Passenger(*json.loads(line)) for line in stream if line.strip())
    if len(passengers) != meta['passenger_count']:
        raise SimulationError('realization file passenger count mismatch')
    realization = ArrivalRealization(meta['generator'], meta['seed'], meta['source_checksum'],
        tuple(BinRow(**r) for r in meta['rows']), passengers, meta['checksum'])
    return validate_realization(realization, rows, expected_checksum=expected_checksum)
