"""P7 resource guard: run one measurement command under timeout and memory limits.

    python -m sim.guard --timeout-s 600 --mem-limit-mb 8000 --mem-mode rss \
        --failures results/failures.jsonl --label complex-100k -- \
        python -m sim.harness run ...

Statuses: OK, TIMEOUT, MEMORY_LIMIT, EXCEPTION, NA (cell intentionally not run).
Every non-OK outcome is appended to failures.jsonl; nothing is dropped.

Memory modes (they measure different things, see plan v0.2 §8):
- rss: the parent samples the child's VmRSS every poll_s and kills it above the
  limit. Spikes shorter than one poll interval can pass unnoticed; the sampled
  maximum is recorded.
- as: RLIMIT_AS (virtual address space) inside the child. Allocation fails with
  MemoryError; address space is larger than RSS, so this limit is stricter.
- both: apply both.
Retries: only infrastructure failures to spawn the process (OSError) are retried
up to infra_retries; code failures, timeouts and limits are never retried.
"""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import resource
import signal
import subprocess
import sys
import tempfile
import time

PROVISIONAL = '독립 검증 전 잠정 (provisional, not independently verified)'
STATUSES = ('OK', 'NA', 'INVALID', 'TIMEOUT', 'MEMORY_LIMIT', 'EXCEPTION')
_EXC = re.compile(r'^([A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt))(?::|$)')


def _rss(pid):
    try:
        with open(f'/proc/{pid}/status') as f:
            for line in f:
                if line.startswith('VmRSS:'): return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        return None
    return None


def _now(): return datetime.now().astimezone().isoformat(timespec='seconds')


def _error_type(stderr_text):
    for line in reversed(stderr_text.strip().splitlines()):
        m = _EXC.match(line.strip())
        if m: return m.group(1), line.strip()[:500]
    return None, (stderr_text.strip().splitlines() or [''])[-1][:500]


def append_failure(path, record):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + '\n')


def record_na(failures_path, label, reason, context=None):
    record = {'status': 'NA', 'label': label, 'reason': reason, 'context': context or {},
              'recorded_at': _now(), 'provisional': PROVISIONAL}
    append_failure(failures_path, record)
    return record


def run_guarded(cmd, *, timeout_s, mem_limit_bytes, mem_mode='rss', poll_s=0.05, failures_path=None,
                label='', context=None, log_dir=None, infra_retries=1, env=None):
    if mem_mode not in ('rss', 'as', 'both'): raise ValueError('mem_mode must be rss, as or both')
    if timeout_s <= 0 or mem_limit_bytes <= 0: raise ValueError('limits must be positive')
    log_dir = Path(log_dir) if log_dir else Path(tempfile.mkdtemp(prefix='m05-guard-'))
    log_dir.mkdir(parents=True, exist_ok=True)
    out_path, err_path = log_dir / 'stdout.txt', log_dir / 'stderr.txt'

    def limit_as():
        if mem_mode in ('as', 'both'):
            resource.setrlimit(resource.RLIMIT_AS, (mem_limit_bytes, mem_limit_bytes))

    attempts = 0
    while True:
        attempts += 1
        try:
            with open(out_path, 'wb') as out, open(err_path, 'wb') as err:
                started = time.monotonic()
                proc = subprocess.Popen(cmd, stdout=out, stderr=err, preexec_fn=limit_as,
                                        start_new_session=True, env=env)
                break
        except OSError as exc:
            if attempts > infra_retries:
                record = {'status': 'EXCEPTION', 'label': label, 'error_type': 'InfraSpawnError',
                          'message': str(exc)[:500], 'attempts': attempts, 'context': context or {},
                          'recorded_at': _now(), 'provisional': PROVISIONAL}
                if failures_path: append_failure(failures_path, record)
                return record
    killed, max_rss = None, 0
    while proc.poll() is None:
        elapsed = time.monotonic() - started
        rss = _rss(proc.pid)
        if rss: max_rss = max(max_rss, rss)
        if elapsed > timeout_s:
            killed = 'TIMEOUT'
        elif mem_mode in ('rss', 'both') and rss and rss > mem_limit_bytes:
            killed = 'MEMORY_LIMIT'
        if killed:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
            break
        time.sleep(poll_s)
    elapsed = time.monotonic() - started
    stderr_text = err_path.read_text(errors='replace')
    stdout_text = out_path.read_text(errors='replace')
    error_type, message = _error_type(stderr_text)
    if killed:
        status = killed
    elif proc.returncode == 0:
        status = 'OK'
    elif error_type == 'MemoryError':
        status = 'MEMORY_LIMIT'
    else:
        status = 'EXCEPTION'
        if proc.returncode < 0 and not error_type:
            error_type = f'Signal{-proc.returncode}'
            message = f'killed by signal {-proc.returncode} (not by this guard; possible OS OOM killer)'
    record = {'status': status, 'label': label, 'returncode': proc.returncode, 'elapsed_s': round(elapsed, 3),
              'max_sampled_rss_bytes': max_rss, 'limits': {'timeout_s': timeout_s,
              'mem_limit_bytes': mem_limit_bytes, 'mem_mode': mem_mode, 'poll_s': poll_s},
              'attempts': attempts, 'context': context or {}, 'recorded_at': _now(),
              'log_dir': str(log_dir), 'provisional': PROVISIONAL}
    if status != 'OK':
        record.update(error_type=error_type, message=message)
        if failures_path: append_failure(failures_path, record)
    else:
        record['stdout_tail'] = stdout_text.strip().splitlines()[-1][:2000] if stdout_text.strip() else ''
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m sim.guard')
    parser.add_argument('--timeout-s', type=float, required=True)
    parser.add_argument('--mem-limit-mb', type=int, required=True)
    parser.add_argument('--mem-mode', choices=('rss', 'as', 'both'), default='rss')
    parser.add_argument('--failures', required=True)
    parser.add_argument('--label', default='')
    parser.add_argument('--log-dir')
    parser.add_argument('cmd', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    cmd = args.cmd[1:] if args.cmd[:1] == ['--'] else args.cmd
    if not cmd: parser.error('command required after --')
    record = run_guarded(cmd, timeout_s=args.timeout_s, mem_limit_bytes=args.mem_limit_mb * 2**20,
                         mem_mode=args.mem_mode, failures_path=args.failures, label=args.label,
                         log_dir=args.log_dir)
    print(json.dumps(record, sort_keys=True, ensure_ascii=False))
    sys.exit(0 if record['status'] == 'OK' else 1)


if __name__ == '__main__': main()
