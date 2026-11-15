"""독립 검증기 불변식 V01–V15(simulator.md §8.2).

입력 파일과 이벤트 로그만으로 다시 계산한다. `busstrike.sim`을 import하지 않는다.
각 검사는 위반을 `Sink`에 쌓기만 하고, 상태(pass/fail/skipped)는 `validator.py`가 정한다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from ._inputs import Passenger, RunFiles, Trip, is_int, sha256_hex, canonical_json

CHECKS: tuple[tuple[str, str], ...] = (
    ("V01", "로그 형식"),
    ("V02", "인원 보존"),
    ("V03", "정원"),
    ("V04", "차량 일정"),
    ("V05", "시간 단조"),
    ("V06", "승차 전 도착"),
    ("V07", "하차는 승차 뒤"),
    ("V08", "구간 시간"),
    ("V09", "정차·회차"),
    ("V10", "운영시간"),
    ("V11", "탑승 규칙"),
    ("V12", "미수송 처리"),
    ("V13", "지표 재계산"),
    ("V14", "시간표"),
    ("V15", "식별·해시"),
)

# §6.1 이벤트 로그 키 순서와 type별 키 집합(값이 없는 키는 생략하므로 정확히 이 키들만 있어야 한다)
EVENT_KEYS = ("seq", "t", "type", "vehicle_id", "trip_id", "visit", "stop_id", "stop_seq",
              "passenger_id", "alighted", "boarded", "left_behind", "load", "cause")
INT_KEYS = frozenset({"seq", "t", "visit", "stop_seq", "alighted", "boarded", "left_behind", "load"})
_BASE = ("seq", "t", "type")
_VEHICLE = ("vehicle_id", "trip_id", "visit")
_STOP = ("stop_id", "stop_seq")
TYPE_KEYS: dict[str, frozenset[str]] = {
    "arrive": frozenset(_BASE + _VEHICLE + _STOP + ("alighted", "boarded", "left_behind", "load")),
    "depart": frozenset(_BASE + _VEHICLE + _STOP + ("load",)),
    "alight": frozenset(_BASE + _VEHICLE + _STOP + ("passenger_id",)),
    "board": frozenset(_BASE + _VEHICLE + _STOP + ("passenger_id",)),
    "denied": frozenset(_BASE + _VEHICLE + _STOP + ("passenger_id",)),
    "cancel": frozenset(_BASE + _VEHICLE + ("cause",)),
    "no_service": frozenset(_BASE + ("passenger_id", "cause")),
    "abandon": frozenset(_BASE + ("passenger_id", "cause")),
    "left_waiting": frozenset(_BASE + ("passenger_id", "cause")),
}
VEHICLE_TYPES = frozenset({"arrive", "depart", "alight", "board", "denied", "cancel"})
OUTCOME_TYPES = frozenset({"no_service", "abandon", "left_waiting"})
FINAL_TYPES = frozenset({"alight"}) | OUTCOME_TYPES
_PHASE = {"no_service": 0, "abandon": 2, "left_waiting": 3}
_TYPE_RANK = {"arrive": 0, "alight": 1, "board": 2, "denied": 3, "depart": 4, "cancel": 0}
CAUSES = ("FULL", "JOURNEY_EXCEEDED", "NO_ROUTE_AT_STOP", "NO_ROUTE_TO_DEST", "SERVICE_ENDED", "WAIT_EXCEEDED")
_LOCATION_KEYS = ("seq", "t", "vehicle_id", "trip_id", "stop_id", "passenger_id")


class Sink:
    """검사별 위반 목록."""

    def __init__(self) -> None:
        self.items: dict[str, list[dict]] = {cid: [] for cid, _ in CHECKS}

    def add(self, cid: str, message: str, event: dict | None = None, **fields: Any) -> None:
        v: dict[str, Any] = {"check_id": cid, "message": message}
        if event is not None:
            for k in _LOCATION_KEYS:
                if k in event:
                    v[k] = event[k]
        for k, val in fields.items():
            if val is not None:
                v[k] = val
        self.items[cid].append(v)


# ---------------------------------------------------------------- V01 로그 형식

def sort_key(e: dict, passengers: dict[str, Passenger]) -> tuple:
    """§6.1 정렬 키. 로그에 없는 승객은 도착 시각을 -1로 둔다(V02가 따로 보고)."""
    typ = e["type"]
    phase = _PHASE.get(typ, 1)
    if phase != 1:
        return (e["t"], phase, "", 0, 0, (e["passenger_id"],))
    if typ == "alight":
        order: tuple = (e["passenger_id"],)
    elif typ in ("board", "denied"):
        p = passengers.get(e["passenger_id"])
        order = (p.arrival_s if p else -1, e["passenger_id"])
    else:
        order = ()
    return (e["t"], 1, e["vehicle_id"], e["visit"], _TYPE_RANK[typ], order)


def parse_log(f: RunFiles, sink: Sink) -> list[dict]:
    """줄 단위로 읽어 형식을 검사한다. 돌려준 목록은 V01이 통과할 때만 신뢰한다."""
    lines = f.event_lines
    if lines and lines[-1] != b"":
        sink.add("V01", "event log must end with a newline", seq=len(lines) - 1)
    body = lines[:-1] if lines and lines[-1] == b"" else lines
    events: list[dict] = []
    for n, line in enumerate(body):
        try:
            text = line.decode("utf-8")
            e = json.loads(text)
        except (UnicodeDecodeError, ValueError):
            sink.add("V01", "line is not UTF-8 JSON", seq=n)
            continue
        if not isinstance(e, dict):
            sink.add("V01", "line is not a JSON object", seq=n)
            continue
        typ = e.get("type")
        if typ not in TYPE_KEYS:
            sink.add("V01", "unknown event type", seq=n, actual=typ if isinstance(typ, str) else None)
            continue
        keys = set(e)
        if keys != TYPE_KEYS[typ]:
            sink.add("V01", f"keys of {typ} event differ from §6.1",
                     seq=n, expected=sorted(TYPE_KEYS[typ]), actual=sorted(keys))
            continue
        bad = [k for k in e if (k in INT_KEYS and not is_int(e[k])) or
               (k not in INT_KEYS and (not isinstance(e[k], str) or not e[k]))]
        if bad:
            sink.add("V01", "field has wrong type", seq=n, actual=sorted(bad))
            continue
        if e["seq"] != n:
            sink.add("V01", "seq must equal the 0-based line number", seq=n, expected=n, actual=e["seq"])
        if [k for k in e] != [k for k in EVENT_KEYS if k in e]:
            sink.add("V01", "key order differs from §6.1", seq=n)
        elif json.dumps(e, ensure_ascii=False, separators=(",", ":")) != text:
            sink.add("V01", "line is not in canonical JSON form", seq=n)
        events.append(e)
    if sink.items["V01"]:
        return events
    prev = None
    for e in events:
        k = sort_key(e, f.passengers)
        if prev is not None and k < prev:
            sink.add("V01", "sort key decreases (§6.1)", e)
        prev = k
    return events


# ---------------------------------------------------------------- 로그 색인

@dataclass
class Visit:
    vehicle_id: str
    visit: int
    arrive: dict
    alights: list[dict] = field(default_factory=list)
    boards: list[dict] = field(default_factory=list)
    denied: list[dict] = field(default_factory=list)
    departs: list[dict] = field(default_factory=list)

    @property
    def depart(self) -> dict | None:
        return self.departs[0] if self.departs else None


@dataclass
class Ctx:
    f: RunFiles
    events: list[dict]
    visits: dict[str, dict[int, Visit]] = field(default_factory=dict)      # vehicle → visit → Visit
    arrive_order: list[Visit] = field(default_factory=list)                 # 로그 순서
    duplicate_arrives: list[dict] = field(default_factory=list)
    orphans: list[dict] = field(default_factory=list)                       # arrive 없는 하위 이벤트
    cancels: dict[str, list[dict]] = field(default_factory=dict)
    by_pid: dict[str, dict[str, list[dict]]] = field(default_factory=dict)  # pid → type → events
    duty_trips: dict[str, list[Trip]] = field(default_factory=dict)
    flat: dict[str, list[tuple[Trip, int]]] = field(default_factory=dict)   # vehicle → [(trip, stop_seq)]
    patterns: tuple[tuple[str, ...], ...] = ()
    static_cause: dict[str, str | None] = field(default_factory=dict)
    left_count: dict[str, int] = field(default_factory=dict)                # V11 재구성 결과(V12가 사용)

    def trip(self, trip_id: Any) -> Trip | None:
        return self.f.timetable.trips_by_id.get(trip_id) if isinstance(trip_id, str) else None

    def pax_events(self, pid: str, typ: str) -> list[dict]:
        return self.by_pid.get(pid, {}).get(typ, [])

    def vehicle_visits(self, vid: str) -> list[Visit]:
        return [self.visits[vid][k] for k in sorted(self.visits.get(vid, {}))]


def alight_index(trip: Trip, k: int, dest: tuple[str, ...]) -> int | None:
    """§4.1: k 뒤에서 처음 나오는 허용 목적지 순번."""
    ds = set(dest)
    for j in range(k + 1, len(trip.stop_ids)):
        if trip.stop_ids[j] in ds:
            return j
    return None


def compute_static_cause(p: Passenger, patterns: tuple[tuple[str, ...], ...]) -> str | None:
    """§4.5: 시간표 영업 trip의 정류장 순서열만으로 판정."""
    origin, dest = set(p.origin_stop_ids), set(p.dest_stop_ids)
    positions = [(pat, k) for pat in patterns for k, s in enumerate(pat[:-1]) if s in origin]
    if not positions:
        return "NO_ROUTE_AT_STOP"
    if any(any(s in dest for s in pat[k + 1:]) for pat, k in positions):
        return None
    return "NO_ROUTE_TO_DEST"


def build_ctx(f: RunFiles, events: list[dict]) -> Ctx:
    c = Ctx(f, events)
    for e in events:
        typ = e["type"]
        if typ == "arrive":
            per = c.visits.setdefault(e["vehicle_id"], {})
            if e["visit"] in per:
                c.duplicate_arrives.append(e)
            else:
                v = Visit(e["vehicle_id"], e["visit"], e)
                per[e["visit"]] = v
                c.arrive_order.append(v)
        elif typ == "cancel":
            c.cancels.setdefault(e["vehicle_id"], []).append(e)
        if "passenger_id" in e:
            c.by_pid.setdefault(e["passenger_id"], {}).setdefault(typ, []).append(e)
    for e in events:
        typ = e["type"]
        if typ in ("alight", "board", "denied", "depart"):
            v = c.visits.get(e["vehicle_id"], {}).get(e["visit"])
            if v is None:
                c.orphans.append(e)
                continue
            {"alight": v.alights, "board": v.boards, "denied": v.denied, "depart": v.departs}[typ].append(e)
    tt = f.timetable
    for d in tt.duties:
        trips = [tt.trips_by_id[x] for x in d.trip_ids if x in tt.trips_by_id]
        c.duty_trips.setdefault(d.vehicle_id, trips)
        c.flat.setdefault(d.vehicle_id, [(t, k) for t in trips for k in range(len(t.stop_ids))])
    pats: dict[tuple[str, ...], None] = {}
    for t in tt.trips:
        if t.in_service:
            pats.setdefault(t.stop_ids, None)
    c.patterns = tuple(pats)
    c.static_cause = {pid: compute_static_cause(p, c.patterns) for pid, p in f.passengers.items()}
    return c


def _cycle_span(trips: list[Trip], i: int) -> tuple[int, int]:
    """duty의 i번째 trip(새 cycle 첫 trip)부터 그 cycle 끝까지: (예정 소요, 영업 trip 수)."""
    j = i
    while j + 1 < len(trips) and trips[j + 1].cycle_index == trips[i].cycle_index and trips[j + 1].leg_index != 0:
        j += 1
    return trips[j].sched_s[-1] - trips[i].sched_s[0], sum(1 for t in trips[i:j + 1] if t.in_service)


# ---------------------------------------------------------------- V02 인원 보존

def check_v02(c: Ctx, sink: Sink) -> None:
    pax = c.f.passengers
    for e in c.events:
        if "passenger_id" in e and e["passenger_id"] not in pax:
            sink.add("V02", "unknown passenger_id", e)
    counts = {"alight": 0, "abandon": 0, "no_service": 0, "left_waiting": 0}
    for pid in sorted(pax):
        finals = [e for typ in sorted(FINAL_TYPES) for e in c.pax_events(pid, typ)]
        if not finals:
            sink.add("V02", "passenger has no final outcome", passenger_id=pid)
        elif len(finals) > 1:
            for e in sorted(finals, key=lambda x: x["seq"])[1:]:
                sink.add("V02", "passenger has more than one final outcome", e)
        else:
            counts[finals[0]["type"]] += 1
        nb, na = len(c.pax_events(pid, "board")), len(c.pax_events(pid, "alight"))
        if nb != na:
            sink.add("V02", "board and alight counts differ for passenger", passenger_id=pid,
                     expected=nb, actual=na)
    boards = sum(1 for e in c.events if e["type"] == "board")
    alights = sum(1 for e in c.events if e["type"] == "alight")
    if boards != alights:
        sink.add("V02", "total board count differs from alight count", expected=boards, actual=alights)
    accounted = counts["alight"] + counts["abandon"] + counts["no_service"] + counts["left_waiting"]
    if accounted != len(pax):
        sink.add("V02", "generated != transported + abandoned + no_service + left_waiting",
                 expected=len(pax), actual=accounted)


# ---------------------------------------------------------------- V03 정원

def check_v03(c: Ctx, sink: Sink) -> None:
    for vid in sorted(c.visits):
        veh = c.f.scenario.vehicles.get(vid)
        load = 0
        onboard: set[str] = set()
        for v in c.vehicle_visits(vid):
            a = v.arrive
            for e in v.alights:
                if e["passenger_id"] not in onboard:
                    sink.add("V03", "alighting passenger is not on board", e)
                onboard.discard(e["passenger_id"])
            for e in v.boards:
                onboard.add(e["passenger_id"])
            na, nb = len(v.alights), len(v.boards)
            load = load - na + nb
            if a["alighted"] != na:
                sink.add("V03", "arrive.alighted differs from alight events", a, expected=na, actual=a["alighted"])
            if a["boarded"] != nb:
                sink.add("V03", "arrive.boarded differs from board events", a, expected=nb, actual=a["boarded"])
            if a["load"] != load:
                sink.add("V03", "arrive.load differs from reconstructed load", a, expected=load, actual=a["load"])
            for d in v.departs:
                if d["load"] != load:
                    sink.add("V03", "depart.load differs from reconstructed load", d, expected=load, actual=d["load"])
            if load < 0:
                sink.add("V03", "negative load", a, actual=load)
            if veh is not None and load > veh.capacity:
                sink.add("V03", "capacity exceeded", a, expected=veh.capacity, actual=load)
            trip = c.trip(a["trip_id"])
            if trip is not None and a["stop_seq"] == trip.last_seq and load != 0:
                sink.add("V03", "passengers still on board at trip end", a, expected=0, actual=load)
                load = 0
                onboard.clear()


# ---------------------------------------------------------------- V04 차량 일정

def check_v04(c: Ctx, sink: Sink) -> None:
    fleet = c.f.scenario.vehicles
    for e in c.events:
        if e["type"] in VEHICLE_TYPES and e["vehicle_id"] not in fleet:
            sink.add("V04", "vehicle not in fleet", e)
    for e in c.duplicate_arrives:
        sink.add("V04", "duplicate arrive for (vehicle_id, visit)", e)
    for e in c.orphans:
        sink.add("V04", f"{e['type']} has no arrive with the same (vehicle_id, visit)", e)
    for v in c.arrive_order:
        a = v.arrive
        for e in v.departs + v.denied:
            for k in ("trip_id", "stop_id", "stop_seq"):
                if e[k] != a[k]:
                    sink.add("V04", f"{e['type']}.{k} differs from its arrive", e, expected=a[k], actual=e[k])
        if len(v.departs) > 1:
            for d in v.departs[1:]:
                sink.add("V04", "more than one depart for a visit", d)
        trip = c.trip(a["trip_id"])
        if trip is not None:
            if a["stop_seq"] < trip.last_seq and not v.departs:
                sink.add("V04", "missing depart (not the last stop of trip)", a)
            if a["stop_seq"] >= trip.last_seq and v.departs:
                sink.add("V04", "depart at the last stop of trip", v.departs[0])

    for vid in sorted(set(c.visits) | set(c.flat) | set(c.cancels)):
        visits = c.vehicle_visits(vid)
        flat = c.flat.get(vid)
        if flat is None:
            if visits:
                sink.add("V04", "vehicle has events but no duty", visits[0].arrive)
            continue
        nums = [v.visit for v in visits]
        if nums != list(range(len(nums))):
            sink.add("V04", "visit numbers are not 0..n-1", vehicle_id=vid, actual=nums[:20])
        for i, v in enumerate(visits):
            a = v.arrive
            if i >= len(flat):
                sink.add("V04", "visit beyond the duty", a)
                continue
            trip, k = flat[i]
            exp = [trip.trip_id, k, trip.stop_ids[k]]
            act = [a["trip_id"], a["stop_seq"], a["stop_id"]]
            if exp != act:
                sink.add("V04", "visit differs from duty order", a, expected=exp, actual=act)
        n = len(visits)
        cancels = c.cancels.get(vid, [])
        for extra in cancels[1:]:
            sink.add("V04", "more than one cancel for a vehicle", extra)
        if cancels:
            ce = cancels[0]
            if ce["visit"] != n:
                sink.add("V04", "cancel.visit must be the next visit number", ce, expected=n, actual=ce["visit"])
            if n >= len(flat):
                sink.add("V04", "cancel after the duty finished", ce)
            else:
                trip, k = flat[n]
                if k != 0 or trip.leg_index != 0 or ce["trip_id"] != trip.trip_id:
                    sink.add("V04", "cancel must name the first trip of the next cycle", ce,
                             expected=trip.trip_id if k == 0 and trip.leg_index == 0 else None,
                             actual=ce["trip_id"])
        elif n < len(flat):
            trip, k = flat[n]
            sink.add("V04", "duty stopped before its end without cancel", vehicle_id=vid, trip_id=trip.trip_id,
                     expected=len(flat), actual=n)
        # 한 차량이 두 trip에 동시에 있지 않음: trip 단위 구간이 겹치지 않는다
        prev_trip, prev_end = None, None
        for v in visits:
            a = v.arrive
            if a["trip_id"] != prev_trip:
                if prev_end is not None and a["t"] < prev_end:
                    sink.add("V04", "trip starts before the previous trip of the vehicle ended", a,
                             expected=prev_end, actual=a["t"])
                prev_trip = a["trip_id"]
            prev_end = a["t"] if prev_end is None else max(prev_end, a["t"])


# ---------------------------------------------------------------- V05 시간 단조

def check_v05(c: Ctx, sink: Sink) -> None:
    for vid in sorted(c.visits):
        prev_t = None
        for v in c.vehicle_visits(vid):
            a = v.arrive
            if prev_t is not None and a["t"] < prev_t:
                sink.add("V05", "arrive earlier than the previous visit", a, expected=prev_t, actual=a["t"])
            prev_t = a["t"]
            d = v.depart
            if d is not None:
                if d["t"] < a["t"]:
                    sink.add("V05", "depart earlier than its arrive", d, expected=a["t"], actual=d["t"])
                prev_t = max(prev_t, d["t"])
        for ce in c.cancels.get(vid, []):
            if prev_t is not None and ce["t"] < prev_t:
                sink.add("V05", "cancel earlier than the last visit", ce, expected=prev_t, actual=ce["t"])
    for pid in sorted(c.by_pid):
        denied = c.pax_events(pid, "denied")
        boards = c.pax_events(pid, "board")
        alights = c.pax_events(pid, "alight")
        outcomes = [e for typ in sorted(OUTCOME_TYPES) for e in c.pax_events(pid, typ)]
        first_denied = min((e["t"] for e in denied), default=None)
        for b in boards:
            if first_denied is not None and b["t"] < first_denied:
                sink.add("V05", "board earlier than the passenger's denied", b, expected=first_denied, actual=b["t"])
            for al in alights:
                if al["t"] < b["t"]:
                    sink.add("V05", "alight earlier than board", al, expected=b["t"], actual=al["t"])
        for o in outcomes:
            if first_denied is not None and o["t"] < first_denied:
                sink.add("V05", f"{o['type']} earlier than the passenger's denied", o,
                         expected=first_denied, actual=o["t"])


# ---------------------------------------------------------------- V06 승차 전 도착

def check_v06(c: Ctx, sink: Sink) -> None:
    pax = c.f.passengers
    for v in c.arrive_order:
        a = v.arrive
        trip = c.trip(a["trip_id"])
        for b in v.boards:
            for k in ("t", "stop_id", "stop_seq", "trip_id"):
                if b[k] != a[k]:
                    sink.add("V06", f"board.{k} differs from its arrive", b, expected=a[k], actual=b[k])
            if trip is not None and (not trip.in_service or a["stop_seq"] >= trip.last_seq):
                sink.add("V06", "board on a deadhead trip or at the last stop", b)
            p = pax.get(b["passenger_id"])
            if p is None:
                continue
            if b["t"] < p.arrival_s:
                sink.add("V06", "board before the passenger arrived", b, expected=p.arrival_s, actual=b["t"])
            if b["t"] > p.deadline_s:
                sink.add("V06", "board after arrival_s + max_wait_s", b, expected=p.deadline_s, actual=b["t"])
            if b["stop_id"] not in p.origin_stop_ids:
                sink.add("V06", "board stop is not in origin_stop_ids", b, expected=list(p.origin_stop_ids))
    for pid in sorted(c.by_pid):
        boards = c.pax_events(pid, "board")
        for b in boards[1:]:
            sink.add("V06", "passenger boards more than once", b)


# ---------------------------------------------------------------- V07 하차는 승차 뒤

def check_v07(c: Ctx, sink: Sink) -> None:
    pax = c.f.passengers
    for v in c.arrive_order:
        a = v.arrive
        for al in v.alights:
            for k in ("t", "stop_id", "stop_seq", "trip_id"):
                if al[k] != a[k]:
                    sink.add("V07", f"alight.{k} differs from its arrive", al, expected=a[k], actual=al[k])
    for pid in sorted(c.by_pid):
        alights = c.pax_events(pid, "alight")
        if not alights:
            continue
        boards = c.pax_events(pid, "board")
        p = pax.get(pid)
        for al in alights:
            if not boards:
                sink.add("V07", "alight without board", al)
                continue
            b = boards[0]
            if al["vehicle_id"] != b["vehicle_id"] or al["trip_id"] != b["trip_id"]:
                sink.add("V07", "alight on a different vehicle or trip than board", al,
                         expected=[b["vehicle_id"], b["trip_id"]], actual=[al["vehicle_id"], al["trip_id"]])
                continue
            if al["stop_seq"] <= b["stop_seq"]:
                sink.add("V07", "alight stop_seq is not after board stop_seq", al,
                         expected=f"> {b['stop_seq']}", actual=al["stop_seq"])
            if p is None:
                continue
            if al["stop_id"] not in p.dest_stop_ids:
                sink.add("V07", "alight stop is not in dest_stop_ids", al, expected=list(p.dest_stop_ids))
            trip = c.trip(b["trip_id"])
            if trip is not None and 0 <= b["stop_seq"] < len(trip.stop_ids):
                j = alight_index(trip, b["stop_seq"], p.dest_stop_ids)
                if j != al["stop_seq"]:
                    sink.add("V07", "alight is not at the earliest allowed stop after board", al,
                             expected=j, actual=al["stop_seq"])


# ---------------------------------------------------------------- V08 구간 시간

def check_v08(c: Ctx, sink: Sink) -> None:
    tt = c.f.travel_times
    for vid in sorted(c.visits):
        visits = c.vehicle_visits(vid)
        for cur, nxt in zip(visits, visits[1:]):
            a, b = cur.arrive, nxt.arrive
            if a["trip_id"] != b["trip_id"] or b["stop_seq"] != a["stop_seq"] + 1:
                continue
            d = cur.depart
            if d is None:
                continue
            travel = tt.lookup(a["stop_id"], b["stop_id"], d["t"])
            if travel is None:
                sink.add("V08", "segment or time bin missing in travel_times", b,
                         actual=[a["stop_id"], b["stop_id"]])
            elif b["t"] - d["t"] != travel:
                sink.add("V08", "arrive - depart differs from travel_times", b,
                         expected=d["t"] + travel, actual=b["t"])


# ---------------------------------------------------------------- V09 정차·회차

def check_v09(c: Ctx, sink: Sink) -> None:
    pol = c.f.scenario.policy
    w_end = c.f.scenario.end_s
    per_pax = pol.dwell_per_boarding_s + pol.dwell_per_alighting_s
    for vid in sorted(set(c.visits) | set(c.cancels)):
        visits = c.vehicle_visits(vid)
        trips = c.duty_trips.get(vid)
        veh = c.f.scenario.vehicles.get(vid)
        cancels = c.cancels.get(vid, [])
        expected_cancel: dict | None = None
        if visits and trips and visits[0].arrive["t"] != trips[0].sched_s[0]:
            sink.add("V09", "first visit is not at the first scheduled time", visits[0].arrive,
                     expected=trips[0].sched_s[0], actual=visits[0].arrive["t"])
        trip_pos = {t.trip_id: i for i, t in enumerate(trips or [])}
        for i, v in enumerate(visits):
            a = v.arrive
            trip = c.trip(a["trip_id"])
            if trip is None:
                continue
            na, nb = len(v.alights), len(v.boards)
            k = a["stop_seq"]
            d = v.depart
            if d is not None and k < trip.last_seq:
                if not trip.in_service:
                    dwell = 0
                else:
                    dwell = pol.dwell_base_s + pol.dwell_per_boarding_s * nb + pol.dwell_per_alighting_s * na
                if d["t"] - a["t"] != dwell:
                    sink.add("V09", "dwell differs from §3.3", d, expected=a["t"] + dwell, actual=d["t"])
            if k != trip.last_seq or veh is None or trips is None or a["trip_id"] not in trip_pos:
                continue
            ti = trip_pos[a["trip_id"]]
            if ti + 1 >= len(trips):
                continue
            nxt = trips[ti + 1]
            free = a["t"] + (pol.dwell_per_alighting_s * na if trip.in_service else 0)
            ready = free + (pol.turnaround_s if trip.in_service else 0)
            late = False
            if nxt.leg_index == 0:
                ready += pol.cycle_rest_s
                s = max(nxt.sched_s[0], ready)
                dur, n_service = _cycle_span(trips, ti + 1)
                allow = per_pax * veh.capacity * n_service
                late = s + dur + allow > min(w_end, veh.available_end_s)
            else:
                s = max(nxt.sched_s[0], ready)
            following = visits[i + 1] if i + 1 < len(visits) else None
            if late:
                expected_cancel = {"t": ready, "trip_id": nxt.trip_id, "visit": a["visit"] + 1}
                if following is not None:
                    sink.add("V09", "next cycle should have been cancelled (§3.4)", following.arrive,
                             expected="cancel", actual="arrive")
                break
            if following is None:
                if not cancels:
                    continue   # 끊긴 duty는 V04가 보고한다
                sink.add("V09", "cancel although the §3.4 condition does not hold", cancels[0])
                continue
            if following.arrive["t"] != s:
                sink.add("V09", "trip start differs from max(sched_s, ready)", following.arrive,
                         expected=s, actual=following.arrive["t"])
        if expected_cancel is not None:
            if not cancels:
                sink.add("V09", "missing cancel although the §3.4 condition holds", vehicle_id=vid,
                         trip_id=expected_cancel["trip_id"], t=expected_cancel["t"])
            else:
                ce = cancels[0]
                act = {"t": ce["t"], "trip_id": ce["trip_id"], "visit": ce["visit"]}
                if act != expected_cancel:
                    sink.add("V09", "cancel fields differ from §3.4", ce, expected=expected_cancel, actual=act)
                if ce["cause"] != "LATE":
                    sink.add("V09", 'cancel.cause must be "LATE"', ce, actual=ce["cause"])
        elif cancels and visits and visits[-1].arrive["visit"] + 1 == cancels[0]["visit"]:
            last = visits[-1].arrive
            trip = c.trip(last["trip_id"])
            if trip is None or last["stop_seq"] != trip.last_seq or trips is None:
                sink.add("V09", "cancel does not follow the end of a trip", cancels[0])
            elif last["trip_id"] in trip_pos and trip_pos[last["trip_id"]] + 1 >= len(trips):
                sink.add("V09", "cancel after the last trip of the duty", cancels[0])
        elif cancels and not visits:
            sink.add("V09", "cancel before the first visit", cancels[0])


# ---------------------------------------------------------------- V10 운영시간

def check_v10(c: Ctx, sink: Sink) -> None:
    """차량 이동(arrive·depart와 그 승하차)만 본다. cancel은 이동이 아니라 판단 기록이고 t=ready라
    영업 leg 회차 뒤에는 W_e를 넘을 수 있다(§3.4)."""
    sc = c.f.scenario
    for e in c.events:
        if e["type"] not in VEHICLE_TYPES or e["type"] == "cancel":
            continue
        veh = sc.vehicles.get(e["vehicle_id"])
        if veh is None:
            continue
        lo, hi = max(sc.start_s, veh.available_start_s), min(sc.end_s, veh.available_end_s)
        if not lo <= e["t"] <= hi:
            sink.add("V10", "vehicle event outside [max(W_s, available_start_s), min(W_e, available_end_s)]",
                     e, expected=[lo, hi], actual=e["t"])


# ---------------------------------------------------------------- V11 탑승 규칙

def check_v11(c: Ctx, sink: Sink) -> None:
    """§8.2 재구성 의사코드: 정차마다 그 순간의 대기 집합을 다시 만들어 승차·남겨짐을 비교한다."""
    pax = c.f.passengers
    order = sorted((p for p in pax.values() if c.static_cause.get(p.passenger_id) is None),
                   key=lambda p: (p.arrival_s, p.passenger_id))
    queues: dict[str, list[Passenger]] = {}
    boarded: set[str] = set()
    load: dict[str, int] = {}
    left = c.left_count
    i = 0
    for v in c.arrive_order:
        a = v.arrive
        t = a["t"]
        while i < len(order) and order[i].arrival_s <= t:
            p = order[i]
            for s in p.origin_stop_ids:
                queues.setdefault(s, []).append(p)
            i += 1
        trip = c.trip(a["trip_id"])
        veh = c.f.scenario.vehicles.get(a["vehicle_id"])
        after_alight = load.get(a["vehicle_id"], 0) - len(v.alights)
        actual_board = [b["passenger_id"] for b in v.boards]
        actual_denied = [d["passenger_id"] for d in v.denied]
        k = a["stop_seq"]
        if trip is not None and veh is not None and trip.in_service and 0 <= k < trip.last_seq:
            q = [p for p in queues.get(a["stop_id"], []) if p.passenger_id not in boarded and t <= p.deadline_s]
            queues[a["stop_id"]] = q
            eligible = [p.passenger_id for p in q if alight_index(trip, k, p.dest_stop_ids) is not None]
            room = max(0, veh.capacity - after_alight)
            exp_board, exp_left = eligible[:room], eligible[room:]
            if actual_board != exp_board:
                sink.add("V11", "boarders differ from the FIFO reconstruction", a,
                         expected=exp_board, actual=actual_board)
            if a["left_behind"] != len(exp_left):
                sink.add("V11", "left_behind differs from the reconstruction", a,
                         expected=len(exp_left), actual=a["left_behind"])
            exp_denied = [pid for pid in exp_left if left.get(pid, 0) == 0]
            if actual_denied != exp_denied:
                sink.add("V11", "denied events differ from first-time left-behind passengers", a,
                         expected=exp_denied, actual=actual_denied)
            for pid in exp_left:
                left[pid] = left.get(pid, 0) + 1
        else:
            if a["left_behind"] != 0:
                sink.add("V11", "left_behind must be 0 on deadhead trips and last stops", a,
                         expected=0, actual=a["left_behind"])
            if actual_denied:
                sink.add("V11", "denied on a deadhead trip or at the last stop", a, actual=actual_denied)
        boarded.update(actual_board)
        load[a["vehicle_id"]] = after_alight + len(actual_board)
        if trip is not None and k == trip.last_seq:
            load[a["vehicle_id"]] = 0


# ---------------------------------------------------------------- V12 미수송 처리

def check_v12(c: Ctx, sink: Sink) -> None:
    w_end = c.f.scenario.end_s
    for pid in sorted(c.f.passengers):
        p = c.f.passengers[pid]
        static = c.static_cause.get(pid)
        full = c.left_count.get(pid, 0) > 0
        for typ in sorted(OUTCOME_TYPES):
            for e in c.pax_events(pid, typ):
                if typ == "no_service":
                    if e["t"] != p.arrival_s:
                        sink.add("V12", "no_service.t must equal arrival_s", e, expected=p.arrival_s, actual=e["t"])
                    if e["cause"] != static:
                        sink.add("V12", "no_service cause differs from the static rule (§4.5)", e,
                                 expected=static, actual=e["cause"])
                    continue
                if static is not None:
                    sink.add("V12", f"{typ} for a passenger with no route ({static})", e, expected="no_service")
                if typ == "abandon":
                    if e["t"] != p.deadline_s:
                        sink.add("V12", "abandon.t must equal arrival_s + max_wait_s", e,
                                 expected=p.deadline_s, actual=e["t"])
                    if p.deadline_s > w_end:
                        sink.add("V12", "abandon after W_e (should be left_waiting)", e, expected="left_waiting")
                    exp = "FULL" if full else "WAIT_EXCEEDED"
                else:
                    if e["t"] != w_end:
                        sink.add("V12", "left_waiting.t must equal W_e", e, expected=w_end, actual=e["t"])
                    if not w_end < p.deadline_s:
                        sink.add("V12", "left_waiting although the wait limit ended by W_e", e, expected="abandon")
                    exp = "FULL" if full else "SERVICE_ENDED"
                if e["cause"] != exp:
                    sink.add("V12", f"{typ} cause differs from §4.5", e, expected=exp, actual=e["cause"])
        if static is not None and (c.pax_events(pid, "board") or c.pax_events(pid, "denied")):
            sink.add("V12", f"passenger with no route ({static}) appears at a vehicle", passenger_id=pid)


# ---------------------------------------------------------------- V13 지표 재계산

def _ratio(num: int, den: int, digits: int = 6) -> float | None:
    return None if den == 0 else round(num / den, digits)


def _mean(values: list[int]) -> float | None:
    return None if not values else round(sum(values) / len(values), 3)


def _rank(sorted_values: list[int], p: int) -> int | None:
    n = len(sorted_values)
    if n == 0:
        return None
    r = (p * n + 99) // 100
    return sorted_values[max(1, r) - 1]


def _stats(values: list[int]) -> dict:
    v = sorted(values)
    return {"count": len(v), "mean": _mean(v), "p50": _rank(v, 50), "p90": _rank(v, 90), "p95": _rank(v, 95),
            "max": v[-1] if v else None}


def expected_metrics(c: Ctx) -> dict:
    """§6.3 정의와 '2단계 구현에서 정한 세부'로 로그 + 입력에서 지표를 다시 계산한다."""
    f = c.f
    tt = f.timetable
    pax = [f.passengers[pid] for pid in sorted(f.passengers)]
    board = {pid: evs["board"][0] for pid, evs in c.by_pid.items() if evs.get("board")}
    alight = {pid: evs["alight"][0]["t"] for pid, evs in c.by_pid.items() if evs.get("alight")}
    outcome = {}
    for pid, evs in c.by_pid.items():
        for typ in sorted(OUTCOME_TYPES):
            if evs.get(typ):
                outcome[pid] = (typ, evs[typ][0]["cause"])
    satisfied = {p.passenger_id for p in pax
                 if p.passenger_id in alight and alight[p.passenger_id] - p.arrival_s <= f.scenario.policy.max_journey_s}

    def block(group: list[Passenger]) -> dict:
        gen = len(group)
        tr = sum(1 for p in group if p.passenger_id in alight)
        sat = sum(1 for p in group if p.passenger_id in satisfied)
        kinds = [outcome.get(p.passenger_id, ("", ""))[0] for p in group]
        return {"generated": gen, "transported": tr, "satisfied": sat, "abandoned": kinds.count("abandon"),
                "no_service": kinds.count("no_service"), "left_waiting": kinds.count("left_waiting"),
                "transport_rate": _ratio(tr, gen), "satisfaction_rate": _ratio(sat, gen)}

    passengers = block(pax)
    passengers["by_kind"] = {k: block([p for p in pax if p.kind == k]) for k in ("access", "egress")}
    waits = [board[p.passenger_id]["t"] - p.arrival_s for p in pax if p.passenger_id in board]
    both = [p for p in pax if p.passenger_id in alight and p.passenger_id in board]
    in_vehicle = [alight[p.passenger_id] - board[p.passenger_id]["t"] for p in both]
    journey = [alight[p.passenger_id] - p.arrival_s for p in pax if p.passenger_id in alight]

    by_cause = {k: 0 for k in CAUSES}
    by_stop: dict[tuple[str, str], int] = {}
    for p in pax:
        pid = p.passenger_id
        if pid in alight:
            if pid in satisfied:
                continue
            cause = "JOURNEY_EXCEEDED"
        elif pid in outcome:
            cause = outcome[pid][1]
        else:
            continue
        by_cause[cause] = by_cause.get(cause, 0) + 1
        key = (p.origin_stop_ids[0], cause)
        by_stop[key] = by_stop.get(key, 0) + 1

    arrives_by_trip: dict[str, list[dict]] = {}
    times_by_vehicle: dict[str, list[int]] = {}
    boardings_by_vehicle: dict[str, int] = {}
    left_total = 0
    denied_pids: set[str] = set()
    for e in c.events:
        typ = e["type"]
        if typ == "arrive":
            arrives_by_trip.setdefault(e["trip_id"], []).append(e)
            times_by_vehicle.setdefault(e["vehicle_id"], []).append(e["t"])
            left_total += e["left_behind"]
        elif typ == "depart":
            times_by_vehicle.setdefault(e["vehicle_id"], []).append(e["t"])
        elif typ == "board":
            boardings_by_vehicle[e["vehicle_id"]] = boardings_by_vehicle.get(e["vehicle_id"], 0) + 1
        elif typ == "denied":
            denied_pids.add(e["passenger_id"])

    route_of = {vid: r.route_id for r in tt.routes for vid in r.vehicle_ids}
    acc: dict[str, dict[str, int]] = {}
    run_cycles: dict[str, set[int]] = {}
    planned: dict[str, set[int]] = {}
    trip_rows: list[dict] = []
    for t in tt.trips:
        planned.setdefault(t.vehicle_id, set()).add(t.cycle_index)
        arr = sorted(arrives_by_trip.get(t.trip_id, []), key=lambda e: e["stop_seq"])
        if not arr:
            continue
        run_cycles.setdefault(t.vehicle_id, set()).add(t.cycle_index)
        a = acc.setdefault(t.vehicle_id, {"revenue_m": 0, "deadhead_m": 0, "revenue_s": 0, "deadhead_s": 0})
        dist = 0
        for e in arr:
            k = e["stop_seq"]
            if 0 < k < len(t.stop_ids):
                dist += f.travel_times.distance(t.stop_ids[k - 1], t.stop_ids[k]) or 0
        secs = arr[-1]["t"] - arr[0]["t"]
        if t.in_service:
            a["revenue_m"] += dist
            a["revenue_s"] += secs
            veh = f.scenario.vehicles[t.vehicle_id]
            max_load = max(e["load"] for e in arr)
            trip_rows.append({
                "trip_id": t.trip_id, "route_id": t.route_id, "vehicle_id": t.vehicle_id,
                "boardings": sum(e["boarded"] for e in arr), "max_load": max_load,
                "load_factor_seat": _ratio(max_load, veh.seat_capacity),
                "load_factor_total": _ratio(max_load, veh.capacity),
                "start_delay_s": arr[0]["t"] - t.sched_s[0], "end_s": arr[-1]["t"],
            })
        else:
            a["deadhead_m"] += dist
            a["deadhead_s"] += secs

    vehicle_rows = []
    for vid in sorted(route_of):
        a = acc.get(vid, {"revenue_m": 0, "deadhead_m": 0, "revenue_s": 0, "deadhead_s": 0})
        times = times_by_vehicle.get(vid)
        first = min(times) if times else None
        last = max(times) if times else None
        n_run = len(run_cycles.get(vid, ()))
        vehicle_rows.append({
            "vehicle_id": vid, "route_id": route_of[vid], "revenue_m": a["revenue_m"], "deadhead_m": a["deadhead_m"],
            "first_s": first, "last_s": last, "span_s": None if not times else last - first,
            "revenue_s": a["revenue_s"], "deadhead_s": a["deadhead_s"], "boardings": boardings_by_vehicle.get(vid, 0),
            "cycles_run": n_run, "cycles_cancelled": len(planned.get(vid, ())) - n_run,
        })
    used = [r for r in vehicle_rows if r["first_s"] is not None]
    rev = sum(r["revenue_m"] for r in vehicle_rows)
    dead = sum(r["deadhead_m"] for r in vehicle_rows)
    fleet = {"vehicles_used": len(used), "total_m": rev + dead, "revenue_m": rev, "deadhead_m": dead,
             "total_span_s": sum(r["span_s"] for r in used),
             "boardings_per_vehicle": _ratio(sum(r["boardings"] for r in vehicle_rows), len(used), 3)}

    route_waits: dict[str, list[int]] = {}
    route_sat: dict[str, int] = {}
    for p in pax:
        b = board.get(p.passenger_id)
        trip = c.trip(b["trip_id"]) if b else None
        if trip is None:
            continue
        route_waits.setdefault(trip.route_id, []).append(b["t"] - p.arrival_s)
        if p.passenger_id in satisfied:
            route_sat[trip.route_id] = route_sat.get(trip.route_id, 0) + 1
    route_rows = []
    for r in tt.routes:
        rows = [x for x in trip_rows if x["route_id"] == r.route_id]
        route_rows.append({
            "route_id": r.route_id, "vehicles": len(r.vehicle_ids), "trips_run": len(rows),
            "boardings": sum(x["boardings"] for x in rows), "satisfied": route_sat.get(r.route_id, 0),
            "mean_wait_s": _mean(route_waits.get(r.route_id, [])),
            "max_load_factor_total": max((x["load_factor_total"] for x in rows), default=None),
        })

    return {
        "schema_version": 1, "plan_id": tt.plan_id, "plan_revision": tt.plan_revision,
        "scope_mode": "single_bus_leg", "passengers": passengers,
        "wait_s": _stats(waits), "in_vehicle_s": _stats(in_vehicle), "journey_s": _stats(journey),
        "denied": {"boardings": left_total, "passengers": len(denied_pids)},
        "unserved": {"by_cause": by_cause,
                     "by_stop": [{"stop_id": s, "cause": k, "count": n} for (s, k), n in sorted(by_stop.items())]},
        "vehicles": vehicle_rows, "fleet": fleet, "trips": trip_rows, "routes": route_rows,
    }


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    return type(a) is type(b) and a == b


def _diff(path: str, exp: Any, act: Any, out: list[tuple[str, Any, Any]]) -> None:
    if isinstance(exp, dict) and isinstance(act, dict):
        for k in sorted(set(exp) | set(act)):
            p = f"{path}.{k}" if path else k
            if k not in act:
                out.append((p, exp[k], "<missing>"))
            elif k not in exp:
                out.append((p, "<absent>", act[k]))
            else:
                _diff(p, exp[k], act[k], out)
    elif isinstance(exp, list) and isinstance(act, list):
        if len(exp) != len(act):
            out.append((f"{path}.length", len(exp), len(act)))
        for i, (x, y) in enumerate(zip(exp, act)):
            _diff(f"{path}[{i}]", x, y, out)
    elif not _same(exp, act):
        out.append((path, exp, act))


def check_v13(c: Ctx, sink: Sink) -> None:
    actual = c.f.metrics
    if not isinstance(actual, dict):
        sink.add("V13", "metrics.json must be an object")
        return
    diffs: list[tuple[str, Any, Any]] = []
    _diff("", expected_metrics(c), actual, diffs)
    for path, exp, act in diffs:
        sink.add("V13", f"metrics value differs: {path}", expected=exp, actual=act)


# ---------------------------------------------------------------- V14 시간표

def check_v14(c: Ctx, sink: Sink) -> None:
    f = c.f
    tt, plan, sc = f.timetable, f.plan, f.scenario
    if tt.plan_id != plan.plan_id or tt.plan_revision != plan.revision:
        sink.add("V14", "timetable plan_id/plan_revision differ from plan.json",
                 expected=[plan.plan_id, plan.revision], actual=[tt.plan_id, tt.plan_revision])
    if tt.origin not in ("generated", "provided"):
        sink.add("V14", "timetable.origin must be generated or provided", actual=tt.origin)
    route_ids = [r.route_id for r in tt.routes]
    if sorted(set(route_ids)) != sorted(plan.routes) or len(route_ids) != len(set(route_ids)):
        sink.add("V14", "timetable routes differ from plan routes", expected=sorted(plan.routes), actual=route_ids)
    owner: dict[str, str] = {}
    for r in tt.routes:
        for vid in r.vehicle_ids:
            if vid in owner:
                sink.add("V14", "vehicle listed on more than one route", vehicle_id=vid,
                         expected=owner[vid], actual=r.route_id)
            owner.setdefault(vid, r.route_id)
            if vid not in sc.vehicles:
                sink.add("V14", "route vehicle not in fleet", vehicle_id=vid)
        pr = plan.routes.get(r.route_id)
        if pr is None:
            continue
        n = len(r.vehicle_ids)
        if tt.origin == "generated" and n != pr.vehicle_count:
            sink.add("V14", "route vehicle count differs from plan vehicle_count",
                     expected=pr.vehicle_count, actual=n)
        if tt.origin == "provided" and n > pr.vehicle_count:
            sink.add("V14", "route uses more vehicles than plan vehicle_count", expected=pr.vehicle_count, actual=n)
        if pr.vehicle_ids is not None and not set(r.vehicle_ids) <= set(pr.vehicle_ids):
            sink.add("V14", "route uses vehicles outside plan vehicle_ids", expected=sorted(pr.vehicle_ids),
                     actual=list(r.vehicle_ids))
    if sum(len(r.vehicle_ids) for r in tt.routes) > len(sc.vehicles):
        sink.add("V14", "timetable uses more vehicles than available")

    seen: set[str] = set()
    by_vehicle: dict[str, list[Trip]] = {}
    prev_key = None
    for t in tt.trips:
        if t.trip_id in seen:
            sink.add("V14", "duplicate trip_id", trip_id=t.trip_id)
        seen.add(t.trip_id)
        key = (t.vehicle_id, t.cycle_index, t.leg_index)
        if prev_key is not None and key <= prev_key:
            sink.add("V14", "trips must be sorted by (vehicle_id, cycle_index, leg_index) without duplicates",
                     trip_id=t.trip_id)
        prev_key = key
        by_vehicle.setdefault(t.vehicle_id, []).append(t)
        if t.vehicle_id not in sc.vehicles:
            sink.add("V14", "trip vehicle not in fleet", trip_id=t.trip_id, vehicle_id=t.vehicle_id)
        if owner.get(t.vehicle_id) != t.route_id:
            sink.add("V14", "trip vehicle is not listed on the trip's route", trip_id=t.trip_id,
                     vehicle_id=t.vehicle_id)
        pr = plan.routes.get(t.route_id)
        if pr is None:
            sink.add("V14", "trip on unknown route", trip_id=t.trip_id)
        elif not 0 <= t.leg_index < len(pr.legs):
            sink.add("V14", "leg_index out of range", trip_id=t.trip_id)
        else:
            leg = pr.legs[t.leg_index]
            if t.stop_ids != leg.stop_ids or t.in_service != leg.in_service:
                sink.add("V14", "trip stops or in_service differ from plan leg", trip_id=t.trip_id,
                         expected=list(leg.stop_ids), actual=list(t.stop_ids))
        if list(t.stop_seqs) != list(range(len(t.stop_ids))):
            sink.add("V14", "stop_seq must be 0..n-1", trip_id=t.trip_id)
        if any(b < a for a, b in zip(t.sched_s, t.sched_s[1:])):
            sink.add("V14", "sched_s must be non-decreasing in a trip", trip_id=t.trip_id)
        for a, b in zip(t.stop_ids, t.stop_ids[1:]):
            if not f.travel_times.has(a, b):
                sink.add("V14", "segment missing in travel_times", trip_id=t.trip_id, actual=[a, b])

    duty_ids = [d.vehicle_id for d in tt.duties]
    if duty_ids != sorted(set(duty_ids)):
        sink.add("V14", "duties must be sorted by vehicle_id without duplicates", actual=duty_ids)
    if set(duty_ids) != set(by_vehicle):
        sink.add("V14", "duties must list exactly the vehicles with trips",
                 expected=sorted(by_vehicle), actual=sorted(set(duty_ids)))
    duties = {d.vehicle_id: d for d in tt.duties}
    for vid in sorted(by_vehicle):
        ts = sorted(by_vehicle[vid], key=lambda t: (t.cycle_index, t.leg_index))
        d = duties.get(vid)
        if d is not None:
            if list(d.trip_ids) != [t.trip_id for t in ts] or d.route_id != ts[0].route_id:
                sink.add("V14", "duty trip_ids/route_id differ from trips", vehicle_id=vid)
            if d.start_s != ts[0].sched_s[0] or d.end_s != ts[-1].sched_s[-1]:
                sink.add("V14", "duty start_s/end_s differ from trips", vehicle_id=vid,
                         expected=[ts[0].sched_s[0], ts[-1].sched_s[-1]], actual=[d.start_s, d.end_s])
        veh = sc.vehicles.get(vid)
        if veh is None:
            continue
        lo, hi = max(sc.start_s, veh.available_start_s), min(sc.end_s, veh.available_end_s)
        for t in ts:
            if any(not lo <= s <= hi for s in t.sched_s):
                sink.add("V14", "sched_s outside the vehicle window", trip_id=t.trip_id, vehicle_id=vid,
                         expected=[lo, hi], actual=[min(t.sched_s), max(t.sched_s)])
        for a, b in zip(ts, ts[1:]):
            if a.stop_ids[-1] != b.stop_ids[0]:
                sink.add("V14", "consecutive trips are not connected", trip_id=b.trip_id, vehicle_id=vid)
            if b.sched_s[0] < a.sched_s[-1]:
                sink.add("V14", "trips of a vehicle overlap in the schedule", trip_id=b.trip_id, vehicle_id=vid,
                         expected=f">= {a.sched_s[-1]}", actual=b.sched_s[0])
        if veh.initial_stop_id is not None and veh.initial_stop_id != ts[0].stop_ids[0]:
            sink.add("V14", "duty does not start at initial_stop_id", vehicle_id=vid)
        if veh.end_stop_id is not None and veh.end_stop_id != ts[-1].stop_ids[-1]:
            sink.add("V14", "duty does not end at end_stop_id", vehicle_id=vid)

    if tt.origin != "generated":
        return
    for r in tt.routes:
        vids = sorted(r.vehicle_ids)
        if not vids:
            if r.period_s is not None:
                sink.add("V14", "period_s must be null for a route without vehicles", actual=r.period_s)
            continue
        if r.period_s is None or r.period_s <= 0:
            sink.add("V14", "generated route needs a positive period_s", actual=r.period_s)
            continue
        if any(v not in sc.vehicles for v in vids):
            continue
        n, period = len(vids), r.period_s
        start = max([sc.start_s] + [sc.vehicles[v].available_start_s for v in vids])
        for k, vid in enumerate(vids):
            anchors = sorted((t.cycle_index, t.sched_s[0]) for t in by_vehicle.get(vid, [])
                             if t.leg_index == 0 and t.route_id == r.route_id)
            if not anchors:
                continue
            offset = start + (k * period) // n
            if [m for m, _ in anchors] != list(range(len(anchors))):
                sink.add("V14", "generated cycle_index must be 0..m-1", vehicle_id=vid,
                         actual=[m for m, _ in anchors][:20])
                continue
            if anchors[0][1] != offset:
                sink.add("V14", "cycle 0 anchor time differs from start + floor(k*period_s/n)", vehicle_id=vid,
                         expected=offset, actual=anchors[0][1])
            for m, s0 in anchors[1:]:
                # §1.3: s = max(offset + m·P, 직전 cycle의 계획 ready). 슬롯보다 이르게 출발하지 않는다.
                if s0 < offset + m * period:
                    sink.add("V14", "cycle anchor earlier than start + floor(k*period_s/n) + m*period_s",
                             vehicle_id=vid, expected=f">= {offset + m * period}", actual=s0)


# ---------------------------------------------------------------- V15 식별·해시

def check_v15(c: Ctx, sink: Sink) -> None:
    f = c.f
    run, ri, raw = f.run, f.run_inputs, f.raw
    sha = {k: sha256_hex(raw[k]) for k in ("scenario", "arrivals", "travel_times", "plan", "timetable",
                                           "events", "metrics")}

    def same(name: str, expected: Any, actual: Any) -> None:
        if expected != actual:
            sink.add("V15", f"{name} differs", expected=expected, actual=actual)

    same("run.plan_id vs plan.json", f.plan.plan_id, run.get("plan_id"))
    same("run.plan_revision vs plan.json", f.plan.revision, run.get("plan_revision"))
    same("run.input_bundle_id vs run_inputs.json", ri.get("input_bundle_id"), run.get("input_bundle_id"))
    same("run.timetable_origin vs timetable.json", f.timetable.origin, run.get("timetable_origin"))
    same("run.plan_sha256", sha["plan"], run.get("plan_sha256"))
    same("run.timetable_sha256", sha["timetable"], run.get("timetable_sha256"))
    same("run.event_log_sha256", sha["events"], run.get("event_log_sha256"))
    same("run.metrics_sha256", sha["metrics"], run.get("metrics_sha256"))
    meta = ri.get("arrival_meta") if isinstance(ri.get("arrival_meta"), dict) else {}
    same("run_inputs.arrival_meta.sha256", sha["arrivals"], meta.get("sha256"))
    same("run_inputs.arrival_meta.passenger_count", len(f.passengers), meta.get("passenger_count"))
    same("run_inputs.travel_time_sha256", sha["travel_times"], ri.get("travel_time_sha256"))
    same("run_inputs.scenario_sha256", sha["scenario"], ri.get("scenario_sha256"))
    same("run_inputs.scenario_id vs scenario.json", f.scenario.scenario_id, ri.get("scenario_id"))
    same("plan.scenario_id vs scenario.json", f.scenario.scenario_id, f.plan.scenario_id)
    same("travel_times.network_snapshot_id vs scenario.json", f.scenario.network_snapshot_id,
         f.travel_times.network_snapshot_id)
    checksum = sha256_hex(canonical_json({"arrivals": sha["arrivals"], "travel_times": sha["travel_times"],
                                          "scenario": sha["scenario"]}).encode("utf-8"))
    same("run_inputs.checksum (recomputed)", checksum, ri.get("checksum"))
    same("run.input_checksum (recomputed)", checksum, run.get("input_checksum"))
    cfg = run.get("config_hash")
    if isinstance(cfg, str):
        run_id = "run-" + sha256_hex(f"{sha['plan']}|{checksum}|{cfg}".encode("utf-8"))[:20]
        same("run.run_id (recomputed from plan_sha256|input_checksum|config_hash)", run_id, run.get("run_id"))
    else:
        sink.add("V15", "run.config_hash missing")
    m = f.metrics if isinstance(f.metrics, dict) else {}
    same("metrics.plan_id vs plan.json", f.plan.plan_id, m.get("plan_id"))
    same("metrics.plan_revision vs plan.json", f.plan.revision, m.get("plan_revision"))


SEMANTIC_CHECKS: tuple[tuple[str, Callable[[Ctx, Sink], None]], ...] = (
    ("V02", check_v02), ("V03", check_v03), ("V04", check_v04), ("V05", check_v05), ("V06", check_v06),
    ("V07", check_v07), ("V08", check_v08), ("V09", check_v09), ("V10", check_v10), ("V11", check_v11),
    ("V12", check_v12), ("V13", check_v13), ("V14", check_v14), ("V15", check_v15),
)
