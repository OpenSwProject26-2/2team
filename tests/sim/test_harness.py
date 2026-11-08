import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from sim.harness import generate_scenario, load_scenario, run_measured, scale, env_manifest
from sim.schedule import validate_schedule


class HarnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.manifest = generate_scenario('simple', 300, cls.root / 'sc', 'net-v1', 'demand-v1')
        common = dict(arrival_seed='a1', policy='proposed-v0.2')
        cls.null = run_measured(cls.root / 'sc', cls.root / 'null', sink_kind='null', **common)
        cls.jsonl = run_measured(cls.root / 'sc', cls.root / 'jsonl', sink_kind='jsonl', **common)
        cls.again = run_measured(cls.root / 'sc', cls.root / 'again', sink_kind='jsonl', **common)
        cls.traced = run_measured(cls.root / 'sc', cls.root / 'traced', sink_kind='null', trace=True, **common)

    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()

    def test_scenario_roundtrip(self):
        network, rows, manifest = load_scenario(self.root / 'sc')
        self.assertEqual(sum(r.passenger_count for r in rows), 300)
        self.assertEqual(manifest['passengers'], 300)
        for name, digest in manifest['sha256'].items():
            self.assertEqual(hashlib.sha256((self.root / 'sc' / name).read_bytes()).hexdigest(), digest)
        validate_schedule(network['trips'])

    def test_stage_measurements(self):
        for m in (self.null, self.jsonl):
            self.assertEqual(set(m['stages']), {'load', 'init', 'run', 'finalize', 'write'})
            for stage in m['stages'].values():
                for key in ('wall_s', 'cpu_s', 'rss_bytes', 'peak_rss_bytes'): self.assertIn(key, stage)
            self.assertGreater(m['peak_rss_bytes'], 0)
            self.assertEqual(m['passengers'], 300)
            self.assertIn('독립 검증 전 잠정', m['label'])
        self.assertIn('traced_peak_bytes', self.traced['stages']['run'])
        self.assertNotIn('traced_peak_bytes', self.null['stages']['run'])

    def test_sinks_separated_and_consistent(self):
        self.assertEqual(self.null['log_bytes'], 0)
        self.assertIsNone(self.null['log_sha256'])
        log = (self.root / 'jsonl' / 'events.jsonl').read_bytes()
        self.assertEqual(self.jsonl['log_bytes'], len(log))
        self.assertEqual(self.jsonl['log_sha256'], hashlib.sha256(log).hexdigest())
        self.assertEqual(len(log.splitlines()), self.jsonl['event_count'])
        self.assertFalse((self.root / 'null' / 'events.jsonl').exists())
        for key in ('event_count', 'outcome_sha256', 'run_checksum', 'realization_checksum'):
            self.assertEqual(self.null[key], self.jsonl[key])

    def test_reproducible(self):
        for key in ('log_sha256', 'outcome_sha256', 'run_checksum', 'realization_checksum', 'metrics'):
            self.assertEqual(self.jsonl[key], self.again[key])

    def test_outputs_and_env(self):
        metrics = json.loads((self.root / 'null' / 'metrics.json').read_text())
        self.assertEqual(metrics['served'] + metrics['unserved'] + metrics['censored'], 300)
        lines = (self.root / 'null' / 'passenger_outcomes.csv').read_text().splitlines()
        self.assertEqual(len(lines), 301)
        env = env_manifest()
        for key in ('commit', 'python', 'platform', 'machine', 'cpu_count', 'recorded_at'): self.assertIn(key, env)
        self.assertEqual(json.loads((self.root / 'null' / 'env_manifest.json').read_text())['python'], env['python'])

    def test_scale_names(self):
        self.assertEqual([scale(x) for x in ('1k', '10k', '100k', '900k', '1234')],
                         [1000, 10000, 100000, 900000, 1234])


if __name__ == '__main__': unittest.main()
