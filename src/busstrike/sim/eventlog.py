"""이벤트 로그 정렬·직렬화(simulator.md §6.1). 정렬 키가 로그 순서의 정의다."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Mapping, Sequence

from . import io
from .models import Event

_PHASE = {"no_service": 0, "abandon": 2, "left_waiting": 3}
_TYPE_RANK = {"arrive": 0, "alight": 1, "board": 2, "denied": 3, "depart": 4, "cancel": 0}
PASSENGER_OUTCOMES = ("no_service", "abandon", "left_waiting")


def canonical_key(event: Event, arrival_s_by_passenger: Mapping[str, int]) -> tuple:
    phase = _PHASE.get(event.type, 1)
    if phase != 1:
        return (event.t, phase, "", 0, 0, (event.passenger_id,))
    if event.type == "alight":
        order: tuple = (event.passenger_id,)
    elif event.type in ("board", "denied"):
        order = (arrival_s_by_passenger[event.passenger_id], event.passenger_id)
    else:
        order = ()
    return (event.t, 1, event.vehicle_id, event.visit, _TYPE_RANK[event.type], order)


def canonical_sort(events: Iterable[Event], arrival_s_by_passenger: Mapping[str, int]) -> list[Event]:
    """정렬 키 순서로 정렬하고 seq를 0부터 매긴다."""
    out = sorted(events, key=lambda e: canonical_key(e, arrival_s_by_passenger))
    for i, e in enumerate(out):
        e.seq = i
    return out


def dumps_events(events: Sequence[Event]) -> bytes:
    return io.jsonl_bytes(e.to_dict() for e in events)


def write_events(events: Sequence[Event], path: Path) -> str:
    return io.write_bytes(path, dumps_events(events))
