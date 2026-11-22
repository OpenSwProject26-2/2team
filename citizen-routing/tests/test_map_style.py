import json
import unittest
from pathlib import Path

from backend.citizen_map_style import BASE, CITIZEN_SOURCE, TIERS, citizen_style


class CitizenMapStyleTests(unittest.TestCase):
    def test_transit_layers_and_vendor_style_are_preserved(self):
        original = json.loads(BASE.read_text(encoding="utf-8"))
        citizen = citizen_style()
        original_layers = {layer["id"]: layer for layer in original["layers"]}
        citizen_layers = {layer["id"]: layer for layer in citizen["layers"]}
        self.assertEqual(citizen["sources"]["openmaptiles"], original["sources"]["openmaptiles"])
        self.assertEqual(citizen_layers["poi_transit"], original_layers["poi_transit"])
        for layer_id, layer in original_layers.items():
            if layer_id not in {"poi_r1", "poi_r7", "poi_r20"}:
                self.assertEqual(citizen_layers[layer_id], layer)
        self.assertEqual(citizen["sources"][CITIZEN_SOURCE]["type"], "geojson")

    def test_poi_layers_have_gradual_zooms_and_collision_priority(self):
        layers = {layer["id"]: layer for layer in citizen_style()["layers"]}
        self.assertEqual([tier[2] for tier in TIERS], sorted(tier[2] for tier in TIERS))
        for tier, _, early, _, detail in TIERS:
            self.assertLess(early, detail)
            for suffix, zoom in (("early", early), ("detail", detail)):
                layer = layers[f"citizen-poi-{tier}-{suffix}"]
                self.assertEqual(layer["minzoom"], zoom)
                self.assertEqual(layer["source-layer"], "poi")
                self.assertEqual(layer["layout"]["symbol-sort-key"],
                                 ["coalesce", ["get", "rank"], 9999])
                self.assertFalse(layer["layout"]["icon-allow-overlap"])
                self.assertFalse(layer["layout"]["text-allow-overlap"])
        self.assertEqual(layers["citizen-osm-major"]["minzoom"], 11.5)
        self.assertEqual(layers["citizen-osm-civic"]["minzoom"], 12.7)
        self.assertEqual(layers["citizen-osm-worship"]["minzoom"], 13.5)


if __name__ == "__main__":
    unittest.main()