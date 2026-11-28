"""Small public-shape fixture for real-demand accounting; no source data copied."""

import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from busstrike.api.d_geo import MapSnapshot, DataError
from busstrike.api.d_transit_data import TransitData


class RealTransitDemandTests(unittest.TestCase):
    def test_strike_changes_exposure_without_changing_observation(self):
        class FixtureTransit(TransitData):
            def __init__(self):
                self.root = Path("fixture")
                self.manifest = {"days": {"20261026": {}}}
                self._observed_cache = {}
                self._real_base_cache = {}
                self.reads = {}
                self._reference_cache = {"stops": {"B1": {"lon": "126.9", "lat": "37.55", "BSST_NM": "Bus stop"}},
                                         "routes": {"R1": {}, "R2": {}}, "gu": {"guro": {}}}

            def _exists(self, name):
                return True

            def _csv(self, name):
                self.reads[name] = self.reads.get(name, 0) + 1
                if name.endswith("subway_stations.csv"):
                    return iter([{"OUT_STN_NUM": "0150", "STN_KR_NM": "Station"}])
                if name.endswith("stop_route_hourly.csv"):
                    return iter([{"BSST_ID": "B1", "ROUTE_ID": rid, "H": "07", "BOARD": board, "ALIGHT": alight}
                                 for rid, board, alight in (("R1", "10", "8"), ("R2", "6", "4"))]
                                + [{"BSST_ID": "B1", "ROUTE_ID": "R1", "H": "08", "BOARD": "2", "ALIGHT": "1"}])
                return iter([{"RIDE_STN_ID": "150", "ALGH_STN_ID": dest, "RIDE_H": "07", "PSNG": count}
                             for dest, count in (("150", "12"), ("-1", "5"))])

        snapshot = MapSnapshot(
            snapshot_id="a", source="a_gtfs_approximate_schedule",
            service_date="2026-11-02", scenario_id=None, plan_id=None,
            revision=None, routes=({"route_id": "R1", "kind": "city"},
                                   {"route_id": "R2", "kind": "city"}),
            stops=({"stop_id": "0150", "kind": "metro", "name": "Station",
                    "coordinates": [126.91, 37.56]},),
            demand=(), fleet=None, warnings=(),
        )
        transit = FixtureTransit()
        normal = transit.real_demand(snapshot, "all", "2026-10-26", "07")
        partial = transit.real_demand(snapshot, "all", "2026-10-26", "07", ("R1",))
        expanded = transit.real_demand(snapshot, "all", "2026-10-26", "07", ("R1", "R2"))
        recovered = transit.real_demand(snapshot, "all", "2026-10-26", "07")
        self.assertEqual([r["totals"]["affected_board"] for r in (normal, partial, expanded, recovered)], [0, 10, 16, 0])
        self.assertEqual([r["totals"]["bus_board"] for r in (normal, partial, expanded, recovered)], [16] * 4)
        self.assertEqual(partial["totals"]["metro_board"], 17)
        self.assertEqual(partial["unmatched_metro"]["destination_passengers"], 5)
        self.assertEqual(partial["unmet_status"], "unavailable_no_capacity_or_passenger_rerouting")
        mapo = transit.real_demand(snapshot, "mapo", "2026-10-26", "07")
        self.assertEqual(mapo["totals"]["bus_board"], normal["totals"]["bus_board"])
        self.assertEqual(mapo["totals"]["metro_board"], normal["totals"]["metro_board"])
        self.assertEqual(transit.reads["share_k5/seoul/20261026/stop_route_hourly.csv"], 1)
        self.assertEqual(transit.reads["share_k5/seoul/20261026/subway_od_hourly.csv"], 1)
        city_off = transit.real_demand(snapshot, "all", "2026-10-26", "07", (), ("city",))
        both_off = transit.real_demand(snapshot, "all", "2026-10-26", "07", ("R1",), ("city", "metro"))
        self.assertEqual(city_off["totals"]["affected_board"], 16)
        self.assertEqual(both_off["totals"]["affected_board"], 16)
        self.assertEqual(both_off["totals"]["affected_metro_board"], 17)
        morning = transit.real_demand(snapshot, "all", "2026-10-26", "morning")
        self.assertEqual(morning["totals"]["bus_board"], 18)
        estimated = transit.real_demand(snapshot, "all", "2026-10-26", "morning_0730_estimate")
        self.assertEqual(estimated["totals"]["bus_board"], 10)
        self.assertEqual(estimated["totals"]["bus_alight"], 7)
        self.assertEqual(estimated["totals"]["metro_board"], 8.5)
        self.assertEqual(estimated["time_window"], "07:30~09:00")
        self.assertEqual(estimated["time_window_status"], "hourly_half_hour_estimate")
        estimated_strike = transit.real_demand(snapshot, "all", "2026-10-26", "morning_0730_estimate", ("R1",))
        self.assertEqual(estimated_strike["totals"]["affected_board"], 7)
        with self.assertRaises(DataError):
            transit.real_demand(snapshot, "all", "2026-10-26", "07", ("not-a-route",))


    def test_mean_is_weekday_average_of_experiment_week(self):
        """date=mean: 실험 주간 월~금 관측일만 평균(테스트 주간 11/02는 빼고, 없는 날은 건너뜀)."""
        class DayTransit(TransitData):
            def __init__(self):
                self.root = Path("fixture")
                self.manifest = {"days": {"20261026": {}, "20261027": {}, "20261102": {}}}
                self._observed_cache, self._real_base_cache = {}, {}
                self._reference_cache = {"stops": {"B1": {"lon": "126.9", "lat": "37.55", "BSST_NM": "Bus stop"}},
                                         "routes": {"R1": {}}, "gu": {"guro": {}}}

            def _exists(self, name):
                return True

            def _csv(self, name):
                if name.endswith("subway_stations.csv"):
                    return iter([])
                if name.endswith("stop_route_hourly.csv"):
                    board = {"20261026": "10", "20261027": "20", "20261102": "999"}[name.split("/")[2]]
                    return iter([{"BSST_ID": "B1", "ROUTE_ID": "R1", "H": "07", "BOARD": board, "ALIGHT": "0"}])
                return iter([])

        snapshot = MapSnapshot(
            snapshot_id="a", source="a_gtfs_approximate_schedule", service_date="2026-11-02", scenario_id=None,
            plan_id=None, revision=None, routes=({"route_id": "R1", "kind": "city"},), stops=(), demand=(),
            fleet=None, warnings=())
        mean = DayTransit().real_demand(snapshot, "all", "mean", "07", ("R1",))
        self.assertEqual(mean["mean_days"], ["20261026", "20261027"])
        self.assertEqual(mean["totals"]["bus_board"], 15)
        self.assertEqual(mean["totals"]["affected_board"], 15)


if __name__ == "__main__":
    unittest.main()
