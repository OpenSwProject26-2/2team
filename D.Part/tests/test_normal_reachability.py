"""A 전달용 정상 교통 판정에서 셔틀과 판정 불가를 구분한다."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gtfs_preview import EXAMPLE
from normal_reachability import assess_normal_reachability


class TestNormalReachability(unittest.TestCase):
    def query(
        self,
        origin="A",
        destination="C",
        departure="2026-11-07T00:00:00+09:00",
        folder=EXAMPLE,
    ):
        return assess_normal_reachability(
            "synthetic-demand-1", origin, destination, departure, folder
        )

    def test_normal_route_is_reachable_without_shuttle_or_strike_filter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copy = Path(directory) / "example"
            shutil.copytree(EXAMPLE, copy, ignore=shutil.ignore_patterns("shuttle"))
            result = self.query(folder=copy)
        self.assertEqual(result["status"], "reachable")
        self.assertIs(result["reachable"], True)
        self.assertEqual(result["arrival_at"], "2026-11-07T00:03:00+09:00")
        self.assertEqual([leg["trip_id"] for leg in result["legs"]], ["normal:n1"])
        self.assertEqual(result["transfer_count"], 0)
        self.assertIsNone(result["walking_time_s"])
        self.assertEqual(result["normal_timetable_version"], "d-normal-demo-v1")
        self.assertEqual(result["scope"], "synthetic_normal_bus_stop_to_stop")

    def test_in_scope_no_path_is_unreachable(self) -> None:
        result = self.query(origin="D")
        self.assertEqual(result["status"], "unreachable")
        self.assertIs(result["reachable"], False)
        self.assertEqual(result["failure_code"], "NO_PATH")
        self.assertIsNone(result["arrival_at"])

    def test_unknown_stop_is_indeterminate(self) -> None:
        result = self.query(origin="unknown")
        self.assertEqual(result["status"], "indeterminate")
        self.assertIsNone(result["reachable"])
        self.assertEqual(result["failure_code"], "OUT_OF_SCOPE")

    def test_missing_normal_timetable_is_indeterminate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copy = Path(directory) / "example"
            shutil.copytree(EXAMPLE, copy)
            (copy / "normal" / "stop_times.txt").unlink()
            result = self.query(folder=copy)
        self.assertEqual(result["status"], "indeterminate")
        self.assertEqual(result["failure_code"], "DATA_INCOMPLETE")
        self.assertIsNone(result["normal_timetable_version"])

    def test_after_midnight_uses_previous_service_date(self) -> None:
        result = self.query(origin="B", departure="2026-11-08T00:11:00+09:00")
        self.assertEqual(result["status"], "reachable")
        self.assertEqual(result["arrival_at"], "2026-11-08T00:13:00+09:00")
        self.assertEqual([leg["trip_id"] for leg in result["legs"]], ["normal:n3"])


if __name__ == "__main__":
    unittest.main()
