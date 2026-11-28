import io
import unittest
import zipfile

from busstrike.api.d_stop_order import longest_trip_stop_ids, major_trip_patterns


class StopOrderTests(unittest.TestCase):
    def test_pattern_families_keep_both_directions(self):
        archive_bytes = io.BytesIO()
        with zipfile.ZipFile(archive_bytes, "w") as archive:
            archive.writestr("calendar.txt", "service_id,monday,start_date,end_date\nS,1,20261101,20261130\n")
            archive.writestr("trips.txt", "route_id,service_id,trip_id\nR,S,R_p0_060000\nR,S,R_p1_070000\n")
            archive.writestr("stop_times.txt", "trip_id,stop_id,stop_sequence\n"
                             "R_p0_060000,A,1\nR_p0_060000,B,2\nR_p1_070000,B,1\nR_p1_070000,C,2\n")
        archive_bytes.seek(0)
        self.assertEqual(major_trip_patterns(archive_bytes, "2026-11-02", "R"),
                         (("A", "B"), ("B", "C")))

    def test_longest_active_trip_retains_return_visit(self):
        archive_bytes = io.BytesIO()
        with zipfile.ZipFile(archive_bytes, "w") as archive:
            archive.writestr("calendar.txt", "service_id,monday,start_date,end_date\nS,1,20261101,20261130\n")
            archive.writestr("trips.txt", "route_id,service_id,trip_id\nR,S,short\nR,S,round\n")
            archive.writestr("stop_times.txt", "trip_id,stop_id,stop_sequence\n"
                             "short,A,1\nshort,B,2\nround,A,1\nround,B,2\nround,C,3\nround,B,4\nround,A,5\n")
        archive_bytes.seek(0)
        self.assertEqual(longest_trip_stop_ids(archive_bytes, "2026-11-02", "R"),
                         ("A", "B", "C", "B", "A"))


if __name__ == "__main__":
    unittest.main()
