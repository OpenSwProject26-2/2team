"""Citizen-only POI layers over the vendored OpenFreeMap Liberty style."""
from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path

BASE = Path(__file__).resolve().parents[1] / "frontend" / "vendor" / "openfreemap-liberty.json"
GENERAL_IDS = {"poi_r1", "poi_r7", "poi_r20"}
TRANSIT_CLASSES = ["airport", "bus", "rail", "railway"]
POINT = ["match", ["geometry-type"], ["MultiPoint", "Point"], True, False]
RANK = ["coalesce", ["get", "rank"], 9999]


def classes(*names):
    return ["match", ["get", "class"], list(names), True, False]


MAJOR = ["any", classes("town_hall", "college", "attraction", "art_gallery",
                         "theatre", "stadium", "castle"),
         ["all", classes("hospital"), ["!=", ["get", "subclass"], "clinic"]]]
CIVIC = ["any", classes("school", "library", "grocery", "post"),
         ["all", classes("hospital"), ["==", ["get", "subclass"], "clinic"]]]
WORSHIP = classes("place_of_worship")
FOOD = classes("restaurant", "cafe", "fast_food")
CONVENIENCE = ["==", ["get", "subclass"], "convenience"]
COMMERCE = ["all", classes("shop", "clothing_store", "pharmacy", "bakery",
                           "bank", "office"), ["!", CONVENIENCE]]
OTHER = ["all", ["!", classes(*TRANSIT_CLASSES)],
         *[["!", group] for group in (MAJOR, CIVIC, WORSHIP, FOOD, CONVENIENCE, COMMERCE)]]

# minzoom, local rank cutoff, later minzoom. Rank is local tile-grid order,
# not a claim about facility size or popularity.
TIERS = [
    ("major", MAJOR, 14, 12, 15.2),
    ("civic", CIVIC, 14.6, 18, 16),
    ("worship", WORSHIP, 15, 20, 16.5),
    ("food", FOOD, 16, 25, 17.2),
    ("convenience", CONVENIENCE, 16.3, 30, 17.3),
    ("commerce", COMMERCE, 17, 25, 18),
    ("other", OTHER, 18, 30, 19),
]
LOW_TIERS = [("major", 11.5), ("civic", 12.7), ("worship", 13.5)]
CITIZEN_SOURCE = "citizen-osm-poi"


def vector_layer(template, group, tier, minzoom, rank_cutoff, early):
    layer = copy.deepcopy(template)
    layer["id"] = f"citizen-poi-{tier}-{'early' if early else 'detail'}"
    layer["minzoom"] = minzoom
    layer.pop("maxzoom", None)
    rank_filter = ["<=" if early else ">", RANK, rank_cutoff]
    layer["filter"] = ["all", POINT, group, rank_filter]
    layout = layer["layout"]
    layout.update({
        "text-field": ["coalesce", ["get", "name:ko"], ["get", "name:nonlatin"],
                       ["get", "name"]],
        "symbol-sort-key": RANK,
        "symbol-z-order": "auto",
        "text-allow-overlap": False,
        "icon-allow-overlap": False,
        "text-optional": True,
        "icon-optional": True,
        "text-padding": 5,
        "icon-padding": 3,
        "text-size": ["interpolate", ["linear"], ["zoom"], 14, 10, 16, 11, 19, 12],
        "icon-size": ["interpolate", ["linear"], ["zoom"], 14, 0.65, 17, 0.8, 19, 0.9],
    })
    fade = ["interpolate", ["linear"], ["zoom"], minzoom, 0, minzoom + 0.5, 1]
    layer["paint"].update({"text-opacity": fade, "icon-opacity": fade})
    return layer


def local_layer(tier, minzoom):
    fade = ["interpolate", ["linear"], ["zoom"],
            minzoom, 0, minzoom + 0.4, 1, 14.5, 1, 15.5, 0]
    return {
        "id": f"citizen-osm-{tier}", "type": "symbol", "source": CITIZEN_SOURCE,
        "minzoom": minzoom, "maxzoom": 15.5,
        "filter": ["==", ["get", "category"], tier],
        "layout": {
            "icon-image": ["get", "icon"],
            "symbol-sort-key": ["get", "priority"],
            "symbol-z-order": "auto",
            "text-field": ["get", "name"],
            "text-font": ["Noto Sans Regular"],
            "text-anchor": "top", "text-offset": [0, 0.65],
            "text-max-width": 9,
            "text-size": ["interpolate", ["linear"], ["zoom"], 11.5, 10, 14.5, 12],
            "icon-size": ["interpolate", ["linear"], ["zoom"], 11.5, 0.6, 14.5, 0.8],
            "text-allow-overlap": False, "icon-allow-overlap": False,
            "text-optional": True, "icon-optional": True,
            "text-padding": 5, "icon-padding": 3,
        },
        "paint": {
            "text-color": "#284741", "text-halo-color": "#fff", "text-halo-width": 1.5,
            "text-opacity": fade, "icon-opacity": fade,
        },
    }


@lru_cache(maxsize=1)
def citizen_style():
    style = json.loads(BASE.read_text(encoding="utf-8"))
    original = style["layers"]
    template = next(layer for layer in original if layer["id"] == "poi_r1")
    first = next(i for i, layer in enumerate(original) if layer["id"] == "poi_r20")
    native = []
    for tier, group, early_zoom, cutoff, detail_zoom in reversed(TIERS):
        native.append(vector_layer(template, group, tier, detail_zoom, cutoff, False))
        native.append(vector_layer(template, group, tier, early_zoom, cutoff, True))
    local = [local_layer(tier, zoom) for tier, zoom in reversed(LOW_TIERS)]
    transit_only = []
    for layer in original:
        if layer["id"] in GENERAL_IDS:
            copy_layer = copy.deepcopy(layer)
            copy_layer["filter"] = ["all", layer["filter"], classes(*TRANSIT_CLASSES)]
            transit_only.append(copy_layer)
    style["sources"][CITIZEN_SOURCE] = {
        "type": "geojson", "data": {"type": "FeatureCollection", "features": []},
    }
    style["layers"] = (
        [layer for layer in original[:first] if layer["id"] not in GENERAL_IDS]
        + native + local + transit_only
        + [layer for layer in original[first:] if layer["id"] not in GENERAL_IDS]
    )
    return style


if __name__ == "__main__":
    print(json.dumps(citizen_style(), ensure_ascii=False, separators=(",", ":")))