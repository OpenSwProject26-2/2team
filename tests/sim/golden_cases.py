"""Scenarios whose event logs were recorded with the pre-P2 engine (commit 39d730f).

The engine must reproduce these logs byte-for-byte after queue/memory refactors.
Regenerate only for an intentional, documented behaviour change:
    .venv/bin/python -m tests.sim.golden_cases --write
"""
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import sys
from sim.arrivals import generate
from sim.engine import simulate
from sim.events import JsonlSink
from sim.models import Config, Trip
from sim.run_inputs import RunInputs, expand
from sim import synthetic
from sim.adapters.opt_adapter import prepare_inputs, compile_schedule
from opt.models import load_inputs, make_plan

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = Path(__file__).resolve().parent / 'golden'
FULL_LOG_LIMIT = 400  # store complete logs for small cases, hashes for all

GOLDEN_SPEC = synthetic.NetworkSpec('golden', 6, 12, 4, True, 3, 10, 90, 30, 400, 20, 60,
                                    6 * 3600, 12 * 3600, 5)


def direct_cases():
    f = json.loads((ROOT / 'sim/fixtures/direct_shuttle.json').read_text())
    run = RunInputs('bundle', 'scenario', 'source', f['service_date'],
                    expand([('d', 'A', 'B', f['arrival_s'], f['passenger_count'])]), (('ab', 5), ('ba', 5)), 'fixture')
    base = Config(f['max_wait_s'], f['max_journey_s'], f['drain_s'], 'fifo', 'fixed_segments')
    trips = tuple(Trip(str(i), 'v', f['capacity'], ('A', 'B'), (t, t + 5), (t, t + 5), t + 10, 100)
                  for i, t in enumerate(f['departures']))
    for name, changes in (('direct', {}), ('direct-wait9', {'max_wait_s': 9}),
                          ('direct-journey14', {'max_journey_s': 14}), ('direct-drain12', {'drain_s': 12})):
        yield name, run, trips, replace(base, **changes)


def tie_case():
    """Hand-built ties: same-time passengers, two trips at one stop at once, shared stops,
    capacity binding, journey deadline shorter than wait, competing destinations."""
    passengers = expand([('a', 'A', 'C', 0, 3), ('b', 'A', 'B', 0, 2), ('c', 'B', 'C', 10, 3),
                         ('d', 'A', 'D', 5, 2), ('e', 'C', 'A', 0, 1), ('f', 'A', 'C', 40, 2)])
    run = RunInputs('tie', 'tie', 'tie', '2026-11-09', passengers, (), 'tie')
    trips = (Trip('t1', 'v1', 2, ('A', 'B', 'C'), (10, 20, 30), (12, 22, 30), 60, 10),
             Trip('t2', 'v2', 3, ('A', 'B', 'D'), (10, 20, 30), (12, 22, 30), 60, 10),
             Trip('t3', 'v1', 2, ('A', 'B', 'C'), (60, 70, 80), (62, 72, 80), 110, 10),
             Trip('t4', 'v3', 1, ('B', 'C'), (20, 25), (20, 25), 40, 10))
    yield 'tie', run, trips, Config(50, 65, 200, 'fifo', 'fixed_segments')
    yield 'tie-journey-short', run, trips, Config(50, 15, 200, 'fifo', 'fixed_segments')


def adapter_cases():
    x = load_inputs(ROOT / 'C.Part/opt/examples/synthetic.json')
    config = Config(x.policy.max_wait_s, x.policy.max_travel_s, 2000, 'fifo', 'fixed_segments')
    for alloc in ({'r1': 1}, {'r1': 1, 'r2': 1}):
        plan = make_plan(x, alloc, 'golden')
        run = prepare_inputs(x, seed='fixed')
        yield 'adapter-exact-' + '-'.join(sorted(alloc)), run, compile_schedule(plan, x, run), config


def synthetic_cases():
    net = synthetic.build_network(GOLDEN_SPEC, 'golden-net')
    for n, seed in ((600, 'g1'), (3000, 'g2')):
        rows = synthetic.build_demand(net, synthetic.DemandSpec(n, 7 * 3600, 8 * 3600, 600, 2, 30), 'golden-' + seed)
        real = generate(rows, seed)
        run = RunInputs('golden', 'golden', real.source_checksum, '2026-11-09', real.passengers, (), seed)
        yield f'synthetic-{n}', run, net['trips'], Config(900, 1800, 8 * 3600 + 1800, 'fifo', 'fixed_segments')
    simple = synthetic.build_network(synthetic.SIMPLE, 'golden-net')
    rows = synthetic.build_demand(simple, synthetic.DemandSpec(800, 7 * 3600, 8 * 3600, 600, 2, 30), 'golden-s')
    real = generate(rows, 's')
    run = RunInputs('golden', 'golden', real.source_checksum, '2026-11-09', real.passengers, (), 's')
    yield 'simple-800', run, simple['trips'], Config(1200, 3600, 8 * 3600 + 3600, 'fifo', 'fixed_segments')


def cases():
    yield from direct_cases()
    yield from tie_case()
    yield from adapter_cases()
    yield from synthetic_cases()


def outcome_digest(result):
    h = hashlib.sha256()
    for s in result.passengers:
        h.update(json.dumps([s.passenger.passenger_id, s.outcome, s.physical, s.vehicle_id,
                             s.boarding_s, s.arrival_s]).encode() + b'\n')
    return h.hexdigest()


def record(name, run, trips, config):
    stream = io.StringIO()
    result = simulate('golden', 1, run, trips, config, JsonlSink(stream))
    log = stream.getvalue()
    return log, {'event_count': result.event_count, 'log_sha256': hashlib.sha256(log.encode()).hexdigest(),
                 'outcome_sha256': outcome_digest(result), 'served': result.served,
                 'unserved': result.unserved, 'censored': result.censored, 'p90_wait_s': result.p90_wait_s,
                 'distance_m': result.distance_m, 'passengers': result.total}


def write():
    GOLDEN.mkdir(exist_ok=True)
    summary = {}
    for name, *case in cases():
        log, summary[name] = record(name, *case)
        if summary[name]['event_count'] <= FULL_LOG_LIMIT:
            (GOLDEN / f'{name}.jsonl').write_text(log)
    (GOLDEN / 'golden.json').write_text(json.dumps(summary, indent=2, sort_keys=True) + '\n')
    return summary


if __name__ == '__main__' and '--write' in sys.argv:
    for k, v in write().items(): print(k, v['event_count'], v['served'], v['unserved'], v['censored'])
