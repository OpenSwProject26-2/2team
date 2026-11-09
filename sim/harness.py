"""P6 측정 하네스: 합성 시나리오를 만들고 시뮬레이션 1회의 시간·메모리를 단계별로 잰다.

    python -m sim.harness generate --network complex --passengers 100k --out DIR
    python -m sim.harness run --scenario DIR --arrival-seed a1 --policy proposed-v0.2 \
        --sink null --out RUN_DIR [--tracemalloc]

단계: load(파일 읽기) → init(R5 생성·입력 검증·엔진 준비) → run(사건 처리) → finalize(결과·
checksum) → write(metrics.json·결과 CSV). JSONL 로그 쓰기는 run에 포함되므로 null/jsonl 실행을
비교해 분리한다. tracemalloc은 느려지므로 별도 실행으로만 쓴다. 모든 수치는 독립 검증 전 잠정이다.
"""
import argparse
import csv
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import subprocess
import sys
from time import perf_counter, process_time
from datetime import datetime
import tracemalloc
from .arrivals import BinRow, generate, write_realization
from .engine import simulate
from .events import NullSink
from .metrics import MetricsAccumulator, check_max_headway, summarize
from .models import Config, Trip
from .run_inputs import RunInputs
from . import synthetic

PROVISIONAL = '독립 검증 전 잠정 (provisional, not independently verified)'
SCALES = {'1k': 1_000, '10k': 10_000, '100k': 100_000, '900k': 900_000}
# 계획서(experiment-evidence-plan §7)의 제안 가정. 팀 승인 전 값이다.
POLICIES = {'proposed-v0.2': {'max_wait_s': 1200, 'max_journey_s': 3600, 'max_headway_s': 1200,
                              'wait_threshold_s': 1200,
                              'source': 'experiment-evidence-plan §7 proposed baseline (제안 가정)'}}
SERVICE_DATE = '2026-11-09'


def scale(value):
    """'1k'·'10k'·'100k'·'900k' 또는 정수 문자열을 인원 수로 바꾼다."""
    return SCALES[value] if value in SCALES else int(value)


# ---------- 실행 환경 ----------

def _git(*args):
    try:
        return subprocess.run(['git', *args], capture_output=True, text=True, check=True,
                              cwd=Path(__file__).resolve().parents[1]).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _meminfo_total():
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith('MemTotal:'): return int(line.split()[1]) * 1024
    except OSError:
        return None


def env_manifest():
    """커밋·작업 트리 상태·Python·플랫폼·CPU·메모리를 기록한다(재현성 확인용)."""
    try:
        import simpy
        simpy_version = simpy.__version__
    except ImportError:
        simpy_version = None
    status = _git('status', '--porcelain')
    tracked = _git('status', '--porcelain', '--untracked-files=no')
    # dirty: 미추적 파일 포함 모든 변경, dirty_tracked: 추적 파일(코드) 변경만.
    return {'commit': _git('rev-parse', 'HEAD'), 'branch': _git('rev-parse', '--abbrev-ref', 'HEAD'),
            'dirty': bool(status) if status is not None else None,
            'dirty_tracked': bool(tracked) if tracked is not None else None,
            'untracked_files': None if status is None else sum(l.startswith('??') for l in status.splitlines()),
            'python': sys.version.split()[0], 'implementation': platform.python_implementation(),
            'platform': platform.platform(), 'machine': platform.machine(),
            'cpu_count': os.cpu_count(), 'mem_total_bytes': _meminfo_total(), 'simpy': simpy_version,
            'recorded_at': datetime.now().astimezone().isoformat(timespec='seconds')}


# ---------- 메모리 측정 ----------

def _status_bytes(field):
    try:
        for line in Path('/proc/self/status').read_text().splitlines():
            if line.startswith(field + ':'): return int(line.split()[1]) * 1024
    except OSError:
        return None


def peak_rss_bytes():
    """프로세스 시작 후 최대 RSS(bytes)."""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024  # Linux는 KiB 단위


class Stages:
    """mark(이름)를 부를 때마다 직전 mark 이후의 wall·CPU 시간과 현재·최대 RSS를 기록한다."""
    def __init__(self, trace):
        self.trace, self.records = trace, {}
        self.wall, self.cpu = perf_counter(), process_time()

    def mark(self, name):
        wall, cpu = perf_counter(), process_time()
        record = {'wall_s': wall - self.wall, 'cpu_s': cpu - self.cpu,
                  'rss_bytes': _status_bytes('VmRSS'), 'peak_rss_bytes': peak_rss_bytes()}
        if self.trace:
            current, peak = tracemalloc.get_traced_memory()
            record.update(traced_current_bytes=current, traced_peak_bytes=peak)
        self.records[name] = record
        self.wall, self.cpu = perf_counter(), process_time()


# ---------- 로그 출력 ----------

class HashingJsonlSink:
    """JSONL로 쓰면서 로그 bytes와 sha256을 함께 센다(재현성 비교용)."""
    def __init__(self, stream):
        self.stream, self.bytes, self.hash = stream, 0, hashlib.sha256()

    def emit(self, event):
        line = (json.dumps(asdict(event), sort_keys=True) + '\n').encode()
        self.stream.write(line)
        self.bytes += len(line)
        self.hash.update(line)


# ---------- 시나리오 파일 ----------

def write_scenario(out, network, rows, meta):
    """network.json·demand_bins.csv·scenario_manifest.json(파일 sha256 포함)을 쓴다."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    payload = {k: v for k, v in network.items() if k != 'trips'}
    payload['trips'] = [asdict(t) for t in network['trips']]
    (out / 'network.json').write_text(json.dumps(payload, sort_keys=True, separators=(',', ':')))
    with open(out / 'demand_bins.csv', 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f, lineterminator='\n')
        w.writerow(['aggregate_id', 'origin', 'destination', 'bin_start_s', 'bin_width_s', 'passenger_count'])
        for r in rows: w.writerow(list(asdict(r).values()))
    files = {name: hashlib.sha256((out / name).read_bytes()).hexdigest()
             for name in ('network.json', 'demand_bins.csv')}
    manifest = {**meta, 'provenance': 'synthetic; generated by sim.synthetic; no observed card data',
                'rows': len(rows), 'passengers': sum(r.passenger_count for r in rows),
                'trips': len(network['trips']), 'stops': len(network['stops']),
                'stop_visits': sum(len(t.stops) for t in network['trips']), 'sha256': files}
    (out / 'scenario_manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return manifest


def load_scenario(path):
    """write_scenario로 만든 디렉터리를 읽어 (망, 집계 행, manifest)를 돌려준다."""
    path = Path(path)
    network = json.loads((path / 'network.json').read_text())
    network['trips'] = tuple(Trip(**{**t, 'stops': tuple(t['stops']), 'arrivals': tuple(t['arrivals']),
                                      'departures': tuple(t['departures'])}) for t in network['trips'])
    network['routes'] = {k: tuple(v) for k, v in network['routes'].items()}
    with open(path / 'demand_bins.csv', newline='', encoding='utf-8') as f:
        rows = tuple(BinRow(r['aggregate_id'], r['origin'], r['destination'], int(r['bin_start_s']),
                            int(r['bin_width_s']), int(r['passenger_count'])) for r in csv.DictReader(f))
    manifest = json.loads((path / 'scenario_manifest.json').read_text())
    return network, rows, manifest


def generate_scenario(network_name, passengers, out, network_seed, demand_seed, *,
                      window=(7 * 3600, 9 * 3600), bin_width_s=600, mean_per_row=2, unconnected_permille=20):
    """프리셋 망과 07–09시 10분 bin 합성 수요로 시나리오 디렉터리를 만든다."""
    spec = synthetic.PRESETS[network_name]
    network = synthetic.build_network(spec, network_seed)
    dspec = synthetic.DemandSpec(passengers, window[0], window[1], bin_width_s, mean_per_row, unconnected_permille)
    rows = synthetic.build_demand(network, dspec, demand_seed)
    return write_scenario(out, network, rows, {'network': network_name, 'network_seed': network_seed,
                          'demand_seed': demand_seed, 'demand_spec': asdict(dspec)})


# ---------- 측정 실행 ----------

OUTCOME_HEADER = ('passenger_id,origin,destination,time_s,outcome,reason,physical,vehicle_id,'
                  'boarding_s,arrival_s,refused\n')


def outcome_line(s):
    """승객 결과 1명을 CSV 한 줄로."""
    p = s.passenger
    values = (p.passenger_id, p.origin, p.destination, p.time_s, s.outcome, s.reason, s.physical,
              s.vehicle_id, s.boarding_s, s.arrival_s, s.refused)
    return ','.join('' if v is None else ('"' + v.replace('"', '""') + '"' if isinstance(v, str) and
                    (',' in v or '"' in v) else str(v)) for v in values) + '\n'


class OutcomeWriter:
    """결과 CSV를 쓰면서 bytes·sha256을 세고, 필요하면 지표 누적기에도 넘긴다."""
    def __init__(self, path, accumulator=None):
        self.f, self.hash, self.bytes, self.accumulator = open(path, 'wb'), hashlib.sha256(), 0, accumulator
        self._write(OUTCOME_HEADER)

    def _write(self, text):
        data = text.encode()
        self.f.write(data); self.hash.update(data); self.bytes += len(data)

    def __call__(self, state):
        self._write(outcome_line(state))
        if self.accumulator is not None: self.accumulator.add(state)

    def close(self): self.f.close()


def run_measured(scenario, out, *, arrival_seed, policy, sink_kind, trace=False, stream_outcomes=False,
                 save_realization=False):
    """시뮬레이션 1회를 단계별로 측정하고 out에 결과 파일을 쓴다. measurement dict를 돌려준다.

    stream_outcomes=True면 결과를 확정 순서로 바로 쓰고 메모리에 두지 않는다(아니면 ID 순).
    """
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    if trace: tracemalloc.start()
    # 코드 상태는 작업 전에 기록한다. 실행 중 트리가 바뀌면 dirty_at_end로 표시한다.
    env = env_manifest()
    stages = Stages(trace)
    network, rows, scen_manifest = load_scenario(scenario)
    stages.mark('load')
    values = POLICIES[policy]
    window_end = scen_manifest['demand_spec']['window_end_s']
    # drain = 발생 창 끝 + 최대 여정(계획서 v0.2 §7). 가능한 여정을 중간에 자르지 않는다.
    drain_s = window_end + values['max_journey_s']
    config = Config(values['max_wait_s'], values['max_journey_s'], drain_s, 'fifo', 'fixed_segments')
    realization = generate(rows, arrival_seed)
    if save_realization:  # 독립 검증기 입력용(docs/verifier_spec.md). init 시간이 늘어난다.
        with open(out / 'realization.jsonl', 'w', encoding='utf-8') as f: write_realization(realization, f)
    run = RunInputs('synthetic-' + scen_manifest['network'], 'scale-pilot', realization.source_checksum,
                    SERVICE_DATE, realization.passengers, (), arrival_seed)  # 주행시간은 망의 운행편 시각에 이미 들어 있다
    stream = open(out / 'events.jsonl', 'wb') if sink_kind == 'jsonl' else None
    sink = HashingJsonlSink(stream) if stream else NullSink()
    def probe(name):
        if name == 'engine_init':
            stages.mark('init')
        elif name == 'engine_run':
            stages.mark('run')

    accumulator = MetricsAccumulator(wait_threshold_s=values['wait_threshold_s'],
                                     stop_area=network['stop_area']) if stream_outcomes else None
    writer = OutcomeWriter(out / 'passenger_outcomes.csv', accumulator) if stream_outcomes else None
    try:
        result = simulate('synthetic-plan', 1, run, network['trips'], config, sink, probe, writer)
    finally:
        if stream: stream.close()
        if writer: writer.close()
    run_checksum = result.run_checksum
    stages.mark('finalize')
    if writer is None:
        writer = OutcomeWriter(out / 'passenger_outcomes.csv')
        for s in result.passengers: writer(s)
        writer.close()
    outcome_hash, outcome_bytes = writer.hash, writer.bytes
    report = summarize(result, wait_threshold_s=values['wait_threshold_s'], stop_area=network['stop_area'],
                       accumulator=accumulator,
                       headway=check_max_headway(network['trips'], network['route_of'], values['max_headway_s']))
    (out / 'metrics.json').write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
    metrics = {'served': result.served, 'unserved': result.unserved, 'censored': result.censored,
               'total': result.total, 'p90_wait_s': result.p90_wait_s, 'distance_m': result.distance_m,
               'event_count': result.event_count, 'refusal_events': result.refusal_count,
               'unserved_by_reason': report['M2']['unserved_by_reason'], 'R12_pass': report['G']['R12']['pass'],
               'label': PROVISIONAL}
    stages.mark('write')
    measurement = {
        'status': 'ok', 'label': PROVISIONAL, 'scenario': str(scenario),
        'scenario_manifest': scen_manifest, 'arrival_seed': arrival_seed,
        'realization_checksum': realization.checksum, 'run_checksum': run_checksum,
        'config': asdict(config), 'policy': {'name': policy, **values},
        'drain_rule': 'window_end_s + max_journey_s', 'sink': sink_kind, 'tracemalloc': trace,
        'passengers': result.total, 'event_count': result.event_count,
        'log_bytes': sink.bytes if stream else 0, 'log_sha256': sink.hash.hexdigest() if stream else None,
        'outcome_bytes': outcome_bytes, 'outcome_sha256': outcome_hash.hexdigest(),
        'outcome_order': 'finalization' if stream_outcomes else 'passenger_id',
        'stages': stages.records, 'peak_rss_bytes': peak_rss_bytes(),
        'total_wall_s': sum(r['wall_s'] for r in stages.records.values()),
        'total_cpu_s': sum(r['cpu_s'] for r in stages.records.values()),
        'metrics': metrics, 'env': env}  # M1–M6 전체 보고서는 metrics.json
    end_status = _git('status', '--porcelain')
    measurement['env']['dirty_at_end'] = bool(end_status) if end_status is not None else None
    if trace: tracemalloc.stop()
    (out / 'measurement.json').write_text(json.dumps(measurement, indent=2, sort_keys=True))
    (out / 'env_manifest.json').write_text(json.dumps(measurement['env'], indent=2, sort_keys=True))
    return measurement


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m sim.harness')
    sub = parser.add_subparsers(dest='command', required=True)
    g = sub.add_parser('generate', help='write a synthetic scenario directory')
    g.add_argument('--network', choices=sorted(synthetic.PRESETS), required=True)
    g.add_argument('--passengers', type=scale, required=True, help='1k/10k/100k/900k or an integer')
    g.add_argument('--network-seed', default='net-v1')
    g.add_argument('--demand-seed', default='demand-v1')
    g.add_argument('--out', required=True)
    r = sub.add_parser('run', help='measure one simulation in this process')
    r.add_argument('--scenario', required=True)
    r.add_argument('--arrival-seed', required=True)
    r.add_argument('--policy', choices=sorted(POLICIES), required=True)
    r.add_argument('--sink', choices=('null', 'jsonl'), required=True)
    r.add_argument('--tracemalloc', action='store_true')
    r.add_argument('--stream-outcomes', action='store_true', help='P3: write outcomes during run, keep none')
    r.add_argument('--write-realization', action='store_true', help='save realization.jsonl for verification')
    r.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    if args.command == 'generate':
        result = generate_scenario(args.network, args.passengers, args.out, args.network_seed, args.demand_seed)
    else:
        m = run_measured(args.scenario, args.out, arrival_seed=args.arrival_seed, policy=args.policy,
                         sink_kind=args.sink, trace=args.tracemalloc,
                         stream_outcomes=args.stream_outcomes, save_realization=args.write_realization)
        result = {k: m[k] for k in ('status', 'passengers', 'event_count', 'total_wall_s',
                                    'peak_rss_bytes', 'log_bytes', 'outcome_sha256')}
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__': main()
