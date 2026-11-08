import json
import unittest
from tests.sim.golden_cases import GOLDEN, cases, record


class GoldenLogTests(unittest.TestCase):
    """Event logs and outcomes must equal those recorded before the P2/P3 refactors."""
    def test_logs_identical_to_recorded(self):
        expected = json.loads((GOLDEN / 'golden.json').read_text())
        seen = set()
        for name, *case in cases():
            with self.subTest(case=name):
                log, summary = record(name, *case)
                self.assertEqual(summary, expected[name])
                full = GOLDEN / f'{name}.jsonl'
                if full.exists(): self.assertEqual(log, full.read_text())
            seen.add(name)
        self.assertEqual(seen, set(expected))


if __name__ == '__main__': unittest.main()
