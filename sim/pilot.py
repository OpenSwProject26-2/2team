"""Scale pilot: run guarded harness measurements in ascending scale order.

    python -m sim.pilot --network complex --scales 1k,10k,100k,900k --repeat 3 \
        --modes batch,stream --timeout-s 1800 --mem-limit-mb 16000 \
        --csv results/scale_pilot.csv --failures results/failures.jsonl

Every cell gets a CSV row, including TIMEOUT / MEMORY_LIMIT / EXCEPTION / NA. After a
failure at one scale, larger scales of the same mode are recorded as NA (not run)
unless --continue-on-failure. Rows are measurements only; nothing is extrapolated.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys
from .guard import PROVISIONAL, record_na, run_guarded
from .harness import generate_scenario, scale

FIELDS = ('scale', 'passengers', 'network', 'mode', 'sink', 'rep', 'status', 'rows', 'trips', 'stop_visits',
          'event_count', 'total_wall_s', 'total_cpu_s', 'load_wall_s', 'init_wall_s', 'run_wall_s',
          'finalize_wall_s', 'write_wall_s', 'peak_rss_mib', 'guard_max_sampled_rss_mib', 'log_bytes',
          'outcome_bytes', 'metrics_sha256', 'served', 'unserved', 'censored', 'commit', 'dirty', 'dirty_tracked',
          'python', 'error_type', 'note', 'label')


def _mib(x): return None if x is None else round(x / 2**20, 1)


def row_from(cell, guard, measurement=None, metrics_sha=None):
    row = dict.fromkeys(FIELDS)
    row.update(cell, status=guard['status'], label=PROVISIONAL,
               guard_max_sampled_rss_mib=_mib(guard.get('max_sampled_rss_bytes')),
               error_type=guard.get('error_type'), note=guard.get('message') or guard.get('reason'))
    if measurement:
        stages, sm = measurement['stages'], measurement['scenario_manifest']
        env = measurement['env']
        row.update(rows=sm['rows'], trips=sm['trips'], stop_visits=sm['stop_visits'],
                   event_count=measurement['event_count'], total_wall_s=round(measurement['total_wall_s'], 3),
                   total_cpu_s=round(measurement['total_cpu_s'], 3),
                   **{f'{k}_wall_s': round(stages[k]['wall_s'], 3) for k in ('load', 'init', 'run', 'finalize', 'write')},
                   peak_rss_mib=_mib(measurement['peak_rss_bytes']), log_bytes=measurement['log_bytes'],
                   outcome_bytes=measurement['outcome_bytes'], metrics_sha256=metrics_sha,
                   served=measurement['metrics']['served'], unserved=measurement['metrics']['unserved'],
                   censored=measurement['metrics']['censored'], commit=(env['commit'] or '')[:12],
                   dirty=env['dirty'], dirty_tracked=env.get('dirty_tracked'), python=env['python'])
    return row


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m sim.pilot')
    parser.add_argument('--network', required=True)
    parser.add_argument('--scales', required=True, help='comma list in ascending order, e.g. 1k,10k,100k,900k')
    parser.add_argument('--repeat', type=int, required=True)
    parser.add_argument('--modes', default='stream', help='batch and/or stream (P3 outcome streaming)')
    parser.add_argument('--sink', choices=('null', 'jsonl'), default='null')
    parser.add_argument('--arrival-seed', default='a1')
    parser.add_argument('--policy', default='proposed-v0.2')
    parser.add_argument('--timeout-s', type=float, required=True)
    parser.add_argument('--mem-limit-mb', type=int, required=True)
    parser.add_argument('--mem-mode', default='rss', choices=('rss', 'as', 'both'))
    parser.add_argument('--work', default='results')
    parser.add_argument('--csv', required=True)
    parser.add_argument('--failures', required=True)
    parser.add_argument('--continue-on-failure', action='store_true')
    args = parser.parse_args(argv)
    scales = [s.strip() for s in args.scales.split(',')]
    sizes = [scale(s) for s in scales]
    if sizes != sorted(sizes): parser.error('scales must ascend')
    work = Path(args.work)
    out_csv = Path(args.csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    new = not out_csv.exists()
    with open(out_csv, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, lineterminator='\n')
        if new: writer.writeheader()
        for mode in [m.strip() for m in args.modes.split(',')]:
            failed_at = None
            for name, n in zip(scales, sizes):
                scenario = work / 'scenarios' / f'{args.network}-{name}'
                if not (scenario / 'scenario_manifest.json').exists():
                    generate_scenario(args.network, n, scenario, 'net-v1', 'demand-v1')
                for rep in range(1, args.repeat + 1):
                    cell = {'scale': name, 'passengers': n, 'network': args.network, 'mode': mode,
                            'sink': args.sink, 'rep': rep}
                    if failed_at and not args.continue_on_failure:
                        guard = record_na(args.failures, f'{args.network}-{name}-{mode}-r{rep}',
                                          f'not run: {failed_at} failed in this mode', cell)
                        writer.writerow(row_from(cell, guard)); f.flush()
                        continue
                    run_dir = work / 'runs' / f'pilot-{args.network}-{name}-{mode}-{args.sink}-r{rep}'
                    cmd = [sys.executable, '-m', 'sim.harness', 'run', '--scenario', str(scenario),
                           '--arrival-seed', args.arrival_seed, '--policy', args.policy, '--sink', args.sink,
                           '--out', str(run_dir)] + (['--stream-outcomes'] if mode == 'stream' else [])
                    guard = run_guarded(cmd, timeout_s=args.timeout_s, mem_limit_bytes=args.mem_limit_mb * 2**20,
                                        mem_mode=args.mem_mode, failures_path=args.failures,
                                        label=f'{args.network}-{name}-{mode}-r{rep}', context=cell,
                                        log_dir=run_dir / 'guard')
                    measurement = metrics_sha = None
                    if guard['status'] == 'OK':
                        measurement = json.loads((run_dir / 'measurement.json').read_text())
                        metrics_sha = hashlib.sha256((run_dir / 'metrics.json').read_bytes()).hexdigest()[:16]
                    else:
                        failed_at = failed_at or name
                    writer.writerow(row_from(cell, guard, measurement, metrics_sha)); f.flush()
                    print(json.dumps({k: v for k, v in row_from(cell, guard, measurement, metrics_sha).items()
                                      if k in ('scale', 'mode', 'rep', 'status', 'total_wall_s', 'peak_rss_mib')}),
                          flush=True)


if __name__ == '__main__': main()
