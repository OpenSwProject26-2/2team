import csv
import json
from pathlib import Path
import tempfile
import unittest
from sim.pilot import main


class PilotTests(unittest.TestCase):
    def run_pilot(self, root, *extra):
        main(['--network', 'simple', '--scales', '100,200', '--repeat', '1', '--work', str(root),
              '--csv', str(root / 'pilot.csv'), '--failures', str(root / 'failures.jsonl'), *extra])
        with open(root / 'pilot.csv', newline='') as f: return list(csv.DictReader(f))

    def test_rows_for_every_cell(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = self.run_pilot(Path(tmp), '--modes', 'batch,stream', '--timeout-s', '120', '--mem-limit-mb', '4000')
            self.assertEqual([(r['scale'], r['mode'], r['status']) for r in rows],
                             [('100', 'batch', 'OK'), ('200', 'batch', 'OK'), ('100', 'stream', 'OK'), ('200', 'stream', 'OK')])
            for r in rows: self.assertIn('독립 검증 전 잠정', r['label'])
            self.assertEqual(rows[0]['metrics_sha256'], rows[2]['metrics_sha256'])  # batch와 stream 보고서가 같다
            self.assertFalse((Path(tmp) / 'failures.jsonl').exists())

    def test_failure_then_na(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = self.run_pilot(Path(tmp), '--modes', 'stream', '--timeout-s', '0.01', '--mem-limit-mb', '4000')
            self.assertEqual([r['status'] for r in rows], ['TIMEOUT', 'NA'])
            failures = [json.loads(x) for x in (Path(tmp) / 'failures.jsonl').read_text().splitlines()]
            self.assertEqual([x['status'] for x in failures], ['TIMEOUT', 'NA'])

    def test_scales_must_ascend(self):
        with self.assertRaises(SystemExit):
            main(['--network', 'simple', '--scales', '10k,1k', '--repeat', '1', '--timeout-s', '1',
                  '--mem-limit-mb', '1', '--csv', '/dev/null', '--failures', '/dev/null'])


if __name__ == '__main__': unittest.main()
