import json
from pathlib import Path
import sys
import tempfile
import unittest
from sim.guard import run_guarded, record_na, _error_type

MB = 2 ** 20
# Touch every page so RSS really grows (bytes(n) * ... is copied, not lazily zero-mapped).
ALLOC = 'import time; x = b"\\x01" * ({mb} * 2**20); time.sleep(5)'


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.failures = Path(self.tmp.name) / 'failures.jsonl'

    def tearDown(self): self.tmp.cleanup()

    def guarded(self, code, **kw):
        kw.setdefault('timeout_s', 20)
        kw.setdefault('mem_limit_bytes', 2048 * MB)
        return run_guarded([sys.executable, '-c', code], failures_path=self.failures, label='t',
                           log_dir=Path(self.tmp.name) / str(len(self.records())), **kw)

    def records(self):
        return [json.loads(x) for x in self.failures.read_text().splitlines()] if self.failures.exists() else []

    def test_ok_not_logged(self):
        r = self.guarded('print("done")')
        self.assertEqual(r['status'], 'OK')
        self.assertEqual(r['stdout_tail'], 'done')
        self.assertEqual(self.records(), [])

    def test_timeout(self):
        r = self.guarded('import time; time.sleep(30)', timeout_s=0.5)
        self.assertEqual(r['status'], 'TIMEOUT')
        self.assertLess(r['elapsed_s'], 10)
        self.assertEqual(self.records()[0]['status'], 'TIMEOUT')

    def test_rss_limit(self):
        r = self.guarded(ALLOC.format(mb=400), mem_limit_bytes=150 * MB, mem_mode='rss')
        self.assertEqual(r['status'], 'MEMORY_LIMIT')
        self.assertGreater(r['max_sampled_rss_bytes'], 150 * MB)
        self.assertEqual(self.records()[0]['limits']['mem_mode'], 'rss')

    def test_address_space_limit(self):
        r = self.guarded(ALLOC.format(mb=600), mem_limit_bytes=300 * MB, mem_mode='as')
        self.assertEqual(r['status'], 'MEMORY_LIMIT')
        self.assertEqual(r['error_type'], 'MemoryError')

    def test_exception(self):
        r = self.guarded('raise ValueError("boom")')
        self.assertEqual((r['status'], r['error_type']), ('EXCEPTION', 'ValueError'))
        self.assertIn('boom', r['message'])
        self.assertEqual(self.records()[0]['error_type'], 'ValueError')

    def test_external_signal_is_exception(self):
        r = self.guarded('import os, signal; os.kill(os.getpid(), signal.SIGKILL)')
        self.assertEqual((r['status'], r['error_type']), ('EXCEPTION', 'Signal9'))

    def test_na_and_append_only(self):
        self.guarded('raise KeyError(1)')
        record_na(self.failures, 'cell', 'skipped: smaller scale failed', {'scale': 900000})
        self.assertEqual([x['status'] for x in self.records()], ['EXCEPTION', 'NA'])
        self.assertIn('독립 검증 전 잠정', self.records()[1]['provisional'])

    def test_infra_spawn_failure(self):
        r = run_guarded(['/nonexistent/binary'], timeout_s=1, mem_limit_bytes=MB, failures_path=self.failures,
                        log_dir=self.tmp.name, infra_retries=1)
        self.assertEqual((r['status'], r['error_type'], r['attempts']), ('EXCEPTION', 'InfraSpawnError', 2))

    def test_guarded_harness_run(self):
        from sim.harness import generate_scenario
        root = Path(self.tmp.name)
        generate_scenario('simple', 100, root / 'sc', 'net-v1', 'demand-v1')
        cmd = [sys.executable, '-m', 'sim.harness', 'run', '--scenario', str(root / 'sc'), '--arrival-seed', 'a',
               '--policy', 'proposed-v0.2', '--sink', 'null', '--out', str(root / 'run')]
        r = run_guarded(cmd, timeout_s=60, mem_limit_bytes=2048 * MB, failures_path=self.failures, log_dir=root / 'g')
        self.assertEqual(r['status'], 'OK')
        self.assertEqual(json.loads(r['stdout_tail'])['passengers'], 100)
        bad = run_guarded(cmd[:-1] + [str(root / 'run2')] + ['--policy', 'missing'], timeout_s=60,
                          mem_limit_bytes=2048 * MB, failures_path=self.failures, log_dir=root / 'g2')
        self.assertEqual((bad['status'], bad['error_type']), ('EXCEPTION', None))

    def test_error_type_parse(self):
        self.assertEqual(_error_type('Traceback\n  File "x"\nsim.models.SimulationError: bad\n')[0],
                         'sim.models.SimulationError')
        self.assertEqual(_error_type('')[0], None)
        with self.assertRaises(ValueError): run_guarded(['true'], timeout_s=0, mem_limit_bytes=1)


if __name__ == '__main__': unittest.main()
