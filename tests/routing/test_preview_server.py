"""로컬 화면과 검색 연결이 동일한 합성 운행안 버전을 쓰는지 확인한다."""

import json
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from busstrike.routing.gtfs_preview import EXAMPLE, load_preview
from busstrike.routing.preview_server import handler_for


class TestPreviewServer(unittest.TestCase):
    def test_selected_plan_is_not_replaced_by_synthetic_shuttle(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            pointer = Path(folder) / "selection.json"
            pointer.write_text(
                json.dumps({"selected": {"plan_id": "real-c-plan", "revision": 4}}),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0), handler_for(load_preview(), selection_file=pointer)
            )
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with urlopen(base + "/__demo__/selection", timeout=2) as response:
                    selected = json.load(response)
                self.assertEqual(selected["selected"]["revision"], 4)
                query = {
                    "journey_state": "strike_selected",
                    "origin": "A",
                    "destination": "C",
                    "departure_at": "2026-11-07T00:00:00+09:00",
                    "plan_id": "real-c-plan",
                    "revision": 4,
                }
                with urlopen(
                    base + "/__demo__/search?" + urlencode(query), timeout=2
                ) as response:
                    result = json.load(response)
                self.assertEqual(result["error"]["code"], "DATA_INCOMPLETE")
                query["revision"] = 3
                with urlopen(
                    base + "/__demo__/search?" + urlencode(query), timeout=2
                ) as response:
                    mismatch = json.load(response)
                self.assertEqual(mismatch["error"]["code"], "DATA_VERSION_MISMATCH")
            finally:
                server.shutdown()
                worker.join(timeout=2)
                server.server_close()

    def test_screen_config_and_search_share_plan_revision(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(load_preview()))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(worker.join, 2)
        self.addCleanup(server.shutdown)

        base = f"http://127.0.0.1:{server.server_port}"
        with urlopen(base, timeout=2) as response:
            page = response.read().decode("utf-8")
        self.assertIn("시뮬레이션 · 합성 시간표 · 독립 검증 전", page)
        with urlopen(base + "/__demo__/config", timeout=2) as response:
            config = json.load(response)
        params = urlencode(
            {
                "plan_id": config["plan_id"],
                "revision": config["revision"],
                "origin": "A",
                "destination": "C",
                "departure_at": "2026-11-07T00:00:00+09:00",
            }
        )
        with urlopen(base + "/__demo__/search?" + params, timeout=2) as response:
            result = json.load(response)
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["plan_id"], config["plan_id"])
        self.assertEqual(result["result"]["plan_revision"], config["revision"])
        self.assertEqual(result["result"]["verification"], "not_independently_verified")

    def test_normal_endpoint_works_without_shuttle_feed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copy = Path(directory) / "example"
            shutil.copytree(EXAMPLE, copy, ignore=shutil.ignore_patterns("shuttle"))
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(None, copy))
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                params = urlencode(
                    {
                        "query_id": "synthetic-demand-1",
                        "origin_stop_id": "A",
                        "destination_stop_id": "C",
                        "departure_at": "2026-11-07T00:00:00+09:00",
                    }
                )
                url = f"http://127.0.0.1:{server.server_port}/__demo__/normal-reachability?{params}"
                with urlopen(url, timeout=2) as response:
                    payload = json.load(response)
                self.assertTrue(payload["preview_only"])
                self.assertEqual(payload["result"]["status"], "reachable")
                self.assertEqual(
                    payload["result"]["normal_timetable_version"], "d-normal-demo-v1"
                )
            finally:
                server.shutdown()
                worker.join(timeout=2)
                server.server_close()


if __name__ == "__main__":
    unittest.main()
