"""마을·경기버스 흡수를 정차와 남는 정원으로 다시 계산한다(정밀 흡수).

묶음의 `maeul_reach`·`gg_reach`는 "출발 정류장 200m 안 → 역 300m 안을 순서대로 지나는 노선이 있으면 전부 흡수"다.
여기서는 아래 두 조건을 모두 따진다.

1. 정차(`*_exact_stop`이 켜진 수단만): 그 노선이 승객의 바로 그 정류장에 서고, 그 뒤에(egress면 그 앞에) 그 역의 정류장에 선다.
   끈 수단은 묶음의 판정(출발 200m 안 → 역 300m 안을 순서대로 지나는 노선, 행의 `maeul_routes`·`gg_routes`)을 그대로 쓴다.
   기본값: 마을은 끔(정원만 적용), 경기는 켬(정차·역 이동·정원).
   - 역의 정류장 = `network/station_stops.csv`의 `candidates`(역 주변 정류장 목록, 지정 정류장 포함).
   - 마을: `absorbing_routes.csv`의 정차 순서(패턴 여러 개는 `|`로 구분).
   - 경기: 정차 순서 + 그 시간 운행 1회 이상. 운행 조건은 묶음 행의 `gg_routes`(시간대별로 이미 걸러진 노선 목록)와 교집합으로 확인한다.
2. 남는 정원: 노선·날짜·시간마다 남는 자리 = 시간당 운행 × 차량 정원 − 이미 타고 있는 인원.
   - 시간당 운행 = `trips_0610` ÷ 4(06~09시 평균).
   - 마을: 이미 타고 있는 인원 = 그 노선의 그 시간 승차 합(`stop_route_boardings`) × `maeul_load_ratio`.
   - 경기: 승차 자료가 없어 정원 × `gg_busy_ratio`가 이미 차 있다고 본다.
   - 같은 노선·시간을 원하는 수요가 남는 자리보다 많으면 비례로 나눈다. 수요가 여러 노선을 탈 수 있으면 노선 수로 나눠 요청한다.

정원·혼잡 값은 자료가 없는 가정이다(`AbsorbParams`). 결과의 흡수 인원은 그 가정에 직접 달려 있다.
"""

from __future__ import annotations

import csv
import gzip
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from .data import FlowRow


@dataclass(frozen=True)
class AbsorbParams:
    maeul_cap: int = 35            # 마을버스 1대 정원(좌석+입석, 가정)
    gg_cap: int = 60               # 경기버스 1대 정원(가정)
    maeul_load_ratio: float = 0.6  # 마을: 시간당 승차 중 가장 붐비는 구간에 타 있는 비율(가정)
    gg_busy_ratio: float = 0.6     # 경기: 이미 차 있는 정원 비율(가정)
    hours: int = 4                 # trips_0610을 나눌 시간 수(06~09시)
    maeul_exact_stop: bool = False # 마을도 바로 그 정류장·역 정류장 정차를 요구할지
    gg_exact_stop: bool = True     # 경기는 바로 그 정류장 정차 + 역 정류장 정차를 요구


def _read(path: Path) -> list[dict]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


class Absorber:
    def __init__(self, root: Path, p: AbsorbParams = AbsorbParams()):
        root = Path(root)
        self.p = p
        self.patterns: dict[str, list[list[str]]] = {}
        self.mode: dict[str, str] = {}
        self.runs_h: dict[str, float] = {}
        for r in _read(root / "network" / "absorbing_routes.csv"):
            pats = [x.split() for x in r["stops"].split("|")] if r.get("order_known") == "True" else []
            self.patterns[r["route_id"]] = [x for x in pats if x]
            self.mode[r["route_id"]] = r["mode"]
            self.runs_h[r["route_id"]] = float(r["trips_0610"] or 0) / p.hours
        self.station_stops: dict[str, set[str]] = defaultdict(set)
        for r in _read(root / "network" / "station_stops.csv"):
            self.station_stops[r["station_id"]] |= set(r["candidates"].split()) | {r["station_stop_id"]}
        self.boardings: dict[tuple[str, str, int], float] = defaultdict(float)   # (route, date, hour) → 승차 합
        for r in _read(root / "demand" / "stop_route_boardings.csv.gz"):
            if r["route_id"] in self.mode:
                self.boardings[(r["route_id"], r["date"], int(r["hour"]))] += float(r["boardings"])
        # 행의 원래 흡수 노선 목록(경기는 시간대 운행으로 이미 걸러짐)
        self.row_routes: dict[tuple, tuple[set[str], set[str]]] = {}
        for r in _read(root / "demand" / "demand_flows.csv.gz"):
            if r["walkable"] != "False" or not r["station_stop_id"]:
                continue
            key = (r["date"], int(r["hour"]), r["kind"], r["stop_id"], r["station_id"], r["station_stop_id"])
            m, g = self.row_routes.get(key, (set(), set()))
            self.row_routes[key] = (m | set(r["maeul_routes"].split()), g | set(r["gg_routes"].split()))

    def stops_in_order(self, route: str, a: str, b_set: set[str]) -> bool:
        """노선의 어떤 패턴에서 a 다음에 b_set의 정류장이 나오는가."""
        for pat in self.patterns.get(route, ()):
            if a not in pat:
                continue
            i = pat.index(a)
            if any(x in b_set for x in pat[i + 1:]):
                return True
        return False

    def stops_before(self, route: str, a_set: set[str], b: str) -> bool:
        """노선의 어떤 패턴에서 a_set의 정류장 다음에 b가 나오는가(egress: 역 → 동네)."""
        for pat in self.patterns.get(route, ()):
            if b not in pat:
                continue
            j = pat.index(b)
            if any(x in a_set for x in pat[:j]):
                return True
        return False

    def candidates(self, row: FlowRow, mode: str) -> list[str]:
        """이 행을 실제로 태울 수 있는 노선(정차 조건). mode = maeul | gg."""
        key = (row.date, row.hour, row.kind, row.stop_id, row.station_id, row.station_stop_id)
        m, g = self.row_routes.get(key, (set(), set()))
        pool = m if mode == "maeul" else g
        st = self.station_stops.get(row.station_id, set())
        exact = self.p.maeul_exact_stop if mode == "maeul" else self.p.gg_exact_stop
        out = []
        for rt in sorted(pool):
            if self.mode.get(rt) != mode:
                continue
            if not exact:
                out.append(rt)
                continue
            ok = self.stops_in_order(rt, row.stop_id, st) if row.kind == "access" else self.stops_before(rt, st, row.stop_id)
            if ok:
                out.append(rt)
        return out

    def spare(self, route: str, date: str, hour: int) -> float:
        runs = self.runs_h.get(route, 0.0)
        if self.mode.get(route) == "maeul":
            cap = runs * self.p.maeul_cap
            used = self.boardings.get((route, date, hour), 0.0) * self.p.maeul_load_ratio
        else:
            cap = runs * self.p.gg_cap
            used = cap * self.p.gg_busy_ratio
        return max(0.0, cap - used)

    def absorb(self, rows: list[FlowRow], use_maeul: bool, use_gg: bool) -> tuple[list[tuple[FlowRow, float]], dict]:
        """행마다 (행, 남은 셔틀 수요) 와 요약. 마을을 먼저 채우고, 남은 몫을 경기가 채운다."""
        left = {i: float(r.flow) for i, r in enumerate(rows)}
        summary = {"maeul": 0.0, "gg": 0.0, "maeul_req": 0.0, "gg_req": 0.0}
        for mode, on in (("maeul", use_maeul), ("gg", use_gg)):
            if not on:
                continue
            req: dict[tuple[str, str, int], float] = defaultdict(float)
            asks: list[tuple[int, list[str], float]] = []
            for i, r in enumerate(rows):
                if left[i] <= 0:
                    continue
                cs = self.candidates(r, mode)
                if not cs:
                    continue
                share = left[i] / len(cs)
                asks.append((i, cs, share))
                for rt in cs:
                    req[(rt, r.date, r.hour)] += share
            ratio = {k: (min(1.0, self.spare(k[0], k[1], k[2]) / v) if v > 0 else 1.0) for k, v in req.items()}
            for i, cs, share in asks:
                r = rows[i]
                got = sum(share * ratio[(rt, r.date, r.hour)] for rt in cs)
                summary[f"{mode}_req"] += left[i]
                summary[mode] += got
                left[i] -= got
        return [(r, max(0.0, left[i])) for i, r in enumerate(rows)], summary


def residual_rows(rows: list[FlowRow], absorbed: list[tuple[FlowRow, float]]) -> list[FlowRow]:
    """흡수 뒤 남은 셔틀 수요를 FlowRow로(유량 6자리 문자열, 0 이하는 뺀다)."""
    out = []
    for r, f in absorbed:
        if f > 1e-9:
            out.append(FlowRow(r.date, r.hour, r.kind, r.stop_id, r.station_id, r.station_stop_id, f"{f:.6f}",
                               False, False))
    return out
