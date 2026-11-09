"""P4 metrics (M1-M6) and the R12 maximum-headway check.

Definitions are fixed here before any experiment and must be cited with results.

M1  served / total. total = served + unserved + censored (exact, never estimated).
    total == 0 -> NA (None). With censored > 0, report bounds:
    lower = served / total (all censored fail), upper = (served + censored) / total.

M2  Exclusive unserved reason, first matching rule wins (REASON_PRIORITY):
    1. no_connection: no trip in the compiled schedule visits origin and later destination.
    2. service_end:   connected, but no such visit arrives at the origin in [time_s, drain_s].
    3. journey / wait: the deadline event that fixed the outcome. Both deadlines are phase 4;
       at an equal time 'journey:' sorts before 'wait:', so journey wins the tie.
    Full-vehicle refusals are a separate, duplicable count: at each visit that ends full,
    every still-eligible passenger it could have carried counts once. Passengers who
    experienced >= 1 refusal are also counted per outcome. Experiencing a refusal is not
    reported as the cause of failure.

M3  Wait = boarding_s - time_s, integer seconds, for explicitly named populations:
    'served' (selection-biased: excludes abandoners) and 'boarded' (all who boarded,
    including later journey failures and censored riders). Not-boarded unserved riders are
    counted by reason only (their observed wait is bounded by the deadline that removed
    them). Mean, nearest-rank P90, and share with wait > threshold (strict). With
    max_wait_s <= threshold the served exceedance is mechanically 0.

M5  Per area of the origin stop (caller-supplied stop->area map; unmapped -> 'unknown').
    Rate = served / total per area; min over areas with total > 0.

M6  Per vehicle (sim.reporting.VehicleStats). Empty distance: NA, Trip lacks per-segment
    distance (TODO).

G/R12 Consecutive departures of the same route at each stop must be <= max_headway_s.
    TODO(policy): gaps between service start / evaluation window edges and the first or
    last departure are not checked; route identity must be supplied by the caller.
"""
from collections import Counter
from dataclasses import asdict
from math import ceil
from .models import SimulationError, integer

PROVISIONAL = '독립 검증 전 잠정 (provisional, not independently verified)'
REASON_PRIORITY = ('no_connection', 'service_end', 'journey', 'wait')


def _rate(n, d): return None if d == 0 else n / d


class WaitStats:
    """Exact streaming wait statistics via an integer histogram (no per-person list)."""
    def __init__(self, threshold_s):
        self.threshold_s, self.hist, self.n, self.sum = threshold_s, Counter(), 0, 0

    def add(self, wait_s):
        if wait_s < 0: raise SimulationError('negative wait')
        self.hist[wait_s] += 1; self.n += 1; self.sum += wait_s

    def p90(self):
        if not self.n: return None
        rank, seen = ceil(self.n * .9), 0
        for value in sorted(self.hist):
            seen += self.hist[value]
            if seen >= rank: return value

    def summary(self, population):
        over = sum(c for v, c in self.hist.items() if v > self.threshold_s)
        return {'population': population, 'n': self.n,
                'mean_s': None if not self.n else self.sum / self.n, 'p90_s': self.p90(),
                'threshold_s': self.threshold_s, 'over_threshold_n': over,
                'over_threshold_rate': _rate(over, self.n)}


class MetricsAccumulator:
    """Consumes each passenger's final state exactly once (batch or streaming)."""
    def __init__(self, *, wait_threshold_s, stop_area=None):
        integer(wait_threshold_s)
        self.stop_area = stop_area
        self.outcomes, self.reasons, self.censored_physical = Counter(), Counter(), Counter()
        self.refused_by_outcome, self.not_boarded_unserved = Counter(), Counter()
        self.served_wait, self.boarded_wait = WaitStats(wait_threshold_s), WaitStats(wait_threshold_s)
        self.areas = {}

    def add(self, s):
        outcome = s.outcome
        if outcome not in ('served', 'unserved', 'censored'): raise SimulationError('unfinished passenger')
        if (outcome == 'unserved') != (s.reason is not None): raise SimulationError('reason/outcome mismatch')
        self.outcomes[outcome] += 1
        if outcome == 'unserved': self.reasons[s.reason] += 1
        if outcome == 'censored': self.censored_physical[s.physical] += 1
        if s.refused: self.refused_by_outcome[outcome] += 1
        if s.boarding_s is not None:
            wait = s.boarding_s - s.passenger.time_s
            self.boarded_wait.add(wait)
            if outcome == 'served': self.served_wait.add(wait)
        elif outcome == 'unserved':
            self.not_boarded_unserved[s.reason] += 1
        if self.stop_area is not None:
            area = self.stop_area.get(s.passenger.origin, 'unknown')
            self.areas.setdefault(area, Counter())[outcome] += 1


def check_max_headway(trips, route_of, max_headway_s):
    """R12 between consecutive same-route departures at each stop. Returns a G report."""
    integer(max_headway_s, 1)
    departures = {}
    for t in trips:
        if t.trip_id not in route_of: raise SimulationError('trip without route identity')
        for stop, dep in zip(t.stops, t.departures):
            departures.setdefault((route_of[t.trip_id], stop), []).append(dep)
    violations, worst = [], {}
    for (route, stop), deps in sorted(departures.items()):
        deps.sort()
        for a, b in zip(deps, deps[1:]):
            gap = b - a
            worst[route] = max(worst.get(route, 0), gap)
            if gap > max_headway_s:
                violations.append({'route': route, 'stop': stop, 'from_s': a, 'to_s': b, 'gap_s': gap})
    return {'rule': 'R12', 'max_headway_s': max_headway_s, 'pass': not violations,
            'violation_count': len(violations), 'violations': violations[:100],
            'max_gap_by_route_s': dict(sorted(worst.items())),
            'not_checked': 'window-edge gaps (service start/end to first/last departure); TODO(policy)'}


def summarize(result, *, wait_threshold_s, stop_area=None, accumulator=None, headway=None):
    """Build the M1-M6 report. Pass `accumulator` when passengers were streamed (P3)."""
    if accumulator is None:
        accumulator = MetricsAccumulator(wait_threshold_s=wait_threshold_s, stop_area=stop_area)
        for s in result.passengers: accumulator.add(s)
    a = accumulator
    served, unserved, censored = (a.outcomes[k] for k in ('served', 'unserved', 'censored'))
    total = served + unserved + censored
    if (served, unserved, censored, total) != (result.served, result.unserved, result.censored, result.total):
        raise SimulationError('metrics do not match result counts')
    if served and a.served_wait.p90() != result.p90_wait_s:
        raise SimulationError('P90 mismatch with result')
    areas = None
    if a.stop_area is not None:
        areas = {k: {'total': sum(c.values()), 'served': c['served'], 'unserved': c['unserved'],
                     'censored': c['censored'], 'served_rate': _rate(c['served'], sum(c.values()))}
                 for k, c in sorted(a.areas.items())}
        rated = [(v['served_rate'], k) for k, v in areas.items() if v['total']]
        low = min(rated) if rated else None
    return {
        'label': PROVISIONAL,
        'M1': {'served': served, 'unserved': unserved, 'censored': censored, 'total': total,
               'rate': _rate(served, total) if not censored else None,
               'lower': _rate(served, total), 'upper': _rate(served + censored, total),
               'note': 'rate is NA when total=0; with censored>0 report lower/upper only'},
        'M2': {'unserved_by_reason': {k: a.reasons[k] for k in REASON_PRIORITY},
               'reason_priority': list(REASON_PRIORITY),
               'refusal_events': result.refusal_count,
               'passengers_refused_by_outcome': {k: a.refused_by_outcome[k] for k in ('served', 'unserved', 'censored')},
               'censored_by_physical': dict(sorted(a.censored_physical.items()))},
        'M3': {'served': a.served_wait.summary('served (boarded and arrived by deadline)'),
               'boarded': a.boarded_wait.summary('boarded (includes later journey-unserved and censored onboard)'),
               'not_boarded_unserved_by_reason': {k: a.not_boarded_unserved[k] for k in REASON_PRIORITY}},
        'M5': None if areas is None else {'by_origin_area': areas,
               'min_served_rate': None if low is None else {'area': low[1], 'rate': low[0]}},
        'M6': {'vehicles': [asdict(v) for v in result.vehicle_stats],
               'empty_distance_m': None, 'empty_distance_note': 'NA: Trip has no per-segment distance (TODO)'},
        'G': {'R12': headway, 'engine_invariants': 'capacity/conservation/time order enforced by engine '
              '(fail-fast); independent check pending M07'},
    }
