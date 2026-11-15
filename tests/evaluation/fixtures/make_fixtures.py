"""F1·F2 손계산 fixture의 정상 산출물을 엔진으로 만든다. **검증기 패키지가 아니다.**

이 스크립트만 `sim`을 import한다. 검증기(`src/busstrike/evaluation/`)는 여기서 만든
*파일*만 읽으며 이 스크립트를 import하지 않는다(docs/verifier_spec.md §1 코드 분리).

왜 필요한가: `sim.harness run`의 정책은 `proposed-v0.2`(W=1200·J=3600) 하나뿐이고 drain은
`window_end_s + max_journey_s` 고정이라, 명세 §5의 F1(W=10·J=15·drain=20)과
F2(W=300·J=600·drain=1100)를 CLI로 만들 수 없다. 그래서 같은 출력 writer를 쓰면서
Config만 바꿔 호출한다. 파일 형식은 실제 harness 실행과 같다.

실행 (B worktree에서):

    .venv/bin/python <이 파일> --out <출력 디렉터리>

산출: <out>/<이름>/scenario/{network,demand_bins,scenario_manifest} +
      <out>/<이름>/run/{events.jsonl,passenger_outcomes.csv,metrics.json,
                        measurement.json,realization.jsonl}
"""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

from sim.arrivals import BinRow, generate, write_realization
from sim.engine import simulate
from sim.harness import HashingJsonlSink, OutcomeWriter, write_scenario
from sim.metrics import check_max_headway, summarize
from sim.models import Config, Trip
from sim.run_inputs import RunInputs

SERVICE_DATE = '2026-11-09'
PROVISIONAL = '독립 검증 전 잠정 (provisional, not independently verified)'
WAIT_THRESHOLD_S = 60  # 명세 §5 F2 "M3(임계 60)". F1 표는 임계를 쓰지 않는다.


def trip(trip_id, vehicle_id, capacity, stops, arrivals, departures, return_s, distance_m):
    return Trip(trip_id, vehicle_id, capacity, tuple(stops), tuple(arrivals), tuple(departures),
                return_s, distance_m)


def f1_network():
    """F1 직행 셔틀: A→B 정원 2, 출발 0·10초, 주행 5초(sim/fixtures/direct_shuttle.json).

    distance_m은 명세 §5 F1 표가 제약하지 않는다(M6 거리에만 영향). 0으로 둔다.
    """
    trips = (trip('f1:v1:0000', 'v1', 2, ('A', 'B'), (0, 5), (0, 5), 10, 0),
             trip('f1:v1:0001', 'v1', 2, ('A', 'B'), (10, 15), (10, 15), 20, 0))
    return {'spec': {'name': 'handcalc-f1', 'source': 'docs/verifier_spec.md §5 F1',
                     'provenance': 'synthetic hand-calculation fixture; no observed data'},
            'seed': 'handcalc-f1', 'stops': ['A', 'B'], 'routes': {'r1': ('A', 'B')},
            'stop_area': {'A': 'dong-1', 'B': 'dong-1'}, 'trips': trips,
            'route_of': {'f1:v1:0000': 'r1', 'f1:v1:0001': 'r1'}}


def f1_rows():
    """승객 3명 t=0. bin 폭 1초이므로 생성기 오프셋은 반드시 0이고 ID는 ["d",0..2]다."""
    return (BinRow('d', 'A', 'B', 0, 1, 3),)


def f2_network():
    """F2 지표 fixture: 명세 §5 F2의 t1·t2·t3와 지역·노선 배정."""
    trips = (trip('t1', 'v1', 2, ('A', 'B', 'C'), (100, 200, 300), (110, 210, 300), 400, 1000),
             trip('t2', 'v1', 2, ('A', 'B', 'C'), (700, 800, 900), (710, 810, 900), 1000, 1000),
             trip('t3', 'v2', 5, ('A', 'B', 'C'), (400, 500, 1000), (410, 510, 1000), 1050, 500))
    return {'spec': {'name': 'handcalc-f2', 'source': 'docs/verifier_spec.md §5 F2',
                     'provenance': 'synthetic hand-calculation fixture; no observed data'},
            'seed': 'handcalc-f2', 'stops': ['A', 'B', 'C', 'D'],
            'routes': {'r1': ('A', 'B', 'C'), 'r2': ('A', 'B', 'C')},
            # D는 어떤 운행편도 방문하지 않는다(no_connection 사례용). 지역 매핑은 있다.
            'stop_area': {'A': 'dong-1', 'B': 'dong-1', 'C': 'dong-2', 'D': 'dong-2'},
            'trips': trips, 'route_of': {'t1': 'r1', 't2': 'r1', 't3': 'r2'}}


def f2_rows():
    """명세 §5 F2 표의 승객 9명. bin 폭 1초로 발생 시각을 고정한다."""
    return (BinRow('a', 'A', 'C', 0, 1, 3),      # ["a",0..2]
            BinRow('b', 'B', 'C', 150, 1, 1),
            BinRow('c', 'A', 'D', 0, 1, 1),
            BinRow('d', 'C', 'A', 50, 1, 1),
            BinRow('e', 'A', 'B', 750, 1, 1),
            BinRow('f', 'B', 'C', 820, 1, 1),
            BinRow('g', 'A', 'C', 380, 1, 1))


# (이름, 망 생성, 수요 행, max_wait_s, max_journey_s, drain_s, max_headway_s, 설명)
CASES = (
    ('f1', f1_network, f1_rows, 10, 15, 20, 600, 'F1 기준: 명세 §5 F1 본문 표'),
    ('f1-w9', f1_network, f1_rows, 9, 15, 20, 600, 'F1 변형 W=9 → ["d",2] unserved(wait)'),
    ('f1-j14', f1_network, f1_rows, 10, 14, 20, 600, 'F1 변형 J=14 → ["d",2] unserved(journey), arrival_s 15 보존'),
    ('f1-drain12', f1_network, f1_rows, 10, 15, 12, 600, 'F1 변형 drain=12 → ["d",2] censored(onboard)'),
    ('f1-wj9', f1_network, f1_rows, 9, 9, 20, 600, 'F1 변형 W=J=9 → ["d",2] unserved(journey), refusal_events 1'),
    ('f2', f2_network, f2_rows, 300, 600, 1100, 600, 'F2 기준: 명세 §5 F2 본문 표 (상한 600 → R12 pass)'),
    ('f2-headway599', f2_network, f2_rows, 300, 600, 1100, 599, 'F2 변형 상한 599 → r1 A·B·C 3건 위반'),
)


def build(name, network_fn, rows_fn, max_wait_s, max_journey_s, drain_s, max_headway_s, note, out):
    base = Path(out) / name
    scen_dir, run_dir = base / 'scenario', base / 'run'
    run_dir.mkdir(parents=True, exist_ok=True)
    network, rows = network_fn(), rows_fn()
    window_start = min(r.bin_start_s for r in rows)
    window_end = max(r.bin_start_s + r.bin_width_s for r in rows)
    scen_manifest = write_scenario(scen_dir, network, rows, {
        'network': name, 'network_seed': network['seed'], 'demand_seed': name,
        'note': note, 'source': 'docs/verifier_spec.md §5',
        'demand_spec': {'passengers': sum(r.passenger_count for r in rows),
                        'window_start_s': window_start, 'window_end_s': window_end,
                        'bin_width_s': rows[0].bin_width_s, 'mean_per_row': 1,
                        'unconnected_permille': 0}})

    realization = generate(rows, name)
    with open(run_dir / 'realization.jsonl', 'w', encoding='utf-8') as f:
        write_realization(realization, f)

    config = Config(max_wait_s, max_journey_s, drain_s, 'fifo', 'fixed_segments')
    run = RunInputs('handcalc-' + name, name, realization.source_checksum, SERVICE_DATE,
                    realization.passengers, (), name)

    with open(run_dir / 'events.jsonl', 'wb') as stream:
        sink = HashingJsonlSink(stream)
        result = simulate('handcalc-plan', 1, run, network['trips'], config, sink)

    writer = OutcomeWriter(run_dir / 'passenger_outcomes.csv')
    for s in result.passengers:
        writer(s)
    writer.close()

    report = summarize(result, wait_threshold_s=WAIT_THRESHOLD_S, stop_area=network['stop_area'],
                       headway=check_max_headway(network['trips'], network['route_of'], max_headway_s))
    (run_dir / 'metrics.json').write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))

    measurement = {
        'status': 'ok', 'label': PROVISIONAL, 'scenario': str(Path('..') / 'scenario'),
        'scenario_manifest': scen_manifest, 'arrival_seed': name,
        'realization_checksum': realization.checksum, 'run_checksum': result.run_checksum,
        'config': asdict(config),
        'policy': {'name': 'handcalc-' + name, 'max_wait_s': max_wait_s,
                   'max_journey_s': max_journey_s, 'max_headway_s': max_headway_s,
                   'wait_threshold_s': WAIT_THRESHOLD_S,
                   'source': 'docs/verifier_spec.md §5 hand-calculation fixture'},
        'drain_rule': 'explicit (hand-calculation fixture)', 'sink': 'jsonl', 'tracemalloc': False,
        'passengers': result.total, 'event_count': result.event_count,
        'log_bytes': sink.bytes, 'log_sha256': sink.hash.hexdigest(),
        'outcome_bytes': writer.bytes, 'outcome_sha256': writer.hash.hexdigest(),
        'outcome_order': 'passenger_id', 'note': note,
        'metrics_sha256': hashlib.sha256((run_dir / 'metrics.json').read_bytes()).hexdigest(),
        'realization_sha256': hashlib.sha256((run_dir / 'realization.jsonl').read_bytes()).hexdigest(),
    }
    (run_dir / 'measurement.json').write_text(json.dumps(measurement, indent=2, sort_keys=True))
    return {'name': name, 'passengers': result.total, 'events': result.event_count,
            'served': result.served, 'unserved': result.unserved, 'censored': result.censored}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--out', required=True)
    parser.add_argument('--only', action='append', help='만들 fixture 이름 (반복 가능)')
    args = parser.parse_args(argv)
    summary = [build(*case, args.out) for case in CASES
               if not args.only or case[0] in args.only]
    json.dump(summary, sys.stdout, indent=2)
    print()


if __name__ == '__main__':
    main()
