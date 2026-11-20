"""Search the local OSM place index without sending queries to external services."""
from __future__ import annotations

import sqlite3
from math import cos, radians, sqrt
from contextlib import closing
from pathlib import Path


def compact(value: str) -> str:
    return "".join(value.casefold().split())



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
