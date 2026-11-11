"""개인정보가 없는 합성 GTFS 파일로 입력 검사기를 검증한다."""

import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gtfs_header_check import check_gtfs

HEADERS = {
    "agency.txt": "agency_name,agency_url,agency_timezone\n",
    "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\n",
    "routes.txt": "route_id,route_type,route_short_name\n",
    "trips.txt": "route_id,service_id,trip_id\n",
    "stop_times.txt": "trip_id,stop_id,stop_sequence,arrival_time,departure_time\n",
    "calendar_dates.txt": "service_id,date,exception_type\n",
}


class TestGtfsHeaderCheck(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "feed"
        self.directory.mkdir()
        for filename, header in HEADERS.items():
            (self.directory / filename).write_text(header, encoding="utf-8")

    def test_valid_folder(self) -> None:
        self.assertEqual(check_gtfs(self.directory), [])

    def test_missing_required_file(self) -> None:
        (self.directory / "trips.txt").unlink()
        self.assertIn("필수 파일 없음: trips.txt", check_gtfs(self.directory))

    def test_missing_column(self) -> None:
        (self.directory / "stops.txt").write_text(
            "stop_id,stop_name\n", encoding="utf-8"
        )
        errors = check_gtfs(self.directory)
        self.assertTrue(any("stop_lat" in error for error in errors))

    def test_missing_calendar_files(self) -> None:
        (self.directory / "calendar_dates.txt").unlink()
        self.assertTrue(
            any("운행일 파일 없음" in e for e in check_gtfs(self.directory))
        )

    def test_calendar_txt_also_allowed(self) -> None:
        (self.directory / "calendar_dates.txt").unlink()
        (self.directory / "calendar.txt").write_text(
            "service_id,monday,tuesday,wednesday,thursday,friday,"
            "saturday,sunday,start_date,end_date\n",
            encoding="utf-8",
        )
        self.assertEqual(check_gtfs(self.directory), [])

    def test_missing_route_name(self) -> None:
        (self.directory / "routes.txt").write_text(
            "route_id,route_type\n", encoding="utf-8"
        )
        self.assertTrue(any("노선명 열 없음" in e for e in check_gtfs(self.directory)))

    def test_valid_zip(self) -> None:
        archive_path = Path(self.temp.name) / "feed.zip"
        with ZipFile(archive_path, "w") as archive:
            for file in self.directory.iterdir():
                archive.write(file, arcname=f"gtfs/{file.name}")
        self.assertEqual(check_gtfs(archive_path), [])

    def test_duplicate_filename_zip(self) -> None:
        archive_path = Path(self.temp.name) / "feed.zip"
        with ZipFile(archive_path, "w") as archive:
            archive.writestr("first/stops.txt", HEADERS["stops.txt"])
            archive.writestr("second/stops.txt", HEADERS["stops.txt"])
        self.assertTrue(any("파일명 중복" in e for e in check_gtfs(archive_path)))

    def test_invalid_file(self) -> None:
        self.assertTrue(check_gtfs(Path(self.temp.name) / "missing.zip"))


if __name__ == "__main__":
    unittest.main()
