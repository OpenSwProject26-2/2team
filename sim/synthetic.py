"""정확성·규모 시험용 공개 합성 망과 OD×10분 수요 생성기.

관측 카드 자료를 쓰지 않는다. 모든 난수는 (seed, 키)의 SHA-256이라 Python 버전과 무관하게 재현된다.
망은 시험용 fixture일 뿐 서울 실제 공급이 아니다.
구조: 정류장을 원형으로 늘어놓고 회랑(노선 묶음)마다 연속 구간을 잘라 쓴다. 이웃 회랑은 정류장을
공유한다. 회랑마다 갈 때·올 때 노선 2개를 만들고, 운행편은 정류장을 다 돈 뒤 빈 차로 복귀한다.
"""
from dataclasses import dataclass, asdict
import hashlib
import json
from math import ceil
from .arrivals import BinRow
from .models import SimulationError, Trip, integer


@dataclass(frozen=True)
class NetworkSpec:
    """합성 망 설정: 회랑 수, 노선당 정류장, 공유 정류장, 차량, 정원, 시간(초), 거리(m), 운영 시간."""
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
    """합성 수요 설정: 총 인원, 발생 창, bin 폭, 행당 평균 인원, 연결 불가 비율(천분율)."""
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
# 단순 망: 노선 1개·정류장 2개. 처리 속도 상한 측정용이며 수송 현실성은 없다.
SIMPLE = NetworkSpec('simple', 1, 2, 0, False, 4, 45, 300, 0, 2000, 30, 300,
                     6 * HOUR, 22 * HOUR, 1)
# 복잡 망: 공유 정류장이 많은 60회랑 양방향, 노선당 30정류장, 노선당 차량 8대.
COMPLEX = NetworkSpec('complex', 60, 30, 10, True, 8, 45, 120, 30, 500, 30, 300,
                      6 * HOUR, 22 * HOUR, 8)
PRESETS = {'simple': SIMPLE, 'complex': COMPLEX}


def _unit(seed, *key):
    """해시로 만든 [0, 1) 균등 난수."""
    h = hashlib.sha256(json.dumps([seed, *key], separators=(',', ':')).encode()).digest()
    return int.from_bytes(h[:7], 'big') / (1 << 56)


def _pick(seed, n, *key):
    """해시로 고른 0..n-1 정수."""
    return min(int(_unit(seed, *key) * n), n - 1)


def build_network(spec, seed):
    """정류장·노선·운행편·trip_id→노선·정류장→지역(dong)을 담은 dict를 만든다."""
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
    """어떤 노선으로든 갈 수 있는 (출발, 도착) 쌍 집합."""
    pairs = set()
    for seq in routes.values():
        for i, o in enumerate(seq):
            for d in seq[i + 1:]: pairs.add((o, d))
    return pairs


def build_demand(network, spec, seed):
    """정확히 spec.passengers명을 OD×bin 행으로 나눈다. 행마다 인원 편차가 크게 나도록 가중치를 준다."""
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
    # 최대 나머지 방식으로 정수 배분해 총 인원을 정확히 맞춘다.
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
