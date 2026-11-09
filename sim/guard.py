"""P7 자원 가드: 측정 명령 1개를 시간·메모리 상한 아래 별도 프로세스로 실행한다.

    python -m sim.guard --timeout-s 600 --mem-limit-mb 8000 --mem-mode rss \
        --failures results/failures.jsonl --label complex-100k -- \
        python -m sim.harness run ...

상태: OK, TIMEOUT, MEMORY_LIMIT, EXCEPTION, NA(일부러 실행 안 함). OK가 아니면 모두
failures.jsonl에 남긴다.
메모리 모드: rss는 부모가 poll_s마다 자식 RSS를 재서 넘으면 종료(아주 짧은 급증은 놓칠 수 있음),
as는 자식의 가상 주소공간 상한(RSS보다 커서 더 엄격, MemoryError로 실패), both는 둘 다.
재시도는 프로세스 생성 실패(OSError)만 한다. 코드 오류·시간·메모리 초과는 재시도하지 않는다.
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
    # /proc에서 자식 프로세스의 현재 RSS(bytes)를 읽는다.
    try:
        with open(f'/proc/{pid}/status') as f:
            for line in f:
                if line.startswith('VmRSS:'): return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        return None
    return None


def _now(): return datetime.now().astimezone().isoformat(timespec='seconds')


def _error_type(stderr_text):
    # stderr 마지막 traceback 줄에서 예외 이름을 찾는다.
    for line in reversed(stderr_text.strip().splitlines()):
        m = _EXC.match(line.strip())
        if m: return m.group(1), line.strip()[:500]
    return None, (stderr_text.strip().splitlines() or [''])[-1][:500]


def append_failure(path, record):
    """failures.jsonl에 한 줄 추가한다(덮어쓰지 않음)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + '\n')


def record_na(failures_path, label, reason, context=None):
    """실행하지 않은 셀을 NA로 기록한다."""
    record = {'status': 'NA', 'label': label, 'reason': reason, 'context': context or {},
              'recorded_at': _now(), 'provisional': PROVISIONAL}
    append_failure(failures_path, record)
    return record


def run_guarded(cmd, *, timeout_s, mem_limit_bytes, mem_mode='rss', poll_s=0.05, failures_path=None,
                label='', context=None, log_dir=None, infra_retries=1, env=None):
    """cmd를 실행하고 결과 기록(dict)을 돌려준다. 실패는 failures_path에도 남긴다."""
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
    # 끝날 때까지 시간·RSS를 재고, 넘으면 프로세스 그룹 전체를 SIGKILL로 끝낸다.
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
    # 가드가 끝냈으면 그 사유, 정상 종료면 OK, MemoryError면 메모리 초과, 나머지는 예외.
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
