"""Build an offline place-name index from the same OSM extract used by OTP."""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime" / "python-libs"))
try:
    import osmium
except ImportError as error:
    raise SystemExit("Install osmium: py -3 -m pip install --target runtime/python-libs osmium==4.3.2") from error

SEARCH_TAGS = ("amenity", "tourism", "leisure", "shop", "healthcare",
               "office", "historic", "railway", "public_transport", "building")
NAME_TAGS = ("name:ko", "name", "official_name", "brand", "alt_name", "name:en")
BOUNDS = (37.0, 126.0, 38.0, 128.0)


def compact(value: str) -> str:
    return "".join(value.casefold().split())


def data_for(obj, lat: float, lon: float, object_type: str):
    if not BOUNDS[0] <= lat <= BOUNDS[2] or not BOUNDS[1] <= lon <= BOUNDS[3]:
        return None
    tags = obj.tags
    key = next((key for key in SEARCH_TAGS if tags.get(key)), None)
    if not key:
        return None
    names = [tags.get(key) for key in NAME_TAGS if tags.get(key)]
    street = tags.get("addr:street") or tags.get("addr:place") or ""
    number = tags.get("addr:housenumber") or ""
    address = " ".join(part for part in (tags.get("addr:city"), tags.get("addr:district"), street, number) if part)
    name = names[0] if names else (address if number else "")
    if not name or len(name) > 120:
        return None
    terms = [*names, address, tags.get("addr:full") or ""]
    search_text = " ".join(compact(term) for term in terms if term)
    if not search_text:
        return None
    kind = f"{key}:{tags.get(key)}"
    source_id = obj.orig_id() if object_type == "R" else obj.id
    return (f"OSM:{object_type}:{source_id}", name, compact(name), address,
            round(lat, 6), round(lon, 6), kind, search_text)


class Indexer(osmium.SimpleHandler):
    def __init__(self, db):
        super().__init__()
        self.db = db
        self.pending = []
        self.count = 0

    def add(self, row):
        if row is None:
            return
        self.pending.append(row)
        self.count += 1
        if len(self.pending) >= 1000:
            self.flush()

    def flush(self):
        if self.pending:
            self.db.executemany(
                "INSERT OR REPLACE INTO places(id,name,name_key,address,lat,lon,kind,search_text) "
                "VALUES (?,?,?,?,?,?,?,?)", self.pending)
            self.pending.clear()

    def node(self, node):
        if node.location.valid():
            self.add(data_for(node, node.location.lat, node.location.lon, "N"))

    def way(self, way):
        if not any(way.tags.get(key) for key in SEARCH_TAGS):
            return
        points = [(ref.location.lat, ref.location.lon) for ref in way.nodes if ref.location.valid()]
        if not points:
            return
        if len(points) > 1 and points[0] == points[-1]:
            points.pop()
        lat = sum(point[0] for point in points) / len(points)
        lon = sum(point[1] for point in points) / len(points)
        self.add(data_for(way, lat, lon, "W"))

    def area(self, area):
        if area.from_way() or not any(area.tags.get(key) for key in SEARCH_TAGS):
            return
        points = [(ref.location.lat, ref.location.lon)
                  for ring in area.outer_rings() for ref in ring if ref.location.valid()]
        if not points:
            return
        lat = sum(point[0] for point in points) / len(points)
        lon = sum(point[1] for point in points) / len(points)
        self.add(data_for(area, lat, lon, "R"))


def build(osm_path: Path, output: Path) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    with sqlite3.connect(output) as db:
        db.execute("CREATE TABLE places(id TEXT PRIMARY KEY,name TEXT NOT NULL,name_key TEXT NOT NULL,"
                   "address TEXT NOT NULL,lat REAL NOT NULL,lon REAL NOT NULL,"
                   "kind TEXT NOT NULL,search_text TEXT NOT NULL)")
        indexer = Indexer(db)
        indexer.apply_file(str(osm_path), locations=True)
        indexer.flush()
        db.execute("CREATE INDEX places_name ON places(name_key)")
        db.execute("CREATE INDEX places_coords ON places(lat,lon)")
        db.commit()
        return indexer.count


def main():
    parser = argparse.ArgumentParser(description="Build a local OSM building and facility search index")
    parser.add_argument("--osm", type=Path, default=ROOT / "runtime" / "downloads" / "seoul.osm.pbf")
    parser.add_argument("--output", type=Path, default=ROOT / "runtime" / "places.sqlite")
    args = parser.parse_args()
    if not args.osm.is_file():
        parser.error(f"OSM extract not found: {args.osm}")
    count = build(args.osm, args.output)
    print(f"Indexed {count} places: {args.output}")


if __name__ == "__main__":
    main()
