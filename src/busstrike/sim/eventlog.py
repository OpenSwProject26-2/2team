"""M07 사건 로그·승객 결과 직렬화(m07-output-contract.md §2.4·§2.5·§5).

로그 순서의 정의는 키 `(time_s, phase, stable_id)`이고 엔진이 그 순서로 만든다. 여기서는 바이트만 정한다:
키 순서 `EVENT_KEYS`, `separators=(",", ":")`, `ensure_ascii=False`, 줄 끝 `\\n`.
"""

from __future__ import annotations

import csv
import io as _io
from pathlib import Path
from typing import Iterable, Sequence

from . import io
from .models import Event

OUTCOME_COLUMNS = ("passenger_id", "origin", "destination", "time_s", "outcome", "reason", "physical",
                   "vehicle_id", "boarding_s", "arrival_s", "refused")


def event_key(event: Event) -> tuple[int, int, str]:
    return (event.time_s, event.phase, event.stable_id)


def dumps_events(events: Sequence[Event]) -> bytes:
    return io.jsonl_bytes(e.to_dict() for e in events)


def write_events(events: Sequence[Event], path: Path) -> str:
    return io.write_bytes(path, dumps_events(events))


def outcomes_bytes(rows: Iterable) -> bytes:
    """`passenger_outcomes.csv`. 빈 값은 빈 칸, 행 순서는 호출자(passenger_id 순)."""
    buf = _io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(OUTCOME_COLUMNS)
    for r in rows:
        w.writerow(["" if getattr(r, c) is None else getattr(r, c) for c in OUTCOME_COLUMNS])
    return buf.getvalue().encode("utf-8")
