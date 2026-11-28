"""Read the longest active GTFS trip for display order, retaining repeated stops."""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import date
from functools import lru_cache
from pathlib import Path


def _rows(archive: zipfile.ZipFile, basename: str):
    name = next((name for name in archive.namelist() if name.rsplit("/", 1)[-1].lower() == basename), None)
    if name is None:
        return ()
    with archive.open(name) as stream, io.TextIOWrapper(stream, encoding="utf-8-sig", newline="") as text:
        yield from csv.DictReader(text)


@lru_cache(maxsize=256)
def longest_trip_stop_ids(gtfs_path: Path, service_date: str, route_id: str) -> tuple[str, ...]:
    day = date.fromisoformat(service_date)
    compact_day = day.strftime("%Y%m%d")
    weekday = day.strftime("%A").lower()
    with zipfile.ZipFile(gtfs_path) as archive:
        active = {row["service_id"] for row in _rows(archive, "calendar.txt")
                  if row.get("start_date", "99999999") <= compact_day <= row.get("end_date", "00000000")
                  and row.get(weekday) == "1"}
        for row in _rows(archive, "calendar_dates.txt"):
            if row.get("date") == compact_day:
                if row.get("exception_type") == "1":
                    active.add(row["service_id"])
                elif row.get("exception_type") == "2":
                    active.discard(row["service_id"])
        trip_ids = {row["trip_id"] for row in _rows(archive, "trips.txt")
                    if row.get("route_id") == route_id and row.get("service_id") in active}
        if not trip_ids:
            return ()
        calls: dict[str, list[tuple[int, str]]] = {trip_id: [] for trip_id in trip_ids}
        for row in _rows(archive, "stop_times.txt"):
            trip_id = row.get("trip_id")
            if trip_id in calls:
                calls[trip_id].append((int(row["stop_sequence"]), row["stop_id"]))
        best = max(sorted(calls), key=lambda trip_id: len(calls[trip_id]))
        return tuple(stop_id for _, stop_id in sorted(calls[best]))


@lru_cache(maxsize=128)
def major_trip_patterns(gtfs_path: Path, service_date: str, route_id: str) -> tuple[tuple[str, ...], ...]:
    """Take the longest active stop sequence in each GTFS pattern family (p0/p1)."""
    day = date.fromisoformat(service_date)
    compact_day = day.strftime("%Y%m%d")
    weekday = day.strftime("%A").lower()
    with zipfile.ZipFile(gtfs_path) as archive:
        active = {row["service_id"] for row in _rows(archive, "calendar.txt")
                  if row.get("start_date", "99999999") <= compact_day <= row.get("end_date", "00000000")
                  and row.get(weekday) == "1"}
        for row in _rows(archive, "calendar_dates.txt"):
            if row.get("date") == compact_day:
                if row.get("exception_type") == "1":
                    active.add(row["service_id"])
                elif row.get("exception_type") == "2":
                    active.discard(row["service_id"])
        trip_ids = {row["trip_id"] for row in _rows(archive, "trips.txt")
                    if row.get("route_id") == route_id and row.get("service_id") in active}
        calls: dict[str, list[tuple[int, str]]] = {trip_id: [] for trip_id in trip_ids}
        for row in _rows(archive, "stop_times.txt"):
            if row.get("trip_id") in calls:
                calls[row["trip_id"]].append((int(row["stop_sequence"]), row["stop_id"]))
    families: dict[str, tuple[str, ...]] = {}
    for trip_id in sorted(calls):
        sequence = tuple(stop_id for _, stop_id in sorted(calls[trip_id]))
        family = trip_id.rsplit("_", 1)[0] if trip_id.rsplit("_", 1)[-1].isdigit() else trip_id
        if len(sequence) > len(families.get(family, ())):
            families[family] = sequence
    return tuple(dict.fromkeys(families.values()))
