"""합성 시간표의 직행·환승과 오류 의미를 손계산 사례로 고정한다."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gtfs_preview import EXAMPLE, PreviewError, load_preview, search


class TestGtfsPreview(unittest.TestCase):
    def setUp(self) -> None:
        self.preview = load_preview()

    def query(
        self,
        origin="A",
        destination="C",
        departure="2026-11-07T00:00:00+09:00",
        **kwargs,
    ):
        return search(
            self.preview,
            origin,
            destination,
            departure,
            self.preview.plan_id,
            self.preview.revision,
            **kwargs,
        )

    def test_normal_direct_when_demo_exclusion_disabled(self) -> None:
        result = self.query(exclude_demo_routes=False)
        self.assertEqual([leg["trip_id"] for leg in result["legs"]], ["normal:n1"])
        self.assertEqual(result["arrival_at"], "2026-11-07T00:03:00+09:00")
        self.assertEqual(result["transfers"], 0)

    def test_excluded_normal_route_uses_shuttle_transfer(self) -> None:
        result = self.query()
        self.assertEqual(
            [leg["trip_id"] for leg in result["legs"]], ["shuttle:s1", "normal:n2"]
        )
        self.assertEqual(result["arrival_at"], "2026-11-07T00:05:00+09:00")
        self.assertEqual(result["transfers"], 1)
        self.assertEqual(result["plan_mode"], "simulation")
        self.assertEqual(result["verification"], "not_independently_verified")

    def test_after_midnight_keeps_service_date_and_offset(self) -> None:
        result = self.query(departure="2026-11-08T00:09:00+09:00")
        self.assertEqual(
            [leg["trip_id"] for leg in result["legs"]], ["shuttle:s2", "normal:n3"]
        )
        self.assertEqual(result["service_date"], "2026-11-07")
        self.assertEqual(result["arrival_at"], "2026-11-08T00:13:00+09:00")

    def test_unserved_stop_has_no_path(self) -> None:
        with self.assertRaises(PreviewError) as caught:
            self.query(origin="D")
        self.assertEqual(caught.exception.code, "NO_PATH")

    def test_unknown_stop_is_out_of_scope(self) -> None:
        with self.assertRaises(PreviewError) as caught:
            self.query(origin="unknown")
        self.assertEqual(caught.exception.code, "OUT_OF_SCOPE")

    def test_wrong_plan_revision_is_version_mismatch(self) -> None:
        with self.assertRaises(PreviewError) as caught:
            search(
                self.preview,
                "A",
                "C",
                "2026-11-07T00:00:00+09:00",
                self.preview.plan_id,
                2,
            )
        self.assertEqual(caught.exception.code, "DATA_VERSION_MISMATCH")

    def test_missing_offset_is_invalid_input(self) -> None:
        with self.assertRaises(PreviewError) as caught:
            self.query(departure="2026-11-07T00:00:00")
        self.assertEqual(caught.exception.code, "INPUT_INVALID")

    def test_other_date_is_out_of_scope(self) -> None:
        with self.assertRaises(PreviewError) as caught:
            self.query(departure="2026-11-09T00:00:00+09:00")
        self.assertEqual(caught.exception.code, "OUT_OF_SCOPE")

    def test_missing_timetable_is_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copy = Path(directory) / "example"
            shutil.copytree(EXAMPLE, copy)
            (copy / "shuttle" / "stop_times.txt").unlink()
            with self.assertRaises(PreviewError) as caught:
                load_preview(copy)
        self.assertEqual(caught.exception.code, "DATA_INCOMPLETE")

    def test_unknown_stop_reference_is_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copy = Path(directory) / "example"
            shutil.copytree(EXAMPLE, copy)
            stop_times = copy / "normal" / "stop_times.txt"
            stop_times.write_text(
                stop_times.read_text(encoding="utf-8").replace(",C,3", ",missing,3"),
                encoding="utf-8",
            )
            with self.assertRaises(PreviewError) as caught:
                load_preview(copy)
        self.assertEqual(caught.exception.code, "DATA_INCOMPLETE")

    def test_same_stop_id_with_different_coordinates_is_version_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copy = Path(directory) / "example"
            shutil.copytree(EXAMPLE, copy)
            stops = copy / "shuttle" / "stops.txt"
            stops.write_text(
                stops.read_text(encoding="utf-8").replace(
                    "37.5027,127.0", "37.6,127.0"
                ),
                encoding="utf-8",
            )
            with self.assertRaises(PreviewError) as caught:
                load_preview(copy)
        self.assertEqual(caught.exception.code, "DATA_VERSION_MISMATCH")

    def test_boarding_restriction_is_explicitly_unsupported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copy = Path(directory) / "example"
            shutil.copytree(EXAMPLE, copy)
            times = copy / "shuttle" / "stop_times.txt"
            rows = times.read_text(encoding="utf-8").splitlines()
            rows[0] += ",pickup_type"
            rows[1] += ",1"
            rows[2:] = [row + ",0" for row in rows[2:]]
            times.write_text("\n".join(rows) + "\n", encoding="utf-8")
            with self.assertRaises(PreviewError) as caught:
                load_preview(copy)
        self.assertEqual(caught.exception.code, "OUT_OF_SCOPE")


if __name__ == "__main__":
    unittest.main()
