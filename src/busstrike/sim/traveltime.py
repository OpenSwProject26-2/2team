"""시간대별 구간 시간 표(simulator.md §0.3 travel_times.json, §3.5)."""

from __future__ import annotations

from bisect import bisect_right
from typing import Any

from .models import SimError, Window


def _int(raw: Any, name: str, minimum: int | None = None) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise SimError(SimError.INPUT_INVALID, f"{name} must be int", {"field": name})
    if minimum is not None and raw < minimum:
        raise SimError(SimError.INPUT_INVALID, f"{name} must be >= {minimum}", {"field": name})
    return raw


class TravelTimeTable:
    """(from_stop_id, to_stop_id) 방향 구간의 시간대별 주행 시간과 거리."""

    def __init__(self, network_snapshot_id: str, valid_from_s: int, valid_until_s: int,
                 segments: dict[tuple[str, str], tuple[int, tuple[int, ...], tuple[int, ...]]]):
        self.network_snapshot_id = network_snapshot_id
        self.valid_from_s = valid_from_s
        self.valid_until_s = valid_until_s
        self._segments = segments
        self._bin_starts = tuple(sorted({b for _, bins, _ in segments.values() for b in bins}))
        self._adjacency: dict[str, tuple[str, ...]] | None = None

    @classmethod
    def from_dict(cls, raw: dict) -> "TravelTimeTable":
        if not isinstance(raw, dict):
            raise SimError(SimError.INPUT_INVALID, "travel_times must be an object")
        if raw.get("schema_version") != 1:
            raise SimError(SimError.INPUT_INVALID, "travel_times.schema_version must be 1")
        snapshot = raw.get("network_snapshot_id")
        if not isinstance(snapshot, str):
            raise SimError(SimError.INPUT_INVALID, "network_snapshot_id must be string")
        valid_from = _int(raw.get("valid_from_s"), "valid_from_s")
        valid_until = _int(raw.get("valid_until_s"), "valid_until_s")
        if valid_until < valid_from:
            raise SimError(SimError.INPUT_INVALID, "valid_until_s < valid_from_s")
        items = raw.get("segments")
        if not isinstance(items, list):
            raise SimError(SimError.INPUT_INVALID, "segments must be a list")
        segments: dict[tuple[str, str], tuple[int, tuple[int, ...], tuple[int, ...]]] = {}
        prev_key: tuple[str, str] | None = None
        for item in items:
            if not isinstance(item, dict):
                raise SimError(SimError.INPUT_INVALID, "segment must be an object")
            a, b = item.get("from_stop_id"), item.get("to_stop_id")
            if not isinstance(a, str) or not isinstance(b, str) or a == b:
                raise SimError(SimError.INPUT_INVALID, "segment stop ids invalid", {"segment": [a, b]})
            key = (a, b)
            if prev_key is not None and key <= prev_key:
                raise SimError(SimError.INPUT_INVALID, "segments must be strictly ascending by (from, to)",
                               {"segment": [a, b]})
            prev_key = key
            distance = _int(item.get("distance_m"), "distance_m", 0)
            profile = item.get("profile")
            if not isinstance(profile, list) or not profile:
                raise SimError(SimError.INPUT_INVALID, "profile must be a non-empty list", {"segment": [a, b]})
            bins: list[int] = []
            travel: list[int] = []
            for pair in profile:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise SimError(SimError.INPUT_INVALID, "profile entry must be [bin_start_s, travel_s]",
                                   {"segment": [a, b]})
                start = _int(pair[0], "bin_start_s")
                secs = _int(pair[1], "travel_s", 1)
                if bins and start <= bins[-1]:
                    raise SimError(SimError.INPUT_INVALID, "profile bins must be strictly ascending",
                                   {"segment": [a, b]})
                bins.append(start)
                travel.append(secs)
            if bins[0] > valid_from:
                raise SimError(SimError.DATA_INCOMPLETE, "profile starts after valid_from_s", {"segment": [a, b]})
            segments[key] = (distance, tuple(bins), tuple(travel))
        return cls(snapshot, valid_from, valid_until, segments)

    def check_window(self, window: Window) -> None:
        if self.valid_from_s > window.start_s or self.valid_until_s < window.end_s:
            raise SimError(SimError.DATA_INCOMPLETE, "travel times do not cover the operating window",
                           {"valid_from_s": self.valid_from_s, "valid_until_s": self.valid_until_s,
                            "window": [window.start_s, window.end_s]})

    def has(self, from_stop_id: str, to_stop_id: str) -> bool:
        return (from_stop_id, to_stop_id) in self._segments

    def stop_ids(self) -> frozenset[str]:
        """구간 표에 한 번이라도 나오는 정류장. M05가 아는 망의 정류장은 이것뿐이다."""
        return frozenset(s for key in self._segments for s in key)

    def successors(self, from_stop_id: str) -> tuple[str, ...]:
        """from_stop_id에서 바로 갈 수 있는 정류장(오름차순)."""
        if self._adjacency is None:
            adj: dict[str, list[str]] = {}
            for a, b in sorted(self._segments):
                adj.setdefault(a, []).append(b)
            self._adjacency = {a: tuple(bs) for a, bs in adj.items()}
        return self._adjacency.get(from_stop_id, ())

    def _segment(self, from_stop_id: str, to_stop_id: str):
        try:
            return self._segments[(from_stop_id, to_stop_id)]
        except KeyError:
            raise SimError(SimError.DATA_INCOMPLETE, "missing segment",
                           {"segment": [from_stop_id, to_stop_id]}) from None

    def lookup(self, from_stop_id: str, to_stop_id: str, t: int) -> int:
        """상류 정류장 출발 시각 t 기준의 주행 시간. 경계 포함(§3.5)."""
        _, bins, travel = self._segment(from_stop_id, to_stop_id)
        i = bisect_right(bins, t) - 1
        if i < 0:
            raise SimError(SimError.DATA_INCOMPLETE, "no time bin for departure",
                           {"segment": [from_stop_id, to_stop_id], "t": t})
        return travel[i]

    def distance(self, from_stop_id: str, to_stop_id: str) -> int:
        return self._segment(from_stop_id, to_stop_id)[0]

    def bin_starts(self) -> tuple[int, ...]:
        return self._bin_starts
