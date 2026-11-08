"""Public synthetic networks and OD x 10-minute demand for correctness and scale tests.

Nothing here is derived from observed card data. Topology, travel times and
demand weights are SHA-256 hash draws of (seed, key), so every scenario file is
reproducible across Python versions. Networks are *test fixtures*: their supply
is not a realistic Seoul transport capacity and results are not field evidence.

Model: each corridor is a window of a cyclic stop list; neighbouring corridors
share stops. Each corridor yields an outbound and an inbound directional route.
A trip visits its route's stops, then dead-heads back (return_s), matching the
current one-way-visit trip model in sim.models.Trip / opt_adapter.
"""
from dataclasses import dataclass, asdict
import hashlib
import json
from math import ceil
from .arrivals import BinRow
from .models import SimulationError, Trip, integer


@dataclass(frozen=True)
class NetworkSpec:
    name: str
    corridors: int
    stops_per_route: int
    shared_stops: int
    both_directions: bool
    vehicles_per_route: int
    capacity: int
    segment_s: int
    segment_jitter_pct: int
    segment_m: int
    dwell_s: int
    turnaround_s: int
    service_start_s: int
    service_end_s: int
    stops_per_area: int

    def __post_init__(self):
        for v in (self.corridors, self.stops_per_route, self.vehicles_per_route, self.capacity,
                  self.segment_s, self.stops_per_area):
            integer(v, 1)
        for v in (self.shared_stops, self.segment_jitter_pct, self.segment_m, self.dwell_s,
                  self.turnaround_s, self.service_start_s, self.service_end_s):
            integer(v)
        if self.stops_per_route < 2 or self.shared_stops >= self.stops_per_route:
            raise SimulationError('invalid corridor shape')
        if self.segment_jitter_pct >= 100 or self.service_end_s <= self.service_start_s:
            raise SimulationError('invalid timing')


@dataclass(frozen=True)
class DemandSpec:
    passengers: int
    window_start_s: int
    window_end_s: int
    bin_width_s: int
    mean_per_row: int
    unconnected_permille: int

    def __post_init__(self):
        integer(self.passengers); integer(self.window_start_s); integer(self.bin_width_s, 1)
        integer(self.mean_per_row, 1); integer(self.unconnected_permille)
        if self.window_end_s <= self.window_start_s or (self.window_end_s - self.window_start_s) % self.bin_width_s:
            raise SimulationError('window must be a whole number of bins')
        if self.unconnected_permille > 1000: raise SimulationError('invalid permille')


HOUR = 3600
# L1-like: one route, two stops. Processing-bound fixture, not transport realism.
SIMPLE = NetworkSpec('simple', 1, 2, 0, False, 4, 45, 300, 0, 2000, 30, 300,
                     6 * HOUR, 22 * HOUR, 1)
# Many shared stops, both directions, long routes, staggered vehicles.
COMPLEX = NetworkSpec('complex', 60, 30, 10, True, 8, 45, 120, 30, 500, 30, 300,
                      6 * HOUR, 22 * HOUR, 8)
PRESETS = {'simple': SIMPLE, 'complex': COMPLEX}


def _unit(seed, *key):
    """Uniform float in [0,1) from a version-independent hash draw."""
    h = hashlib.sha256(json.dumps([seed, *key], separators=(',', ':')).encode()).digest()
    return int.from_bytes(h[:7], 'big') / (1 << 56)


def _pick(seed, n, *key):
    return min(int(_unit(seed, *key) * n), n - 1)


def build_network(spec, seed):
    step = spec.stops_per_route - spec.shared_stops
    pool = max(spec.stops_per_route, spec.corridors * step + spec.shared_stops)
    width = len(str(pool))
    stops = [f'S{i:0{width}d}' for i in range(pool)]
    routes = {}
    for c in range(spec.corridors):
        seq = tuple(stops[(c * step + k) % pool] for k in range(spec.stops_per_route))
        if len(set(seq)) != len(seq): raise SimulationError('corridor wraps onto itself')
        routes[f'R{c:03d}o'] = seq
        if spec.both_directions: routes[f'R{c:03d}i'] = tuple(reversed(seq))
    trips, route_of = [], {}
    for rid, seq in sorted(routes.items()):
        legs = []
        for k in range(len(seq) - 1):
            jitter = spec.segment_jitter_pct * (2 * _unit(seed, 'seg', seq[k], seq[k + 1]) - 1) / 100
            legs.append(max(1, round(spec.segment_s * (1 + jitter))))
        outbound = sum(legs) + spec.dwell_s * (len(seq) - 1)
        deadhead = sum(legs)
        cycle = outbound + spec.turnaround_s + deadhead + spec.turnaround_s
        for v in range(spec.vehicles_per_route):
            vid = f'{rid}:v{v:02d}'
            t0, n = spec.service_start_s + v * cycle // spec.vehicles_per_route, 0
            while t0 + cycle <= spec.service_end_s:
                arrivals, departures, t = [], [], t0
                for k, stop in enumerate(seq):
                    arrivals.append(t)
                    if k == len(seq) - 1:
                        departures.append(t)
                    else:
                        departures.append(t + spec.dwell_s)
                        t = departures[-1] + legs[k]
                tid = f'{vid}:{n:04d}'
                trips.append(Trip(tid, vid, spec.capacity, seq, tuple(arrivals), tuple(departures),
                                  t0 + cycle, 2 * spec.segment_m * (len(seq) - 1)))
                route_of[tid] = rid
                t0, n = t0 + cycle, n + 1
    area = {s: f'dong-{i // spec.stops_per_area:04d}' for i, s in enumerate(stops)}
    return {'spec': asdict(spec), 'seed': seed, 'stops': stops, 'routes': routes,
            'stop_area': area, 'trips': trips, 'route_of': route_of}


def served_pairs(routes):
    pairs = set()
    for seq in routes.values():
        for i, o in enumerate(seq):
            for d in seq[i + 1:]: pairs.add((o, d))
    return pairs


def build_demand(network, spec, seed):
    """Exactly spec.passengers people in OD x bin rows; heavy-tailed row weights."""
    routes = sorted(network['routes'].items())
    stops, pairs = network['stops'], served_pairs(network['routes'])
    bins = (spec.window_end_s - spec.window_start_s) // spec.bin_width_s
    n_rows = ceil(spec.passengers / spec.mean_per_row) if spec.passengers else 0
    cells, weights = [], []
    for r in range(n_rows):
        b = spec.window_start_s + _pick(seed, bins, 'bin', r) * spec.bin_width_s
        if _pick(seed, 1000, 'unconnected', r) < spec.unconnected_permille:
            for attempt in range(1000):
                o, d = (stops[_pick(seed, len(stops), 'u', r, attempt, x)] for x in 'od')
                if o != d and (o, d) not in pairs: break
            else:
                raise SimulationError('no unconnected pair found')
        else:
            rid, seq = routes[_pick(seed, len(routes), 'route', r)]
            i = _pick(seed, len(seq) - 1, 'o', r)
            j = i + 1 + _pick(seed, len(seq) - 1 - i, 'd', r)
            o, d = seq[i], seq[j]
        cells.append((o, d, b))
        weights.append(1 / (_unit(seed, 'w', r) + 0.05))
    # Largest-remainder allocation: the total is exact, never rounded away.
    total_w = sum(weights)
    raw = [spec.passengers * w / total_w for w in weights]
    counts = [int(x) for x in raw]
    order = sorted(range(n_rows), key=lambda k: (-(raw[k] - counts[k]), k))
    for k in order[:spec.passengers - sum(counts)]: counts[k] += 1
    merged = {}
    for (o, d, b), c in zip(cells, counts):
        if c: merged[(o, d, b)] = merged.get((o, d, b), 0) + c
    rows = tuple(BinRow(f'{o}>{d}@{b}', o, d, b, spec.bin_width_s, c)
                 for (o, d, b), c in sorted(merged.items()))
    if sum(r.passenger_count for r in rows) != spec.passengers:
        raise SimulationError('demand total not preserved')
    return rows
