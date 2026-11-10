"""로컬 화면과 검색 연결이 동일한 합성 운행안 버전을 쓰는지 확인한다."""

import json
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gtfs_preview import load_preview
from preview_server import handler_for


class TestPreviewServer(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
