"""새 노선 생성 알고리즘(New_Algol.md 3.3, 작업 2026-11-25): 배차 예산 안의 역-종점 순회. 모든 옵션은 기본 끔.

합성 망은 test_tour.py의 것을 쓴다(역 K·Q·R, K와 R은 같은 1호선).
검사(지시문 3절): 순환 상한 n × H, 끝 역이 같은 꼬리표, 소외 정류장 가산점 효과, 금지 목록으로 k개가 서로 다름, 결정론,
옵션을 끄면 결과 동일. 그리고 U턴 허용(9단계), R1 후보 건너뛰기, 역 쌍 병합.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "planning"))

from test_routegen import scenario  # noqa: E402
from test_tour import ENDS, LINES, demand, params, reach_rule, run, tour_network, tours  # noqa: E402

from busstrike.planning.routegen import generate as G  # noqa: E402
from busstrike.planning.routegen import tour as T  # noqa: E402
from busstrike.planning.routegen.graph import Graph  # noqa: E402
from busstrike.planning.routegen.loop import cyclic_seq  # noqa: E402


class UturnOptionTest(unittest.TestCase):
    def test_graph_allow_uturn_never_reports_uturn(self):
        net = tour_network(uturn_closure=True)
        seq = ("N4", "KA", "KE", "N1")
        self.assertTrue(Graph(net, 10, 3600, "network").has_uturn(seq))
        self.assertFalse(Graph(net, 10, 3600, "network", allow_uturn=True).has_uturn(seq))

    def test_generate_allow_uturn_brings_back_station(self):
        net = tour_network(uturn_closure=True)
        self.assertEqual([c for c in tours(run(net)) if c.hubs[0] == "K"], [])
        got = [c for c in tours(run(net, allow_uturn=True)) if c.hubs[0] == "K"]
        self.assertTrue(got)

    def test_allow_uturn_off_by_default(self):
        self.assertFalse(G.Params().allow_uturn)


if __name__ == "__main__":
    unittest.main()
