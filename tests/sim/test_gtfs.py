"""셔틀 GTFS 내보내기: simulator.md §6.4. 왕복(내보내기 → 다시 읽기) 검사."""

import tempfile
import unittest
from pathlib import Path

from busstrike.sim import SimError, build_timetable, export_gtfs, io, normalize_plan, read_gtfs, run
from busstrike.sim.__main__ import main
from busstrike.sim.gtfs import format_gtfs_time, parse_gtfs_time

from tests.sim import cases


def timetable_of(case):
    sc, tt, plan, _ = case.objects()
    return sc, normalize_plan(plan, sc.vehicles, sc.window, sc.policy, tt)


def expected_by_route(timetable):
    """입력 시간표에서 노선별 (영업 trip의 정류장열, 출발 시각) 목록."""
    out = {}
    for t in timetable.trips:
        if t.in_service:
            out.setdefault(t.route_id, []).append((t.stop_ids, t.stop_times[0].sched_s, t.vehicle_id, t.trip_id))
    return {r: sorted(v, key=lambda x: (x[1], x[3])) for r, v in out.items()}


class RoundTripMixin:
    def assert_round_trip(self, sc, timetable, d):
        export_gtfs(timetable, sc.service_date, d)
        feed = read_gtfs(d)
        exp = expected_by_route(timetable)
        n_in = sum(1 for t in timetable.trips if t.in_service)
        self.assertEqual(len(feed["trips"]), n_in)                      # trip 수
        self.assertEqual(sorted(feed["routes"]), sorted(exp))
        got = {}
        for t in feed["trips"]:
            got.setdefault(t["route_id"], []).append((tuple(t["stop_ids"]), t["times_s"][0], t["block_id"], t["trip_id"]))
        for rid, rows in exp.items():
            self.assertEqual(sorted(got[rid], key=lambda x: (x[1], x[3])), rows, rid)  # 정차 순서·출발 시각·차량
        by_id = {t.trip_id: t for t in timetable.trips}
        for t in feed["trips"]:                                          # 정차 시각 전부
            src = by_id[t["trip_id"]]
            self.assertEqual(t["times_s"], [s.sched_s for s in src.stop_times])
            self.assertEqual(t["stop_sequences"], [s.stop_seq for s in src.stop_times])
        self.assertEqual(feed["feed_version"], f"{timetable.plan_id}@{timetable.plan_revision}")
        return feed


class RoundTripTests(RoundTripMixin, unittest.TestCase):
    def test_f1_round_trip(self):
        case = cases.t1()
        sc, tb = timetable_of(case)
        with tempfile.TemporaryDirectory() as d:
            feed = self.assert_round_trip(sc, tb, Path(d))
        self.assertEqual(len(feed["trips"]), 2)
        self.assertEqual([t["stop_ids"] for t in feed["trips"]][0], ["A", "B", "S"])

    def test_synthetic_round_trip_with_deadhead_excluded(self):
        for seed in (1, 2, 3):
            case = cases.synthetic(seed, two_station=True)
            sc, tb = timetable_of(case)
            self.assertTrue(any(not t.in_service for t in tb.trips), "공차 trip이 있는 사례여야 한다")
            with tempfile.TemporaryDirectory() as d:
                self.assert_round_trip(sc, tb, Path(d))

    def test_direction_id_for_in_service_return_leg(self):
        sc, tb = timetable_of(cases.synthetic(1, two_station=True))
        with tempfile.TemporaryDirectory() as d:
            feed = self.assert_round_trip(sc, tb, Path(d))
        by_id = {t.trip_id: t for t in tb.trips}
        self.assertEqual({t["direction_id"] for t in feed["trips"]}, {0, 1})
        for t in feed["trips"]:
            self.assertEqual(t["direction_id"], by_id[t["trip_id"]].leg_index % 2)

    def test_round_trip_through_run_output(self):
        """run()이 쓴 timetable.json에서 내보내도 같다(CLI 경로)."""
        case = cases.t2()
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            paths = case.write(d / "in")
            run(paths["scenario.json"], paths["run_inputs.json"], paths["plan.json"], d / "out")
            tb = io.timetable_from_dict(io.read_json(d / "out" / "run" / "timetable.json"))
            sc = io.scenario_from_dict(io.read_json(paths["scenario.json"]))
            self.assert_round_trip(sc, tb, d / "gtfs")
            self.assertEqual(main(["gtfs", "--scenario", str(paths["scenario.json"]),
                                   "--timetable", str(d / "out" / "run" / "timetable.json"), "--out", str(d / "gtfs2")]), 0)
            for name in (d / "gtfs").iterdir():
                self.assertEqual(name.read_bytes(), (d / "gtfs2" / name.name).read_bytes())


class FormatTests(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.dir = Path(self._d.name)
        case = cases.t1()
        self.sc, self.tb = timetable_of(case)
        export_gtfs(self.tb, self.sc.service_date, self.dir)

    def tearDown(self):
        self._d.cleanup()

    def lines(self, name):
        return (self.dir / name).read_text(encoding="utf-8").split("\n")

    def test_files_and_headers(self):
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()),
                         ["agency.txt", "calendar.txt", "feed_info.txt", "routes.txt", "stop_times.txt", "trips.txt"])
        self.assertEqual(self.lines("agency.txt")[0], "agency_id,agency_name,agency_url,agency_timezone")
        self.assertEqual(self.lines("trips.txt")[0], "route_id,service_id,trip_id,direction_id,block_id")
        for p in self.dir.iterdir():
            raw = p.read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
            self.assertTrue(raw.endswith(b"\n") and b"\r" not in raw)

    def test_calendar_is_service_date_only(self):
        # 2026-11-02는 월요일
        self.assertEqual(self.lines("calendar.txt")[1], "STRIKE_20261102,1,0,0,0,0,0,0,20261102,20261102")

    def test_stop_times_rules(self):
        rows = [l.split(",") for l in self.lines("stop_times.txt")[1:] if l]
        first = [r for r in rows if r[4] == "0"][0]
        self.assertEqual(first[1], first[2])                      # arrival = departure
        self.assertEqual((first[5], first[6], first[7]), ("0", "1", "1"))
        last = [r for r in rows if r[0] == first[0]][-1]
        self.assertEqual((last[5], last[6], last[7]), ("1", "0", "0"))   # 마지막은 pickup 불가
        self.assertEqual(rows, sorted(rows, key=lambda r: (r[0], int(r[4]))))

    def test_direction_block_route(self):
        trips = [l.split(",") for l in self.lines("trips.txt")[1:] if l]
        self.assertEqual([t[3] for t in trips], ["0", "0"])   # 회차 leg는 공차라 내보내지 않는다
        self.assertTrue(all(t[4] == "V1" for t in trips))
        self.assertEqual(self.lines("routes.txt")[1], "R1,busstrike-sim,R1,A–S,3")

    def test_deterministic(self):
        d2 = self.dir.parent / (self.dir.name + "-2")
        export_gtfs(self.tb, self.sc.service_date, d2)
        for p in self.dir.iterdir():
            self.assertEqual(p.read_bytes(), (d2 / p.name).read_bytes())

    def test_time_formatting(self):
        self.assertEqual(format_gtfs_time(0), "00:00:00")
        self.assertEqual(format_gtfs_time(25 * 3600 + 600), "25:10:00")
        self.assertEqual(parse_gtfs_time("25:10:00"), 90600)
        with self.assertRaises(SimError):
            format_gtfs_time(-1)
        with self.assertRaises(SimError):
            parse_gtfs_time("9:5")

    def test_bad_service_date(self):
        with self.assertRaises(SimError):
            export_gtfs(self.tb, "2026/11/02", self.dir / "x")

    def test_optional_stops_file(self):
        d = self.dir / "withstops"
        export_gtfs(self.tb, self.sc.service_date, d, {"A": (37.5, 127.0), "ZZ": (0.0, 0.0)})
        rows = (d / "stops.txt").read_text(encoding="utf-8").split("\n")
        self.assertEqual(rows[0], "stop_id,stop_name,stop_lat,stop_lon")
        self.assertEqual(rows[1:3], ["A,A,37.5,127.0", ""])      # 쓰이지 않은 정류장은 빠진다

    def test_missing_file_on_read(self):
        (self.dir / "trips.txt").unlink()
        with self.assertRaises(SimError):
            read_gtfs(self.dir)


if __name__ == "__main__":
    unittest.main()
