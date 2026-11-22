"""Classify bus legs from prepared GTFS route metadata."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import zipfile

from .gtfs_adapter import FeedError, rows


def classify_bus(route: dict[str, str]) -> str:
    description = (route.get("route_desc") or "").replace(" ", "")
    agency_id = (route.get("agency_id") or "").upper()
    if "\uacbd\uae30" in description or agency_id.startswith(("GYEONGGI", "GG_")):
        return "gyeonggi"
    if "\ub9c8\uc744" in description:
        return "village"
    if any(word in description for word in ("\uac04\uc120", "\uc9c0\uc120", "\uc21c\ud658", "\uad11\uc5ed", "\uc2dc\ub0b4")):
        return "city"
    return "unclassified"


@lru_cache(maxsize=4)
def _from_feed(path: str, modified_ns: int, size: int) -> dict[str, str]:
    with zipfile.ZipFile(path) as feed:
        _, routes = rows({"routes.txt": feed.read("routes.txt")}, "routes.txt")
    return {f"A:{route['route_id']}": classify_bus(route) for route in routes
            if route.get("route_id") and route.get("route_type") == "3"}


def load_categories(otp_dir: Path) -> dict[str, str]:
    path = otp_dir / "existing.gtfs.zip"
    try:
        stamp = path.stat()
        return _from_feed(str(path), stamp.st_mtime_ns, stamp.st_size)
    except (OSError, zipfile.BadZipFile, KeyError, FeedError):
        return {}