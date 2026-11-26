import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from busstrike.api.d_dashboard import DEMO
from busstrike.api.d_geo import load_demo
from busstrike.api.d_strike_registry import strike_registry_view


class StrikeRegistryTests(unittest.TestCase):
    def test_dated_registry_matches_id_and_name_but_not_operator_or_direction(self) -> None:
        snapshot = load_demo(DEMO)
        route = next(row for row in snapshot.routes if row["route_id"] == "demo-city-421")
        member = "guro_0610_v4/registry/strike_route_registry.csv"
        content = (
            "route_id,route_name,operator_id,operator_name,strike_state,valid_from,valid_to,match_status\n"
            f"demo-city-421,{route['short_name']},x,unknown,affected,2026-01-13,2026-01-14,rule_only\n"
        ).encode()

        class Bundle:
            def __enter__(self): return self
            def __exit__(self, *_): return None
            def namelist(self): return [member]
            def read(self, name): return content

        with patch.object(Path, "is_file", return_value=True), patch(
            "busstrike.api.d_strike_registry.zipfile.ZipFile", return_value=Bundle()
        ):
            result = strike_registry_view(Path("test-root"), snapshot, "guro")
        self.assertEqual(result["status"], "historical_model_scenario")
        self.assertEqual(result["matched_id_name"], 1)
        self.assertFalse(result["routes"][0]["operator_verified"])
        self.assertFalse(result["routes"][0]["direction_verified"])
