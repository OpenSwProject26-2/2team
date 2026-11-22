"""파업 시나리오 수요(설계 5.1, 지자체 절차 ① 파업 영향 분석) — 파업 노선 집합 S로 셔틀 대상 수요를 만든다.

생성기를 특정 파업(2026-01 전면 파업)에 묶지 않고, 파업 비율·노선 조합이 바뀌어도 같은 규칙으로 돈다.
결과는 `data.FlowRow` 목록이라 기존 흐름(정밀 흡수 `absorb.Absorber` → 남은 셔틀 수요)에 그대로 들어간다.

정의(구 묶음 하나, 날짜·시간·종류·정류장·역 행마다; `data.load_flows`와 같은 모집단: 걸어서 못 감 + 역 지정 정류장 있음)
- 파업 몫: flow_S = flow × frac_S(타는 정류장, 시간, 날짜)
  - flow = 묶음의 파업 노선(등록부 affected 전체) 승객(`demand_flows.flow`).
  - frac_S = Σ_{r∈S} 승차 ÷ Σ_{r∈affected} 승차(`demand/stop_route_boardings`, 타는 정류장 = access는 출발 정류장,
    egress는 역 정류장). S = affected 전체면 1이라 묶음 수요와 같다. 그 칸에 affected 승차 기록이 없으면 |S| ÷ |affected|.
- 시내버스 흡수: 운행 중인 시내버스(묶음 GTFS 노선 중 마을버스가 아니고 S 밖인 노선: 파업 안 한 affected·unaffected·
  미확인) 운행편 하나가 타는 정류장 → 내리는 정류장 순서로 지나면 그 행을 뺀다. 부분 파업에서 새로 생기는 흡수다.
  정원은 보지 않는다.
- 마을·경기버스 흡수는 여기서 하지 않는다. 그 뒤 `absorb.Absorber`(정밀 흡수: 정차·남는 정원)가 한다. 모든 비율에 같은 순서.
- all_bus_no_absorption(mode="all"): 파업과 무관하게 걸어서 못 가는 모든 버스 승객(flow_total)을 흡수 없이 대상으로.
  "파업 없음" 대조군이 아니다(파업이 없으면 셔틀 대상이 없다). 이름을 그렇게 바꿨다(2026-11-21).
"""

from __future__ import annotations

import csv
import gzip
import random
from dataclasses import dataclass
from pathlib import Path

from .data import FlowRow, _read_csv

MODES = ("strike", "all")


@dataclass(frozen=True)
class StrikeSet:
    routes: frozenset[str]          # 파업 노선(묶음 GTFS route_id)
    eligible: tuple[str, ...]       # 파업 대상 후보(등록부 affected) — 비율의 분모
    rate: float                     # 명목 파업 비율
    draw: int | None = None         # 무작위 뽑기 번호
    seed: int | None = None

    def record(self) -> dict:
        return {"rate": self.rate, "draw": self.draw, "seed": self.seed, "routes": sorted(self.routes),
                "eligible": len(self.eligible)}


def eligible_routes(root: Path) -> tuple[str, ...]:
    """파업 대상 후보 = 묶음 등록부 strike_state == affected 이면서 묶음 GTFS에 있는 노선(ID 순)."""
    root = Path(root)
    gtfs = {r["route_id"] for r in _read_csv(root / "gtfs" / "routes.txt")}
    return tuple(sorted(r["route_id"] for r in _read_csv(root / "registry" / "strike_route_registry.csv")
                        if r["strike_state"] == "affected" and r["route_id"] in gtfs))


def draw_strike(root: Path, rate: float, draw: int, seed: int = 20261121) -> StrikeSet:
    """후보 중 round(rate × n)개를 무작위로 뽑는다. (seed, rate, draw)가 같으면 같은 집합(실험·테스트 주간에서도 같다)."""
    elig = eligible_routes(root)
    k = round(rate * len(elig))
    rng = random.Random(f"{seed}:{rate:.4f}:{draw}")
    chosen = frozenset(rng.sample(elig, k)) if k else frozenset()
    return StrikeSet(chosen, elig, rate, draw, seed)


def full_strike(root: Path) -> StrikeSet:
    elig = eligible_routes(root)
    return StrikeSet(frozenset(elig), elig, 1.0, None, None)


class ReachIndex:
    """(타는 정류장, 내리는 정류장) → 그 순서로 지나는 묶음 GTFS 노선. maeul = 마을버스 노선 집합."""

    def __init__(self, root: Path):
        root = Path(root)
        routes = _read_csv(root / "gtfs" / "routes.txt")
        self.maeul = frozenset(r["route_id"] for r in routes if "마을" in (r.get("route_desc") or ""))
        trip_route = {r["trip_id"]: r["route_id"] for r in _read_csv(root / "gtfs" / "trips.txt")}
        seqs: dict[str, list[tuple[int, str]]] = {}
        with open(root / "gtfs" / "stop_times.txt", encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                seqs.setdefault(r["trip_id"], []).append((int(r["stop_sequence"]), r["stop_id"]))
        orders: dict[str, set[tuple[str, ...]]] = {}
        for t, s in seqs.items():
            rid = trip_route.get(t)
            if rid:
                orders.setdefault(rid, set()).add(tuple(x for _, x in sorted(s)))
        self.orders = {r: sorted(v) for r, v in orders.items()}
        self._pos: dict[str, dict[str, list[tuple[int, int]]]] = {}
        for rid, trips in self.orders.items():
            for ti, seq in enumerate(trips):
                for i, s in enumerate(seq):
                    self._pos.setdefault(s, {}).setdefault(rid, []).append((ti, i))
        self._cache: dict[tuple[str, str], frozenset[str]] = {}

    def routes(self, a: str, b: str) -> frozenset[str]:
        got = self._cache.get((a, b))
        if got is not None:
            return got
        pa, pb = self._pos.get(a, {}), self._pos.get(b, {})
        hit = set()
        for rid in pa.keys() & pb.keys():
            ib: dict[int, list[int]] = {}
            for ti, i in pb[rid]:
                ib.setdefault(ti, []).append(i)
            if any(any(j > i for j in ib.get(ti, ())) for ti, i in pa[rid]):
                hit.add(rid)
        got = self._cache[(a, b)] = frozenset(hit)
        return got


def _boardings(root: Path) -> dict[tuple[str, str, int], dict[str, float]]:
    """(날짜, 정류장, 시간) → {노선: 승차}(affected 노선만)."""
    out: dict[tuple[str, str, int], dict[str, float]] = {}
    with gzip.open(Path(root) / "demand" / "stop_route_boardings.csv.gz", "rt", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if r["strike_state"] != "affected":
                continue
            b = float(r["boardings"])
            if b > 0:
                d = out.setdefault((r["date"], r["stop_id"], int(r["hour"])), {})
                d[r["route_id"]] = d.get(r["route_id"], 0.0) + b
    return out


def strike_rows(root: Path, strike: StrikeSet | None, mode: str = "strike",
                reach: ReachIndex | None = None) -> tuple[list[FlowRow], dict]:
    """셔틀 대상 유량 행(정밀 흡수 전)과 집계(하루 평균). mode = strike | all(all_bus_no_absorption)."""
    root = Path(root)
    if mode not in MODES:
        raise ValueError(f"mode는 {MODES} 중 하나: {mode}")
    reach = reach or ReachIndex(root)
    city_running = set(reach.orders) - reach.maeul - (set(strike.routes) if strike else set())
    board = _boardings(root) if mode == "strike" else {}
    fallback = (len(strike.routes) / len(strike.eligible)) if (strike and strike.eligible) else 0.0
    out: list[FlowRow] = []
    st = {"flow_bundle": 0.0, "flow_strike_share": 0.0, "absorbed_running_city": 0.0, "flow_out": 0.0,
          "fallback_cells": 0}
    dates = set()
    for r in _read_csv(root / "demand" / "demand_flows.csv.gz"):
        if r["walkable"] != "False" or not r["station_stop_id"]:
            continue
        dates.add(r["date"])
        kind, s, ss = r["kind"], r["stop_id"], r["station_stop_id"]
        o, d = (s, ss) if kind == "access" else (ss, s)
        if mode == "all":
            v = float(r["flow_total"])
        else:
            base = float(r["flow"])
            st["flow_bundle"] += base
            if base <= 0 or strike is None or not strike.routes:
                continue
            cell = board.get((r["date"], o, int(r["hour"])))
            if cell:
                frac = sum(x for k, x in cell.items() if k in strike.routes) / sum(cell.values())
            else:
                frac = fallback
                st["fallback_cells"] += 1
            v = base * frac
            if v <= 0:
                continue
            st["flow_strike_share"] += v
            if reach.routes(o, d) & city_running:
                st["absorbed_running_city"] += v
                continue
        if v <= 0:
            continue
        out.append(FlowRow(r["date"], int(r["hour"]), kind, s, r["station_id"], ss, f"{v:.6f}",
                           (r["maeul_reach"] == "True") if mode == "strike" else False,
                           (r["gg_reach"] == "True") if mode == "strike" else False))
        st["flow_out"] += v
    nd = max(1, len(dates))
    st = {k: (round(v / nd, 2) if isinstance(v, float) else v) for k, v in st.items()}
    st.update(mode="strike" if mode == "strike" else "all_bus_no_absorption", days=nd)
    return out, st
