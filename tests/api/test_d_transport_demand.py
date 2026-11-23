"""Exact transport scenario lookup; never infer missing demand from map layers."""

import json
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from busstrike.api.d_geo import DataError
from busstrike.api.d_transport_demand import TransportDemandStore
from busstrike.api.d_dashboard import Store, make_handler


def scenario(kind="M05_unserved", modes=None):
    return {
        "region": "guro", "fleet": 11, "plan": "ours", "period": "exp",
        "date": "2026-10-26", "disabled_modes": modes or ["metro"], "kind": kind,
        "source": {"network_snapshot_id": "network-test-1", "input_bundle_id": "bundle-test-1",
                   "run_id": "run-test-1"},
        "stops": [{"stop_id": "stop-a", "name": "가상 정류장", "lon": 126.88, "lat": 37.49,
                   "hours": {"06": {"total": 8, "unserved": 3},
                             "07": {"total": 12, "unserved": 5}}}],
    }


def query(modes="metro", date="2026-10-26", snapshot="network-test-1"):
    return {"region": ["guro"], "fleet": ["11"], "plan": ["ours"],
            "period": ["exp"], "date": [date], "disabled_modes": [modes],
            "network_snapshot_id": [snapshot]}


class TransportDemandTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "scenarios.json"

    def write(self, *rows):
        self.path.write_text(json.dumps({"schema": "d-transport-demand/1", "scenarios": rows}),
                             encoding="utf-8")
        return TransportDemandStore(self.path)

    def test_exact_m05_result_and_missing_combination(self):
        store = self.write(scenario())
        result = store.get(query())
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["kind"], "M05_unserved")
        self.assertEqual(result["features"][0]["properties"]["total"], 20)
        self.assertEqual(result["features"][0]["properties"]["unserved"], 8)
        self.assertEqual(store.get(query("city"))["status"], "missing")
        self.assertEqual(store.get(query("metro", date="2026-10-27"))["status"], "missing")
        self.assertEqual(store.get(query("metro", snapshot="other-network"))["status"], "snapshot_mismatch")

    def test_residual_uses_hourly_counts_without_adding_modes(self):
        row = scenario("pre_shuttle_residual", ["city", "metro"])
        row["stops"][0]["hours"] = {"06": 4, "07": 9}
        result = self.write(row).get(query("metro,city"))
        self.assertEqual(result["features"][0]["properties"]["passenger_count"], 13)
        self.assertEqual(result["selection"]["disabled_modes"], ["city", "metro"])
        self.assertEqual(TransportDemandStore().get(query())["status"], "not_configured")

    def test_configured_http_endpoint_returns_exact_stop_result(self):
        self.write(scenario())
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Store(transport_demand_file=self.path)))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            url = (f"http://127.0.0.1:{server.server_port}/api/d/simulation/transport-demand"
                   "?region=guro&fleet=11&plan=ours&period=exp&date=2026-10-26&disabled_modes=metro")
            with urlopen(url, timeout=2) as response:
                result = json.load(response)
            self.assertEqual(result["status"], "available")
            self.assertEqual(result["features"][0]["properties"]["unserved"], 8)
        finally:
            server.shutdown()
            worker.join(timeout=2)
            server.server_close()

    def test_rejects_impossible_or_unapproved_fields(self):
        row = scenario()
        row["stops"][0]["hours"]["06"]["unserved"] = 9
        with self.assertRaises(DataError):
            self.write(row)
        row = scenario()
        row["stops"][0]["person_identifier"] = "not-allowed"
        with self.assertRaises(DataError):
            self.write(row)


if __name__ == "__main__":
    unittest.main()
