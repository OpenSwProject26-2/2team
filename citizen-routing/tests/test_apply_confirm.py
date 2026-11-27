import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from confirm_fixture import write_bundle  # noqa: E402
from backend import apply_confirm as ac  # noqa: E402
from backend.gtfs_adapter import read_feed  # noqa: E402

A_GTFS = REPO / "tests/fixtures/routing/gtfs_preview/normal"


class FakeRunner:
    """OTP 빌드·실행을 흉내 낸다(Docker 없이 상태 전이만 시험)."""

    def __init__(self, fail_build=False):
        self.fail_build = fail_build
        self.builds, self.served, self.stopped = [], [], []
        self.live = set()

    def build(self, graph_dir: Path, log: Path) -> None:
        self.builds.append(graph_dir)
        if self.fail_build:
            raise RuntimeError("빌드 실패(시험)")
        (graph_dir / "graph.obj").write_bytes(b"graph")

    def serve(self, slot: str, graph_dir: Path, port: int) -> str:
        name = f"fake-{slot}"
        self.served.append((slot, graph_dir, port))
        self.live.add(port)
        return name

    def healthy(self, url: str) -> bool:
        return any(f":{port}/" in url for port in self.live)

    def stop(self, handle: str) -> None:
        self.stopped.append(handle)


class Env:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.confirm_root = tmp / "confirm_root"
        self.osm = tmp / "streets.osm.pbf"
        self.osm.write_bytes(b"osm")
        self.normal_dir = tmp / "normal"
        self.normal_dir.mkdir()
        (self.normal_dir / "manifest.json").write_text(json.dumps({"version": "normal-v1"}))
        self.runtime = tmp / "runtime"

    def config(self, **extra):
        values = {"confirm_root": self.confirm_root, "gu": "guro", "a_gtfs": A_GTFS,
                  "osm": self.osm, "normal_dir": self.normal_dir, "runtime": self.runtime,
                  "ports": (18811, 18812), "keep": 2, "health_timeout_s": 1}
        values.update(extra)
        return ac.ApplyConfig(**values)


class ConfirmBundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_current_bundle_is_loaded_with_paths(self):
        write_bundle(self.root, "c1")
        bundle = ac.load_confirm(self.root, "guro")
        self.assertEqual(bundle["confirm"]["confirm_id"], "c1")
        self.assertTrue(bundle["paths"]["timetable"].is_file())

    def test_operator_style_plan_sha256_is_an_identifier_not_the_bundle_file_hash(self):
        # 작업자 A 스키마: plan_sha256 = 파이프라인 운행안 파일 해시(pareto.json), 묶음 plan.json 해시와 다를 수 있다.
        out = write_bundle(self.root, "20261127T130000-0123abcd")
        confirm = json.loads((out / "confirm.json").read_text(encoding="utf-8"))
        confirm["plan_sha256"] = "a" * 64
        confirm["strike"]["seed"] = 0
        (out / "confirm.json").write_text(json.dumps(confirm), encoding="utf-8")
        bundle = ac.load_confirm(self.root, "guro")
        self.assertEqual(bundle["confirm"]["plan_sha256"], "a" * 64)

    def test_hash_mismatch_is_rejected(self):
        out = write_bundle(self.root, "c1")
        (out / "plan.json").write_text('{"plan_id": "changed"}', encoding="utf-8")
        with self.assertRaises(ac.ConfirmError) as caught:
            ac.load_confirm(self.root, "guro")
        self.assertEqual(caught.exception.code, "CONFIRM_HASH_MISMATCH")

    def test_missing_current_is_reported(self):
        write_bundle(self.root, "c1", current=False)
        with self.assertRaises(ac.ConfirmError) as caught:
            ac.load_confirm(self.root, "guro")
        self.assertEqual(caught.exception.code, "CONFIRM_NOT_FOUND")

    def test_path_escape_is_rejected(self):
        out = write_bundle(self.root, "c1")
        confirm = json.loads((out / "confirm.json").read_text(encoding="utf-8"))
        confirm["files"]["plan"]["path"] = "../c1/plan.json"
        (out / "confirm.json").write_text(json.dumps(confirm), encoding="utf-8")
        with self.assertRaises(ac.ConfirmError) as caught:
            ac.load_confirm(self.root, "guro")
        self.assertEqual(caught.exception.code, "CONFIRM_INVALID")

    def test_one_way_route_is_rejected(self):
        write_bundle(self.root, "c1", closing=["C", "B"])
        bundle = ac.load_confirm(self.root, "guro")
        with self.assertRaises(ac.ConfirmError) as caught:
            ac.check_loops(bundle)
        self.assertEqual(caught.exception.code, "ONE_WAY_ROUTE")

    def test_long_closing_deadhead_is_rejected(self):
        write_bundle(self.root, "c1", closing_step_s=400)
        bundle = ac.load_confirm(self.root, "guro")
        with self.assertRaises(ac.ConfirmError) as caught:
            ac.check_loops(bundle)
        self.assertEqual(caught.exception.code, "ONE_WAY_ROUTE")

    def test_loop_routes_pass(self):
        write_bundle(self.root, "c1")
        summary = ac.check_loops(ac.load_confirm(self.root, "guro"))
        self.assertEqual(summary["r01"]["closing_s"], 120)


class DateAndRouteMapTests(unittest.TestCase):
    def test_same_weekday_is_chosen(self):
        days = ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"]
        self.assertEqual(ac.map_service_date("2026-10-28", days), ("2026-11-04", "same_weekday"))

    def test_fallback_to_first_date_when_weekday_missing(self):
        self.assertEqual(ac.map_service_date("2026-10-28", ["2026-11-02"]),
                         ("2026-11-02", "first_available"))

    def test_date_inside_feed_is_kept(self):
        self.assertEqual(ac.map_service_date("2026-11-02", ["2026-11-02"]), ("2026-11-02", "same_date"))

    def test_service_dates_are_listed_from_calendar_dates(self):
        self.assertEqual(ac.feed_service_dates(read_feed(A_GTFS)), ["2026-11-07"])

    def test_strike_routes_match_by_exact_id_only(self):
        routes = [{"route_id": "100100029", "route_short_name": "6512"},
                  {"route_id": "100100033", "route_short_name": "6513"}]
        table = ac.strike_route_map(["100100029", "6513", "999"], routes)
        self.assertEqual(table["matched"], ["100100029"])
        self.assertEqual(table["unmatched"], ["6513", "999"])
        self.assertEqual(table["rows"][0], {"strike_route_id": "100100029", "a_route_id": "100100029",
                                            "a_route_short_name": "6512", "match": "exact_id"})


class ApplyStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = Env(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def status(self):
        return json.loads((self.env.runtime / "apply" / "status.json").read_text(encoding="utf-8"))

    def test_apply_runs_all_steps_and_activates_graph(self):
        write_bundle(self.env.confirm_root, "c1")
        runner = FakeRunner()
        result = ac.Applier(self.env.config(), runner).run()
        status = self.status()
        self.assertEqual(status["state"], "done")
        self.assertEqual([s["name"] for s in status["steps"]],
                         ["verify", "export", "route_map", "prepare", "build", "swap"])
        self.assertTrue(all(s["state"] == "done" for s in status["steps"]))
        active = ac.read_active(self.env.runtime)
        self.assertEqual(active["confirm_id"], "c1")
        self.assertEqual(active["shuttle_version"], "plan-test@1")
        graph_dir = Path(active["dir"])
        manifest = json.loads((graph_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual((graph_dir / "graph-version.txt").read_text().strip(), manifest["version"])
        self.assertEqual(manifest["baseline_version"], "normal-v1")
        self.assertEqual(manifest["shuttle_source"]["confirm"]["confirm_id"], "c1")
        self.assertEqual(manifest["shuttle_source"]["service_date"], "2026-11-07")
        self.assertEqual(manifest["excluded_route_ids"], ["r1"])
        self.assertEqual(result["graph_version"], manifest["version"])

    def test_failed_build_keeps_previous_graph(self):
        write_bundle(self.env.confirm_root, "c1")
        ac.Applier(self.env.config(), FakeRunner()).run()
        before = ac.read_active(self.env.runtime)
        write_bundle(self.env.confirm_root, "c2", plan_extra={"note": "다른 안"})
        with self.assertRaises(RuntimeError):
            ac.Applier(self.env.config(), FakeRunner(fail_build=True)).run()
        status = self.status()
        self.assertEqual(status["state"], "failed")
        self.assertEqual(status["step"], "build")
        self.assertEqual(ac.read_active(self.env.runtime), before)

    def test_hash_mismatch_fails_at_verify(self):
        out = write_bundle(self.env.confirm_root, "c1")
        (out / "timetable.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(ac.ConfirmError):
            ac.Applier(self.env.config(), FakeRunner()).run()
        status = self.status()
        self.assertEqual((status["state"], status["step"]), ("failed", "verify"))
        self.assertEqual(status["error"]["code"], "CONFIRM_HASH_MISMATCH")
        self.assertIsNone(ac.read_active(self.env.runtime))

    def test_same_inputs_reuse_built_graph(self):
        write_bundle(self.env.confirm_root, "c1")
        runner = FakeRunner()
        ac.Applier(self.env.config(), runner).run()
        ac.Applier(self.env.config(), runner).run()
        self.assertEqual(len(runner.builds), 1)
        self.assertTrue(self.status()["cache_hit"])

    def test_swap_alternates_slots_and_stops_old_server(self):
        write_bundle(self.env.confirm_root, "c1")
        runner = FakeRunner()
        ac.Applier(self.env.config(), runner).run()
        write_bundle(self.env.confirm_root, "c2", plan_extra={"note": "다른 안"})
        ac.Applier(self.env.config(), runner).run()
        self.assertEqual([port for _, _, port in runner.served], [18811, 18812])
        self.assertEqual(runner.stopped, ["fake-a"])
        self.assertEqual(ac.read_active(self.env.runtime)["confirm_id"], "c2")

    def test_only_newest_graphs_are_kept(self):
        runner = FakeRunner()
        for i in range(3):
            write_bundle(self.env.confirm_root, f"c{i}", plan_extra={"note": str(i)})
            ac.Applier(self.env.config(), runner).run()
        builds = [p for p in (self.env.runtime / "strike-graphs").iterdir() if p.is_dir()]
        self.assertEqual(len(builds), 2)
        self.assertIn(Path(ac.read_active(self.env.runtime)["dir"]), builds)

    def test_prebuilt_only_fails_without_cached_graph(self):
        write_bundle(self.env.confirm_root, "c1")
        runner = FakeRunner()
        with self.assertRaises(ac.ConfirmError) as caught:
            ac.Applier(self.env.config(), runner).run(prebuilt_only=True)
        self.assertEqual(caught.exception.code, "PREBUILT_MISSING")
        self.assertEqual(runner.builds, [])

    def test_switch_to_retained_build(self):
        runner = FakeRunner()
        write_bundle(self.env.confirm_root, "c1")
        ac.Applier(self.env.config(), runner).run()
        first = ac.read_active(self.env.runtime)["build_id"]
        write_bundle(self.env.confirm_root, "c2", plan_extra={"note": "다른 안"})
        ac.Applier(self.env.config(), runner).run()
        ac.Applier(self.env.config(), runner).switch(first)
        self.assertEqual(ac.read_active(self.env.runtime)["build_id"], first)
        self.assertEqual(ac.read_active(self.env.runtime)["confirm_id"], "c1")

    def test_same_plan_id_revision_confirms_are_told_apart(self):
        # 실제 파이프라인 안들은 plan_id@revision이 모두 같다(plan-eqmc07-f11@1).
        # 그래프·상태·활성 표시가 confirm_id와 plan_sha256으로 안을 구별해야 한다.
        runner = FakeRunner()
        first = write_bundle(self.env.confirm_root, "c1")
        ac.Applier(self.env.config(), runner).run()
        a1 = ac.read_active(self.env.runtime)
        second = write_bundle(self.env.confirm_root, "c2", plan_extra={"note": "다른 안"})
        ac.Applier(self.env.config(), runner).run()
        a2 = ac.read_active(self.env.runtime)
        sha1 = json.loads((first / "confirm.json").read_text(encoding="utf-8"))["plan_sha256"]
        sha2 = json.loads((second / "confirm.json").read_text(encoding="utf-8"))["plan_sha256"]
        self.assertEqual(a1["shuttle_version"], a2["shuttle_version"])  # 둘 다 plan-test@1
        self.assertEqual(a1["plan_key"], f"plan-test@1#{sha1[:8]}")
        self.assertEqual(a2["plan_key"], f"plan-test@1#{sha2[:8]}")
        self.assertNotEqual(a1["build_id"], a2["build_id"])
        status = self.status()
        self.assertEqual((status["confirm_id"], status["plan_key"]), ("c2", a2["plan_key"]))

    def test_cached_graph_built_from_other_bundle_is_not_swapped_in(self):
        # 같은 build 폴더라도 셔틀 입력 해시가 이 확정 묶음 파일과 다르면 교체하지 않는다.
        write_bundle(self.env.confirm_root, "c1")
        runner = FakeRunner()
        ac.Applier(self.env.config(), runner).run()
        before = ac.read_active(self.env.runtime)
        manifest_path = Path(before["dir"]) / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["shuttle_source"]["input_sha256"]["timetable"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(ac.ConfirmError) as caught:
            ac.Applier(self.env.config(), runner).run()
        self.assertEqual(caught.exception.code, "GRAPH_VERSION_MISMATCH")
        self.assertEqual(ac.read_active(self.env.runtime), before)

    def test_status_never_contains_passenger_fields(self):
        write_bundle(self.env.confirm_root, "c1")
        ac.Applier(self.env.config(), FakeRunner()).run()
        text = (self.env.runtime / "apply" / "status.json").read_text(encoding="utf-8")
        for word in ("TRCR_ID", "card_id", "passenger_id"):
            self.assertNotIn(word, text)


if __name__ == "__main__":
    unittest.main()


class ShuttleExportConfirmTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bundle = write_bundle(self.root / "cr", "c1")
        self.args = [self.bundle / n for n in ("scenario.json", "plan.json", "timetable.json",
                                                "verifier_report.json", "stops.csv")]

    def tearDown(self):
        self.tmp.cleanup()

    def test_draft_without_demo_or_confirm_is_still_rejected(self):
        from backend.shuttle_export import export_checked
        with self.assertRaises(Exception):
            export_checked(*self.args, self.root / "out")

    def test_confirmed_draft_is_exported_with_gtfs_date_and_simulated_date(self):
        from backend.shuttle_export import export_checked
        confirm = json.loads((self.bundle / "confirm.json").read_text(encoding="utf-8"))
        meta = export_checked(*self.args, self.root / "out", service_date="2026-11-07", confirm=confirm)
        self.assertEqual((meta["service_date"], meta["simulated_service_date"]), ("2026-11-07", "2026-10-28"))
        self.assertEqual(meta["status"], "operator_confirmed_not_approved")
        self.assertEqual(meta["confirm"]["confirm_id"], "c1")
        calendar = (self.root / "out" / "calendar.txt").read_text(encoding="utf-8")
        self.assertIn("20261107", calendar)

    def test_demo_and_confirm_together_are_rejected(self):
        from backend.shuttle_export import export_checked
        confirm = json.loads((self.bundle / "confirm.json").read_text(encoding="utf-8"))
        with self.assertRaises(Exception):
            export_checked(*self.args, self.root / "out", demo=True, confirm=confirm)
