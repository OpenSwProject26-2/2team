"""호선 단위 묶기(Params.line_group)와 옵션을 끈 회귀(새 알고리즘에서 남긴 부분, 작업 2026-11-25).

합성 망은 test_tour.py의 것을 쓴다(역 K·Q·R, K와 R은 같은 1호선). 배차 예산·역 쌍 병합·U턴 허용·대수 고정 배분의 테스트는
그 기능과 함께 지웠다(docs/design/pipeline.md).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "planning"))

from test_routegen import scenario  # noqa: E402
from test_tour import ENDS, LINES, demand, params, reach_rule, run, tour_network  # noqa: E402

from busstrike.planning.routegen import generate as G  # noqa: E402
from busstrike.planning.routegen import tour as T  # noqa: E402

TAGS = {"K": frozenset({"1"}), "Q": frozenset({"2"}), "R": frozenset({"1"})}


def lg_run(**kw):
    net = tour_network()
    return G.generate(net, demand(), scenario(), params(**{"tour": ("feeder",), **kw}),
                      reach=reach_rule(net), tour_inputs=T.TourInputs(ENDS, LINES, TAGS))


class LineGroupTest(unittest.TestCase):
    """호선 단위 묶기: 끝 역 = 씨앗에서 망 거리로 가장 가까운 같은 꼬리표 역."""

    def test_line_groups(self):
        from busstrike.planning.routegen.budget import line_groups
        self.assertEqual(line_groups(TAGS, ["K"], ["K", "Q", "R"]), {"1": ["K", "R"]})
        self.assertEqual(line_groups(TAGS, ["K", "Q"], ["K", "Q", "R"]), {"1": ["K", "R"], "2": ["Q"]})

    def test_end_station_has_group_tag(self):
        res = lg_run(line_group=True)
        got = [c for c in res.candidates if c.origin == "tour_feeder"]
        self.assertTrue(got)
        for c in got:
            tg = [t.split(":", 1)[1] for t in c.made_by if t.startswith("lg:")]
            self.assertTrue(tg, c.made_by)
            self.assertTrue(any(t in TAGS[c.hubs[0]] for t in tg))

    def test_nearer_same_line_station_becomes_end(self):
        # N3의 수요는 K(egress 15)가 R(egress 6)보다 크지만 망 거리로는 R이 가깝다 → 끝 역 R, 원래 역 K 기록
        res = lg_run(line_group=True)
        got = [c for c in res.candidates if c.origin == "tour_feeder" and any(t.startswith("tsp:N3") for t in c.made_by)
               and "lg:1" in c.made_by]
        self.assertTrue(got)
        self.assertTrue(all(c.hubs == ("R",) and "orig:K" in c.made_by for c in got), [(c.hubs, c.made_by) for c in got])

    def test_off_keeps_hub_ends(self):
        res = lg_run()
        self.assertFalse(any(t.startswith(("lg:", "orig:")) for c in res.candidates for t in c.made_by))

    def test_load_inputs_reads_tags(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "network").mkdir()
            (Path(d) / "network" / "station_stops.csv").write_text(
                "station_id,station_name,kind,station_stop_id,status,candidates\n0001,가,access,s1,ok,s1 s2\n"
                "0001,가,egress,s2,ok,s1 s2\n", encoding="utf-8")
            (Path(d) / "lines.csv").write_text("station_id,station_name,lines,tags,complex\n0001,가,1,1-경부 1-경인,가\n",
                                               encoding="utf-8")
            got = T.load_inputs(d, Path(d) / "lines.csv")
            self.assertEqual(got.tags, {"0001": frozenset({"1-경부", "1-경인"})})


OFF = dict(fill=False, fill_bonus=0.0, fill_top=5, fill_pool=60, line_group=False)


class OptionsOffTest(unittest.TestCase):
    """옵션을 모두 기본값(끔)으로 적어도 결과는 예전과 같다(회귀 방지). tags를 넣어도 묶기를 끄면 같다."""

    def test_all_off_same_candidates(self):
        base = run()
        again = run(**OFF)
        self.assertEqual([c.record() for c in base.candidates], [c.record() for c in again.candidates])
        net = tour_network()
        with_tags = G.generate(net, demand(), scenario(), params(**OFF), reach=reach_rule(net),
                               tour_inputs=T.TourInputs(ENDS, LINES, TAGS))
        self.assertEqual([c.record() for c in base.candidates], [c.record() for c in with_tags.candidates])

    def test_no_tour_same_as_before(self):
        net = tour_network()
        a = G.generate(net, demand(), scenario(), params(tour=None), reach=reach_rule(net))
        b = G.generate(net, demand(), scenario(), params(tour=None, **OFF), reach=reach_rule(net))
        self.assertEqual([c.record() for c in a.candidates], [c.record() for c in b.candidates])

    def test_removed_options_rejected(self):
        for kw in ({"budget_h_s": 1200}, {"tour_only": True}, {"merge": True}, {"allow_uturn": True}):
            with self.assertRaises(TypeError):
                G.Params(**kw)
        with self.assertRaises(ValueError):
            G.Params(loop="demand", tour=("pair",))


if __name__ == "__main__":
    unittest.main()
