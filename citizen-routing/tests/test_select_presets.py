import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import select_presets as sp  # noqa: E402


def best(duration, shuttle):
    legs = [{"kind": "walk"}] + ([{"kind": "shuttle", "route_id": "SHUTTLE:CR_R_r01"}] if shuttle else [])
    return {"duration_seconds": duration, "uses_shuttle": shuttle, "legs": legs}


class PresetRuleTests(unittest.TestCase):
    def test_keeps_only_shuttle_routes_at_least_15_minutes_faster(self):
        evaluated = [
            {"id": "a", "route_id": "r01", "best": {"normal": best(900, False),
                                                    "strike_no_shuttle": best(3000, False),
                                                    "strike_shuttle": best(1500, True)}},
            {"id": "b", "route_id": "r02", "best": {"normal": best(900, False),
                                                    "strike_no_shuttle": best(2000, False),
                                                    "strike_shuttle": best(1500, True)}},
            {"id": "c", "route_id": "r03", "best": {"normal": best(900, False),
                                                    "strike_no_shuttle": best(4000, False),
                                                    "strike_shuttle": best(1000, False)}},
            {"id": "d", "route_id": "r04", "best": {"normal": best(900, False),
                                                    "strike_no_shuttle": None,
                                                    "strike_shuttle": best(1700, True)}},
        ]
        chosen = sp.pick_presets(evaluated, min_gain_s=900, count=5)
        self.assertEqual([c["id"] for c in chosen], ["d", "a"])
        self.assertEqual(chosen[1]["gain_s"], 1500)
        self.assertIsNone(chosen[0]["gain_s"])

    def test_prefers_distinct_shuttle_routes(self):
        rows = [{"id": str(i), "route_id": "r01" if i < 3 else "r02",
                 "best": {"normal": best(900, False), "strike_no_shuttle": best(4000 - i, False),
                          "strike_shuttle": best(1000, True)}} for i in range(5)]
        chosen = sp.pick_presets(rows, min_gain_s=900, count=2)
        self.assertEqual({c["route_id"] for c in chosen}, {"r01", "r02"})

    def test_shuttle_leg_time_is_compared_with_m05_timetable(self):
        timetable = {"trips": [{"route_id": "r01", "in_service": True, "stop_times": [
            {"stop_id": "A", "sched_s": 25200}, {"stop_id": "B", "sched_s": 25320},
            {"stop_id": "C", "sched_s": 25500}]}]}
        leg = {"kind": "shuttle", "route_id": "SHUTTLE:CR_R_r01",
               "from": {"stop_id": "SHUTTLE:CR_S_A"}, "to": {"stop_id": "SHUTTLE:CR_S_C"},
               "start": "2026-11-02T07:00:10+09:00", "end": "2026-11-02T07:05:00+09:00",
               "duration_seconds": 290}
        check = sp.m05_leg_check(leg, timetable)
        self.assertEqual(check["m05_s"], 300)
        self.assertEqual(check["diff_s"], -10)
        self.assertTrue(check["within_2min"])

    def test_far_from_station_uses_800m_threshold(self):
        stations = [{"name": "역", "lat": 37.5, "lon": 127.0}]
        self.assertFalse(sp.far_from_stations(37.5050, 127.0, stations, 800))
        self.assertTrue(sp.far_from_stations(37.5080, 127.0, stations, 800))

    def test_near_misses_use_distinct_origins_and_skip_chosen(self):
        rows = [{"id": f"{o}-{d}", "route_id": "r05", "origin": {"stop_id": o},
                 "best": {"normal": best(900, False), "strike_no_shuttle": best(1000 + g, False),
                          "strike_shuttle": best(1000, True)}}
                for o, d, g in (("A", "x", 800), ("A", "y", 790), ("B", "x", 700), ("C", "x", 600))]
        misses = sp.near_misses(rows, chosen=[{"id": "C-x"}], count=3)
        self.assertEqual([m["id"] for m in misses], ["A-x", "B-x"])


if __name__ == "__main__":
    unittest.main()
