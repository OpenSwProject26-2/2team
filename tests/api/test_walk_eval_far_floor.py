"""walk_eval --far-floor(역 800m 밖 하한만 바꾸기, 충족률-형평성 곡선 실험): 전체 하한은 그대로, 기본 끔."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "experiments" / "routegen_lab"))

from walk_eval import apply_far_floor  # noqa: E402


class FarFloorTest(unittest.TestCase):
    base = {"total": 0.2808, "far": 0.3, "source": "muni"}

    def test_off_sets_far_zero_keeps_total(self):
        f = apply_far_floor(self.base, "off")
        self.assertEqual(f["far"], 0.0)
        self.assertEqual(f["total"], 0.2808)
        self.assertEqual(f["source"], "muni")
        self.assertEqual(f["far_rule"], {"arg": "off", "base_far": 0.3})

    def test_none_turns_both_floors_off(self):
        self.assertIsNone(apply_far_floor(self.base, "none"))
        self.assertIsNone(apply_far_floor(None, "none"))

    def test_absolute_value(self):
        f = apply_far_floor(self.base, "0.58")
        self.assertEqual((f["far"], f["total"]), (0.58, 0.2808))      # 역 밖 0.58 이상, 전체는 지자체안 값

    def test_plus_adds_to_base(self):
        f = apply_far_floor(self.base, "+0.05")
        self.assertAlmostEqual(f["far"], 0.35, places=12)
        self.assertEqual(f["total"], 0.2808)

    def test_plus_zero_is_same_value(self):
        self.assertEqual(apply_far_floor(self.base, "+0")["far"], 0.3)

    def test_number_is_fixed(self):
        self.assertEqual(apply_far_floor(self.base, "0.42")["far"], 0.42)

    def test_does_not_mutate_input(self):
        apply_far_floor(self.base, "+0.1")
        self.assertEqual(self.base, {"total": 0.2808, "far": 0.3, "source": "muni"})

    def test_needs_floor(self):
        with self.assertRaises(SystemExit):
            apply_far_floor(None, "off")

    def test_rejects_extra_combos(self):
        with self.assertRaises(SystemExit):
            apply_far_floor({**self.base, "extra": [("x", {}, 0.1, 0.1)]}, "off")


if __name__ == "__main__":
    unittest.main()
