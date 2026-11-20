import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path

from backend.place_search import compact, nearby, search


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


if __name__ == "__main__":
    unittest.main()
