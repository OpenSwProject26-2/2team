"""탈 수 있는지 판정하는 규칙(설계 7절): 목적 역 대체 · 역 도착 · 걷기 · 도보 시간.

생성기(`generate`의 덮는 수요), 배분기(`allocate.Model`에 넣는 덮는 OD 키), 평가(시뮬레이터 입력의 수요 재배정)가
모두 이 모듈 하나를 쓴다. 그래서 설계와 평가가 같은 규칙으로 "탈 수 있다"를 센다. 3단계의 "탈 수 있는 수요 비율"도
`Reach.rideable`로 같은 규칙을 쓴다.

수요 OD 키
- access (동네 정류장 o, 역 k의 access 지정 정류장 d): o 쪽이 동네 끝, d 쪽이 역 끝.
- egress (역 k의 egress 지정 정류장 d, 동네 정류장 o): d 쪽이 역 끝, o 쪽이 동네 끝.
- 키가 어느 역(들)의 것인지는 수요 원자료(역 ID가 있음)에서 정한다(`StationIndex.add_flows`). 원자료에 없는 키는
  지정 정류장을 그 역으로 쓰는 역 모두로 본다.

규칙(`EvalRule`)
- 목적 역 대체 `station_mode`: `fixed`(그 역만) / `same_line`(호선·지선 꼬리표가 하나라도 겹치는 역, 꼬리표는
  `station_lines.csv`) / `any`(구 묶음의 아무 역). 대상 역은 그 방향(access/egress) 지정 정류장이 있는(status=ok) 역뿐이다.
- 역 도착 `station_arrival`: `candidates`(기본) = 그 역의 주변 정류장 목록(`station_stops.csv`의 `candidates`,
  역 좌표 250m 안 정류장)에 내리면 도착(egress면 거기서 탄다). 역 쪽에는 걷기가 없다.
  `walk_designated`(참고 구현·예전 평가 재현용) = 역 지정 정류장에서 걸어서 `walk_radius_m` 안 정류장.
- 걷기 `walk_radius_m`: 동네 쪽 끝에서 도로 도보 거리 R m 안 정류장에서 타거나 내린다(0이면 그 정류장만).
- 같은 정류장을 여러 역이 지정 정류장으로 쓰는 경우(구로: 온수 1·7호선, 가산디지털단지 1·7호선 access, 영등포구청 2·5호선)
  `shared_stop`: OD 키의 역은 수요 원자료(역 ID가 있음)에서 정한다.
  `flow_union`(기본) = 그 키에 유량이 있는 역들 중 어느 역의 도착 정류장이어도 된다(한 키에 두 역 유량이 섞인 경우만 후하다).
  `stop_union`(참고 구현) = 원자료를 보지 않고 정류장으로만 정한다. 역 = 그 정류장을 지정 정류장으로 쓰는 모든 역의 합,
  방향 = 도착이 access 지정 정류장이면 access(두 끝이 모두 역 지정 정류장인 키도 access로 본다).
  `strict` = 그 키에 유량이 있는 모든 역에 대해 받아들일 수 있는 도착 정류장이어야 한다(교집합).
- `transfer_complex`(기본 끔): 켜면 같은 이름 역(환승역의 호선별 ID)의 꼬리표를 합친다.
- 도보 시간 `walk_speed_m_min`(분당 75m), `walk_wait_limit_s`(1,200초): 동네 쪽 도보 시간 + 대기가 한도를 넘으면 못 탄 것.
  시뮬레이터는 그대로 두고 M07 결과(passenger_outcomes)에서 따로 센다(`served_with_walk`).

재배정 순서(`Reach.best_pair`): ① 원래 (o, d)로 바로 탈 수 있으면 그대로 ② 원래 역(들)의 도착 정류장으로 가는 짝을 대체 역보다
먼저 ③ 도보 거리가 짧은 것(`candidates`에서는 동네 쪽 도보만 있다. `walk_designated`는 참고 구현처럼 양쪽 도보 합)
④ 차내 시간(주면)이 짧은 것 ⑤ 정류장 ID 순.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Callable, Iterable

STATION_MODES = ("fixed", "same_line", "any")
ARRIVALS = ("candidates", "walk_designated")
SHARED = ("flow_union", "stop_union", "strict")
KINDS = ("access", "egress")


@dataclass(frozen=True)
class EvalRule:
    station_mode: str = "same_line"
    walk_radius_m: float = 400.0
    station_arrival: str = "candidates"
    shared_stop: str = "flow_union"
    transfer_complex: bool = False
    walk_speed_m_min: float | None = 75.0
    walk_wait_limit_s: int = 1200

    def __post_init__(self):
        if self.station_mode not in STATION_MODES:
            raise ValueError(f"station_mode는 {STATION_MODES} 중 하나: {self.station_mode}")
        if self.station_arrival not in ARRIVALS:
            raise ValueError(f"station_arrival은 {ARRIVALS} 중 하나: {self.station_arrival}")
        if self.shared_stop not in SHARED:
            raise ValueError(f"shared_stop은 {SHARED} 중 하나: {self.shared_stop}")
        if self.walk_radius_m < 0:
            raise ValueError("walk_radius_m은 0 이상")

    @classmethod
    def from_dict(cls, d: dict) -> "EvalRule":
        names = {f.name for f in fields(cls)}
        bad = sorted(set(d) - names)
        if bad:
            raise ValueError(f"EvalRule에 없는 값 {bad}")
        return cls(**d)

    def record(self) -> dict:
        return asdict(self)

    def label(self) -> str:
        s = f"{self.station_mode}-w{int(round(self.walk_radius_m))}"
        if self.station_arrival != "candidates":
            s += "-wd"
        if self.shared_stop != "flow_union":
            s += "-" + self.shared_stop
        if self.transfer_complex:
            s += "-cx"
        return s

    def walk_s(self, walk_m: float) -> int:
        """동네 쪽 도보 시간(초). 속도가 없으면 0."""
        if not self.walk_speed_m_min:
            return 0
        return int(round(walk_m / self.walk_speed_m_min * 60))


# ---------------------------------------------------------------- 역 자료

def load_lines(path: Path | str) -> tuple[dict[str, frozenset[str]], dict[str, str]]:
    """`station_lines.csv` → ({역 ID: 꼬리표}, {역 ID: 같은 이름 묶음})."""
    tags, cx = {}, {}
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            tags[r["station_id"]] = frozenset(r["tags"].split())
            cx[r["station_id"]] = r.get("complex") or r["station_id"]
    return tags, cx


class StationIndex:
    """구 묶음의 역: 방향별 지정 정류장, 주변 정류장 목록, 호선 꼬리표, OD 키 → 역."""

    def __init__(self, designated: dict[tuple[str, str], str], candidates: dict[str, Iterable[str]],
                 tags: dict[str, frozenset[str]], complexes: dict[str, str] | None = None,
                 names: dict[str, str] | None = None):
        self.designated = dict(designated)                         # (역, kind) → 지정 정류장(status=ok)
        self.by_stop: dict[str, dict[str, set[str]]] = {k: defaultdict(set) for k in KINDS}
        for (k, kind), s in self.designated.items():
            self.by_stop[kind][s].add(k)
        self.ok = {kind: sorted(k for (k, kd) in self.designated if kd == kind) for kind in KINDS}
        self.candidates = {k: frozenset(set(v) | {s for (kk, _), s in self.designated.items() if kk == k})
                           for k, v in candidates.items()}
        for (k, _), s in self.designated.items():
            self.candidates.setdefault(k, frozenset({s}))
            if s not in self.candidates[k]:
                self.candidates[k] = self.candidates[k] | {s}
        stations = {k for k, _ in self.designated} | set(self.candidates)
        self.missing_lines = sorted(k for k in stations if k not in tags)
        self.tags = {k: tags.get(k, frozenset({f"?{k}"})) for k in stations}   # 표에 없는 역은 자기 자신만
        names = names or {}
        self.names = names
        self.complex = {k: (complexes or {}).get(k, names.get(k, k)) for k in stations}
        self.od_stations: dict[tuple[str, str], dict[str, dict[str, float]]] = {}   # 키 → kind → 역 → 유량

    @classmethod
    def from_root(cls, root: Path | str, lines_path: Path | str) -> "StationIndex":
        net = Path(root) / "network"
        designated, cand, names = {}, {}, {}
        with open(net / "station_stops.csv", encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                names[r["station_id"]] = r["station_name"]
                if r["status"] != "ok":
                    continue
                designated[(r["station_id"], r["kind"])] = r["station_stop_id"]
                cand.setdefault(r["station_id"], set()).update(r["candidates"].split())
        tags, cx = load_lines(lines_path)
        return cls(designated, cand, tags, cx, names)

    def add_flows(self, rows: Iterable) -> None:
        """수요 원자료(FlowRow: kind, stop_id, station_id, station_stop_id, flow)로 OD 키 → 역을 채운다."""
        for r in rows:
            key = (r.stop_id, r.station_stop_id) if r.kind == "access" else (r.station_stop_id, r.stop_id)
            m = self.od_stations.setdefault(key, {}).setdefault(r.kind, {})
            m[r.station_id] = m.get(r.station_id, 0.0) + float(r.flow)

    def station_stops(self) -> set[str]:
        return set(self.designated.values())

    def key_info(self, key: tuple[str, str], by_stop: bool = False) -> tuple[str, frozenset[str]] | None:
        """(kind, 역들). 원자료가 있으면 유량이 큰 kind와 그 키에 유량이 있는 역들, 없으면 지정 정류장으로 정한다
        (access 먼저). by_stop이면 원자료를 보지 않고 정류장으로만 정한다(참고 구현): 도착이 access 지정 정류장이면
        access, 아니면 출발이 egress 지정 정류장이면 egress, 역들 = 그 지정 정류장을 쓰는 모든 역."""
        o, d = key
        got = None if by_stop else self.od_stations.get(key)
        if got:
            kind = max(got, key=lambda kd: (sum(got[kd].values()), kd == "access"))
            st = frozenset(k for k, v in got[kind].items() if (k, kind) in self.designated)
            if st:
                return kind, st
        if d in self.by_stop["access"]:
            return "access", frozenset(self.by_stop["access"][d])
        if o in self.by_stop["egress"]:
            return "egress", frozenset(self.by_stop["egress"][o])
        return None

    def line_tags(self, k: str, complex_: bool) -> frozenset[str]:
        if not complex_:
            return self.tags[k]
        c = self.complex[k]
        return frozenset(t for kk, cc in self.complex.items() if cc == c for t in self.tags[kk])

    def acceptable(self, k: str, kind: str, rule: EvalRule) -> list[str]:
        """역 k로 가려는 승객이 받아들이는 역(그 방향 지정 정류장이 있는 역만)."""
        if rule.station_mode == "fixed":
            return [k] if (k, kind) in self.designated else []
        if rule.station_mode == "any":
            return list(self.ok[kind])
        tg = self.line_tags(k, rule.transfer_complex)
        return [x for x in self.ok[kind] if x == k or self.line_tags(x, rule.transfer_complex) & tg]


# ---------------------------------------------------------------- 판정

class Reach:
    """규칙 하나 + 도보 이웃 하나로 OD 키마다 동네 쪽 정류장(도보 m)과 역 쪽 정류장(역 쪽 도보 m)을 정한다."""

    def __init__(self, index: StationIndex, rule: EvalRule, nb: dict[str, dict[str, float]] | None,
                 keys: Iterable[tuple[str, str]] = ()):
        self.ix, self.rule = index, rule
        self.nb = nb or {}
        self._home: dict[str, dict[str, float]] = {}
        self._arr: dict[tuple, dict[str, float]] = {}
        self._info: dict[tuple[str, str], tuple | None] = {}
        self._keys_by_home: dict[str, list[tuple[str, str]]] | None = None
        self._rev_home: dict[str, list[str]] | None = None
        self._aid: dict[tuple, int] = {}
        self._aid_at: dict[str, list[int]] | None = None
        self._kinfo: dict = {}
        self.keys = list(dict.fromkeys(keys)) or sorted(index.od_stations)   # 비우면 수요 원자료의 OD 키
        if self.keys:
            self._build_index()

    # -- 기본 집합
    def home(self, x: str) -> dict[str, float]:
        """동네 정류장 x에서 걸어서 닿는 정류장(자기 자신 0m 포함)."""
        got = self._home.get(x)
        if got is None:
            R = self.rule.walk_radius_m
            got = {x: 0.0}
            if R > 0:
                for s, w in self.nb.get(x, {}).items():
                    if w <= R + 1e-9:
                        got[s] = min(got.get(s, 1e18), float(w))
            self._home[x] = got
        return got

    def _station_arrivals(self, k: str, kind: str, mode: str) -> dict[str, float]:
        rule = self.rule if mode == self.rule.station_mode else EvalRule(**{**self.rule.record(), "station_mode": mode})
        out: dict[str, float] = {}
        for x in self.ix.acceptable(k, kind, rule):
            if self.rule.station_arrival == "candidates":
                for s in self.ix.candidates.get(x, ()):
                    out[s] = 0.0
            else:
                d = self.ix.designated[(x, kind)]
                for s, w in self.home(d).items():
                    out[s] = min(out.get(s, 1e18), w)
        return out

    def arrivals(self, kind: str, stations: frozenset[str], mode: str | None = None) -> dict[str, float]:
        """역 쪽 정류장 → 역 쪽 도보 m. 역이 여럿이면 shared_stop 규칙으로 합친다."""
        mode = mode or self.rule.station_mode
        ck = (kind, stations, mode)
        got = self._arr.get(ck)
        if got is None:
            per = [self._station_arrivals(k, kind, mode) for k in sorted(stations)]
            if not per:
                got = {}
            elif self.rule.shared_stop != "strict" or len(per) == 1:
                got = {}
                for m in per:
                    for s, w in m.items():
                        got[s] = min(got.get(s, 1e18), w)
            else:
                common = set(per[0]).intersection(*per[1:])
                got = {s: max(m[s] for m in per) for s in common}
            self._arr[ck] = got
        return got

    def info(self, key: tuple[str, str]):
        """(kind, 동네 끝 정류장, 역들) 또는 None(역 OD가 아님)."""
        if key not in self._info:
            ki = self.ix.key_info(key, self.rule.shared_stop == "stop_union")
            if ki is None:
                self._info[key] = None
            else:
                kind, st = ki
                self._info[key] = (kind, key[0] if kind == "access" else key[1], st)
        return self._info[key]

    # -- 덮는 OD 키(설계)
    def _build_index(self) -> None:
        kb: dict[str, list] = defaultdict(list)
        rev: dict[str, set[str]] = defaultdict(set)
        aid_at: dict[str, list[int]] = defaultdict(list)
        self._kinfo = {}
        self._aid = {}
        for key in self.keys:
            inf = self.info(key)
            if inf is None:
                continue
            kind, h, st = inf
            ak = (kind, st)
            if ak not in self._aid:
                self._aid[ak] = len(self._aid)
                for s in self.arrivals(kind, st):
                    aid_at[s].append(self._aid[ak])
            self._kinfo[key] = (kind == "access", h, self._aid[ak])
            kb[h].append(key)
            for s in self.home(h):
                rev[s].add(h)
        self._keys_by_home = dict(kb)
        self._rev_home = {s: sorted(v) for s, v in rev.items()}
        self._aid_at = dict(aid_at)

    def cover_keys(self, seq, keys: Iterable[tuple[str, str]] | None = None) -> tuple[tuple[str, str], ...]:
        """이 정차열 하나로 규칙 안에서 탈 수 있는 OD 키(정렬). keys를 주면 그 안에서만(없으면 만들 때 준 키)."""
        if keys is not None:
            keys = list(keys)
            new = [k for k in keys if k not in self._kinfo and self.info(k) is not None]
            if new or self._keys_by_home is None:
                self.keys = list(dict.fromkeys([*self.keys, *keys]))
                self._build_index()
            want = set(keys)
        else:
            want = None
        if self._keys_by_home is None:
            return ()
        pos_min: dict[str, int] = {}
        pos_max: dict[str, int] = {}
        amin: dict[int, int] = {}
        amax: dict[int, int] = {}
        for i, s in enumerate(seq):
            for h in self._rev_home.get(s, ()):
                pos_min.setdefault(h, i)
                pos_max[h] = i
            for a in self._aid_at.get(s, ()):
                amin.setdefault(a, i)
                amax[a] = i
        out = []
        for h in pos_min:
            for key in self._keys_by_home.get(h, ()):
                acc, _, a = self._kinfo[key]
                if a not in amin:
                    continue
                if (pos_min[h] < amax[a]) if acc else (amin[a] < pos_max[h]):
                    if want is None or key in want:
                        out.append(key)
        return tuple(sorted(out))

    def spans(self, seq, keys: Iterable[tuple[str, str]] | None = None, ride: Callable | None = None):
        """정차열 하나로 탈 수 있는 OD 키와 키마다 (탈 정차 순번, 내릴 정차 순번).

        짝은 평가 재배정(`best_pair`, 이 노선 하나만 있을 때)과 같다. 배분기 근사의 구간별 정원(allocate cap_mode="segment")에 쓴다.
        """
        seq = list(seq)
        ks = self.cover_keys(seq, keys)
        pos = {x: i for i, x in enumerate(seq)}
        out = []
        for k in ks:
            b = self.best_pair(k, [seq], ride)
            out.append((pos[b[1]], pos[b[2]]) if b is not None else (0, len(seq) - 1))
        return ks, tuple(out)

    def cover_value(self, seq, od_total: dict[tuple[str, str], float]) -> float:
        return sum(od_total.get(k, 0.0) for k in self.cover_keys(seq))

    def rideable(self, routes: Iterable, od_total: dict[tuple[str, str], float]) -> tuple[set, float]:
        """안(정차열들)으로 규칙 안에서 탈 수 있는 OD 키와 그 수요 합(3단계 형평 지표의 분자)."""
        ks: set = set()
        for seq in routes:
            ks |= set(self.cover_keys(seq))
        return ks, sum(od_total.get(k, 0.0) for k in ks)

    # -- 재배정(평가)
    def best_pair(self, key: tuple[str, str], routes: list, ride: Callable | None = None):
        """(정렬 키, s, t, 동네 쪽 도보 m, 원래 역이면 True) 또는 None. s·t는 승객이 탈·내릴 정류장."""
        o, d = key
        for seq in routes:                      # ① 그대로 탈 수 있으면 그대로
            pos = {x: i for i, x in enumerate(seq)}
            if o in pos and d in pos and pos[o] < pos[d]:
                return ((0, 0.0, 0.0, 0.0, o, d), o, d, 0.0, True)
        inf = self.info(key)
        if inf is None:
            return None
        kind, h, st = inf
        H = self.home(h)
        A = self.arrivals(kind, st)
        A0 = self.arrivals(kind, st, "fixed")
        best = None
        for seq in routes:
            pos = {x: i for i, x in enumerate(seq)}
            hp = [(w, x) for x, w in H.items() if x in pos]
            if not hp:
                continue
            ap = [(w, x) for x, w in A.items() if x in pos]
            for wh, x in hp:
                for wa, y in ap:
                    if kind == "access":
                        i, j, s, t = pos[x], pos[y], x, y
                    else:
                        i, j, s, t = pos[y], pos[x], y, x
                    if i >= j:
                        continue
                    tier = 0 if y in A0 else 1
                    rv = float(ride(seq, i, j)) if ride else 0.0
                    # 역 쪽 걷기가 있는 예전 규칙은 참고 구현처럼 도보 합 → 정류장 ID 순
                    k = ((1 + tier, wh + wa, wh, rv, s, t) if self.rule.station_arrival == "candidates"
                         else (1 + tier, wh + wa, 0.0, rv, s, t))
                    if best is None or k < best[0]:
                        best = (k, s, t, wh, tier == 0)
        return best

    def reassign(self, rows: list[dict], routes: list, ride: Callable | None = None) -> tuple[list[dict], dict, dict]:
        """수요 묶음 행을 규칙 안에서 탈 수 있는 (탈 정류장, 내릴 정류장)으로 옮긴다.

        행을 합치지 않고 aggregate_id·시간대를 그대로 둔다(승객 도착 시각이 재배정 전과 같다).
        돌려주는 값: (새 행, 행별 기록 {aggregate_id: [동네 쪽 도보 m, 분류]}, 요약(승객 수)).
        분류: direct(그대로) / same_station(원래 역, 걷기나 다른 주변 정류장) / alt_station(대체 역) / none(못 탐).
        """
        cache: dict = {}
        per: dict[str, list] = {}
        stats = {"direct": 0, "same_station": 0, "alt_station": 0, "none": 0, "walked": 0, "walk_m_sum": 0.0}
        out = []
        for r in rows:
            o, d, c = r["origin"], r["destination"], int(r["passenger_count"])
            key = (o, d)
            if key not in cache:
                cache[key] = self.best_pair(key, routes, ride)
            b = cache[key]
            if b is None:
                cls, s, t, w = "none", o, d, 0.0
            else:
                _, s, t, w, same = b
                cls = "direct" if (s, t) == (o, d) else ("same_station" if same else "alt_station")
            stats[cls] += c
            if w > 0:
                stats["walked"] += c
                stats["walk_m_sum"] += w * c
            per[r["aggregate_id"]] = [round(w, 1), cls]
            out.append({**r, "origin": s, "destination": t})
        stats["moved"] = stats["same_station"] + stats["alt_station"]
        stats["reachable"] = stats["direct"] + stats["moved"]
        stats["walk_m_avg"] = round(stats.pop("walk_m_sum") / stats["walked"], 1) if stats["walked"] else 0.0
        return out, per, stats


def served_with_walk(outcome_rows: Iterable[dict], walk_m: dict[str, float], rule: EvalRule) -> dict:
    """M07을 통과한 결과(passenger_outcomes)에서 도보 시간을 넣은 탑승을 센다(시뮬레이터는 그대로).

    승객 도착 시각(time_s)을 셔틀 정류장 도착으로 보고(집에서는 도보 시간만큼 먼저 나섬),
    탑승(served) 중 (탑승 시각 − 도착 시각) + 동네 쪽 도보 시간 > walk_wait_limit_s면 못 탄 것으로 센다.
    walk_m: aggregate_id → 동네 쪽 도보 m(없으면 0).
    """
    import json

    served = ok = 0
    late_walkers = 0
    for r in outcome_rows:
        if r["outcome"] != "served":
            continue
        served += 1
        agg = json.loads(r["passenger_id"])[0]
        ws = rule.walk_s(walk_m.get(agg, 0.0))
        if int(r["boarding_s"]) - int(r["time_s"]) + ws <= rule.walk_wait_limit_s:
            ok += 1
        elif ws > 0:
            late_walkers += 1
    return {"served": served, "served_walk": ok, "dropped_by_walk_time": served - ok, "dropped_walkers": late_walkers}
