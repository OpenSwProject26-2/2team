import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path

from backend.place_search import compact, nearby, search, visible


class PlaceSearchTests(unittest.TestCase):
    def test_korean_name_and_space_insensitive_search(self):
        with tempfile.TemporaryDirectory() as directory:
            index = Path(directory) / "places.sqlite"
            with closing(sqlite3.connect(index)) as db:
                db.execute("CREATE TABLE places(id TEXT, name TEXT, name_key TEXT, address TEXT, lat REAL, lon REAL, kind TEXT, search_text TEXT)")
                db.executemany("INSERT INTO places VALUES (?,?,?,?,?,?,?,?)", [
                    ("1", "\uad6d\ub9bd\uc911\uc559\ubc15\ubb3c\uad00", compact("\uad6d\ub9bd\uc911\uc559\ubc15\ubb3c\uad00"), "", 37.52, 126.98, "tourism:museum", compact("\uad6d\ub9bd\uc911\uc559\ubc15\ubb3c\uad00")),
                    ("2", "\uad6d\ub9bd\uc911\uc559\ubc15\ubb3c\uad00 \ubcc4\uad00", compact("\uad6d\ub9bd\uc911\uc559\ubc15\ubb3c\uad00 \ubcc4\uad00"), "", 37.53, 126.99, "building:yes", compact("\uad6d\ub9bd\uc911\uc559\ubc15\ubb3c\uad00 \ubcc4\uad00")),
                ])
                db.commit()
            result = search(index, "\uad6d\ub9bd \uc911\uc559\ubc15\ubb3c\uad00")
            self.assertEqual([row["id"] for row in result], ["1", "2"])
            self.assertEqual(search(index, "x"), [])
            self.assertEqual([row["id"] for row in nearby(index, 37.52, 126.98)], ["1"])
            self.assertEqual(nearby(index, 37.4, 127.1), [])


    def test_visible_facilities_are_thinned_for_low_zoom_style(self):
        with tempfile.TemporaryDirectory() as directory:
            index = Path(directory) / "places.sqlite"
            with closing(sqlite3.connect(index)) as db:
                db.execute("CREATE TABLE places(id TEXT,name TEXT,name_key TEXT,address TEXT,lat REAL,lon REAL,kind TEXT,search_text TEXT)")
                entries = [
                    ("1", "Hospital", 37.54, 126.89, "amenity:hospital"),
                    ("2", "School A", 37.55, 126.90, "amenity:school"),
                    ("3", "School B", 37.5501, 126.9001, "building:school"),
                    ("4", "Church", 37.56, 126.91, "amenity:place_of_worship"),
                    ("5", "Cafe", 37.57, 126.92, "amenity:cafe"),
                ]
                db.executemany("INSERT INTO places VALUES (?,?,?,?,?,?,?,?)", [
                    (id, name, compact(name), "", lat, lon, kind, compact(name))
                    for id, name, lat, lon, kind in entries
                ])
                db.commit()
            bounds = (126.8, 37.5, 127.0, 37.6)
            self.assertEqual(visible(index, *bounds, 10), [])
            places = visible(index, *bounds, 11)
            self.assertEqual({place["category"] for place in places},
                             {"major", "civic", "worship"})
            self.assertEqual(sum(place["category"] == "civic" for place in places), 1)
            self.assertEqual({place["icon"] for place in places},
                             {"hospital", "school", "place_of_worship"})
            self.assertNotIn("Cafe", {place["name"] for place in places})
            self.assertEqual(visible(index, *bounds, 15.5), [])

if __name__ == "__main__":
    unittest.main()
