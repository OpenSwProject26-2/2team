"""Search the local OSM place index without sending queries to external services."""
from __future__ import annotations

import sqlite3
from math import cos, radians, sqrt
from contextlib import closing
from pathlib import Path


def compact(value: str) -> str:
    return "".join(value.casefold().split())



VISIBLE_KINDS = {
    "major": {
        "amenity:hospital": ("hospital", 1),
        "amenity:university": ("college", 2),
        "amenity:townhall": ("town_hall", 3),
        "tourism:museum": ("museum", 4),
        "tourism:attraction": ("attraction", 5),
        "historic:castle": ("castle", 6),
    },
    "civic": {
        "amenity:school": ("school", 1),
        "building:school": ("school", 2),
        "amenity:library": ("library", 3),
        "amenity:clinic": ("hospital", 4),
        "shop:supermarket": ("grocery", 5),
    },
    "worship": {
        "amenity:place_of_worship": ("place_of_worship", 1),
        "building:church": ("place_of_worship", 2),
        "building:cathedral": ("place_of_worship", 2),
        "building:temple": ("place_of_worship", 2),
    },
}
VISIBLE_LOOKUP = {
    kind: (category, icon, priority)
    for category, kinds in VISIBLE_KINDS.items()
    for kind, (icon, priority) in kinds.items()
}


def visible(index: Path, west: float, south: float, east: float, north: float,
            zoom: float, limit: int = 540) -> list[dict]:
    """Supply low-zoom OSM POIs missing from OpenFreeMap tiles below z14."""
    if zoom < 11 or zoom >= 15.5 or not index.is_file():
        return []
    kinds = tuple(VISIBLE_LOOKUP)
    markers = ",".join("?" for _ in kinds)
    with closing(sqlite3.connect(f"file:{index.as_posix()}?mode=ro", uri=True, timeout=3)) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            f"SELECT id,name,address,lat,lon,kind FROM places "
            f"WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? "
            f"AND kind IN ({markers}) AND name <> address",
            (south, north, west, east, *kinds),
        ).fetchall()
    cell = 0.016 * 2 ** (12 - min(zoom, 14))
    picked = {}
    for row in rows:
        place = dict(row)
        category, icon, priority = VISIBLE_LOOKUP[place["kind"]]
        y, x = int(place["lat"] / cell), int(place["lon"] / cell)
        key = (y, x, category)
        offset = (place["lat"] / cell - y - 0.5) ** 2 + (place["lon"] / cell - x - 0.5) ** 2
        candidate = (priority, offset, place["name"])
        current = picked.get(key)
        if current is None or candidate < current[0]:
            place.update(category=category, icon=icon, priority=priority)
            picked[key] = (candidate, place)
    mid_lat, mid_lon = (south + north) / 2, (west + east) / 2
    by_category = {category: [] for category in VISIBLE_KINDS}
    for _, place in picked.values():
        by_category[place["category"]].append(place)
    quota = max(1, min(limit // len(VISIBLE_KINDS), 200))
    result = []
    for category, places in by_category.items():
        places.sort(key=lambda place: (place["priority"],
                                      (place["lat"] - mid_lat) ** 2
                                      + ((place["lon"] - mid_lon)
                                         * cos(radians(mid_lat))) ** 2,
                                      place["name"]))
        result.extend(places[:quota])
    return result[:max(1, min(limit, 600))]

def nearby(index: Path, lat: float, lon: float, radius_m: float = 80, limit: int = 6) -> list[dict]:
    if not index.is_file():
        return []
    lat_span = radius_m / 111_320
    lon_span = lat_span / max(cos(radians(lat)), 0.1)
    with closing(sqlite3.connect(f"file:{index.as_posix()}?mode=ro", uri=True, timeout=3)) as db:
        db.row_factory = sqlite3.Row
        candidates = db.execute(
            "SELECT id,name,address,lat,lon,kind FROM places "
            "WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
            (lat - lat_span, lat + lat_span, lon - lon_span, lon + lon_span),
        ).fetchall()
    places = []
    for row in candidates:
        place = dict(row)
        north = (place["lat"] - lat) * 111_320
        east = (place["lon"] - lon) * 111_320 * cos(radians(lat))
        distance = sqrt(north * north + east * east)
        if distance <= radius_m:
            place["distance_m"] = round(distance)
            places.append(place)
    places.sort(key=lambda place: (place["distance_m"], place["name"]))
    unique = []
    names = set()
    for place in places:
        if place["name"] in names:
            continue
        names.add(place["name"])
        unique.append(place)
        if len(unique) >= max(1, min(limit, 10)):
            break
    return unique


def search(index: Path, query: str, limit: int = 20) -> list[dict]:
    term = compact(query)
    if len(term) < 2 or not index.is_file():
        return []
    with closing(sqlite3.connect(f"file:{index.as_posix()}?mode=ro", uri=True, timeout=3)) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT id,name,address,lat,lon,kind FROM places "
            "WHERE instr(search_text, ?) > 0 "
            "ORDER BY CASE WHEN name_key = ? THEN 0 "
            "WHEN substr(name_key, 1, length(?)) = ? THEN 1 ELSE 2 END, "
            "CASE WHEN kind LIKE 'historic:%' OR kind LIKE 'tourism:%' THEN 0 "
            "WHEN kind IN ('amenity:townhall','amenity:hospital','amenity:university',"
            "'amenity:school','amenity:library') THEN 1 "
            "WHEN kind LIKE 'building:%' THEN 2 "
            "WHEN kind LIKE 'railway:%' OR kind LIKE 'public_transport:%' THEN 3 "
            "ELSE 4 END, length(name), name LIMIT ?",
            (term, term, term, term, max(1, min(limit, 30))),
        ).fetchall()
    return [dict(row) for row in rows]
