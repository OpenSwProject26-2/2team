"""닫힌 순회(tour) 후보(옵션 `Params.tour`, 작업 2026-11-24).

지금 순환(`loop.py`)은 편도 가는 길을 먼저 확정한 뒤 돌아오는 길을 붙이고, 역이 바퀴 가운데(꺾는 정류장)에 있다.
tour는 역을 차고지처럼 양 끝에 둔다.

    한 바퀴 = 역 E(egress 쪽 정류장) → 정류장들 → 역 A(access 쪽 정류장) → 고리 닫기 공차(A → E) → E

- 역에서 탄 egress 승객은 가는 동안 내리고, 동네에서 탄 access 승객은 끝의 역에서 내린다. 바퀴 안 모든 정류장이 E보다 뒤,
  A보다 앞이라 한 정류장이 양방향 수요를 다 받는다. 그래서 시작점 최적화(⑥)는 tour에 쓰지 않는다.
- 끝점 쌍(`end_pair`): 그 역 egress 후보(시작) × access 후보(끝) 중 A ≠ E, A → E 최단경로가 고리 닫기 상한 이하이고
  그 경로 안에 U턴이 없는 쌍에서 공차가 가장 짧은 것(동률은 (A, E) ID 순). 후보 = station_stops.csv status=ok 행의
  candidates ∪ 지정 정류장(`load_inputs`). 없는 역은 tour를 만들지 않는다(사유 no_end_pair).
- 제약(`TourLimits`): 순환 시간(근사) = oneway_s(E…A) + 고리 닫기 공차 + 회차 + cycle_rest ≤ cycle_max_s(= 2L),
  정차 수(역 지정 정류장 제외) ≤ max_stops(24), 같은 정류장 반복 금지, U턴 금지(고리 닫는 이음 포함),
  우회율은 기본 검사하지 않음(detour를 주면 방향별 상한: 가는 쪽 E → 가장 먼 정류장, 돌아오는 쪽 그 정류장 → A).
  최종 판정은 generate._finish가 변환기 공차 경로와 route_cycle 주기로 다시 한다.
- 생성기
  ①-T `sector`: 기존 ①의 섹터·씨앗으로 [E, 씨앗, A]에서 시작해 한 바퀴 점수(덮는 수요 증가 ÷ 순환 시간 증가)로 삽입, 2-opt 1회.
  ②-T `feeder`: E → 씨앗 → A를 U턴 없는 최단경로로(삽입 없음, 기준선).
  (③-T 역 쌍 순회는 실험에 쓰지 않아 지웠다, docs/design/pipeline.md.)
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from pathlib import Path

from .graph import Graph
from .loop import closure_s, cyclic_seq, designated_stops

TOUR_KINDS = ("sector", "feeder")


@dataclass(frozen=True)
class TourInputs:
    ends: dict          # 역 → (egress 후보 정류장들, access 후보 정류장들)
    lines: dict         # 역 → (물리적 역 이름, 호선 집합). 기록용(역 쌍 순회를 지운 뒤 생성에는 쓰지 않음)
    tags: dict = field(default_factory=dict)   # 역 → 평가 규칙 꼬리표(station_lines.csv tags, 호선 단위 묶기용)


@dataclass(frozen=True)
class TourLimits:
    cycle_max_s: int
    max_stops: int | None = 24
    detour: float | None = None
    closure_max_s: int = 300


def load_inputs(root: Path | str, lines_path: Path | str) -> TourInputs:
    """station_stops.csv(status=ok 행의 candidates ∪ 지정 정류장)와 station_lines.csv(lines 열)."""
    ends: dict[str, dict[str, set[str]]] = {}
    names: dict[str, str] = {}
    with open(Path(root) / "network" / "station_stops.csv", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            names[r["station_id"]] = r["station_name"]
            if r["status"] != "ok":
                continue
            m = ends.setdefault(r["station_id"], {"egress": set(), "access": set()})
            m[r["kind"]].update(r["candidates"].split())
            m[r["kind"]].add(r["station_stop_id"])
    lines: dict[str, set[str]] = {}
    tags: dict[str, frozenset[str]] = {}
    with open(lines_path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            lines[r["station_id"]] = set(r["lines"].replace(";", " ").split())
            if r.get("tags"):
                tags[r["station_id"]] = frozenset(r["tags"].split())
    return TourInputs({k: (tuple(sorted(v["egress"])), tuple(sorted(v["access"]))) for k, v in sorted(ends.items())},
                      {k: (names.get(k, k), frozenset(lines.get(k, ()))) for k in sorted(names)},
                      {k: tags[k] for k in sorted(tags) if k in names})


def default_inputs(net) -> TourInputs:
    """station_stops.csv를 주지 않았을 때: 지정 정류장만, 호선 정보 없음."""
    ends = {k: ((st.egress_stop,) if st.egress_stop else (), (st.access_stop,) if st.access_stop else ())
            for k, st in sorted(net.stations.items())}
    return TourInputs(ends, {})


def end_pair(g: Graph, egress: tuple[str, ...], access: tuple[str, ...], closure_max_s: int):
    """(E, A, 고리 닫기 경로 A … E) 또는 None."""
    known = g.tt.stop_ids()
    best = None
    for a in sorted(set(access)):
        for e in sorted(set(egress)):
            if a == e or a not in known or e not in known:
                continue
            p = g.path(a, e)
            if p is None:
                continue
            t = closure_s(g, p)
            if t > closure_max_s or g.has_uturn(p):
                continue
            key = (t, a, e)
            if best is None or key < best[0]:
                best = (key, e, a, p)
    return None if best is None else (best[1], best[2], best[3])


class TourBuilder:
    def __init__(self, builder, policy, inputs: TourInputs, closure_max_s: int):
        self.b = builder
        self.g: Graph = builder.g
        self.net = builder.net
        self.extra_s = policy.turnaround_s + policy.cycle_rest_s
        self.dz = designated_stops(self.net)
        self.inputs = inputs
        self.closure_max_s = closure_max_s
        self._ends: dict[str, tuple | None] = {}
        self.reasons: dict[tuple[str, str], int] = {}
        by_station: dict[str, dict[str, dict[str, float]]] = {}
        for (kind, s, k), v in builder.demand.pair.items():
            m = by_station.setdefault(k, {"access": {}, "egress": {}})
            m[kind][s] = m[kind].get(s, 0.0) + sum(v)
        self.by_station = by_station

    def count(self, stage: str, reason: str) -> None:
        k = (stage, reason)
        self.reasons[k] = self.reasons.get(k, 0) + 1

    def ends(self, k: str):
        if k not in self._ends:
            e, a = self.inputs.ends.get(k, ((), ()))
            self._ends[k] = end_pair(self.g, e, a, self.closure_max_s)
        return self._ends[k]

    def value(self, k: str, kind: str, s: str) -> float:
        return self.by_station.get(k, {}).get(kind, {}).get(s, 0.0)

    # ---------------------------------------------------------------- 제약
    def closure(self, stops: tuple[str, ...]):
        return self.g.path(stops[-1], stops[0])

    def cycle_s(self, stops: tuple[str, ...]) -> int:
        clo = self.closure(stops)
        return self.g.oneway_s(stops) + (closure_s(self.g, clo) if clo else 10 ** 9) + self.extra_s

    def far_index(self, stops: tuple[str, ...]) -> int:
        """E에서 망 거리로 가장 먼 정류장 순번(동률 앞쪽). 기록·우회율용."""
        if len(stops) <= 2:
            return len(stops) - 1
        d = self.g.dist_from(stops[0])
        return max(range(1, len(stops) - 1), key=lambda i: (d.get(stops[i], -1), -i))

    def detours(self, stops: tuple[str, ...]) -> tuple[float, float]:
        i = self.far_index(stops)
        out, ret = stops[:i + 1], stops[i:]
        return (self.g.detour(out) if len(out) > 1 else 1.0), (self.g.detour(ret) if len(ret) > 1 else 1.0)

    def violations(self, stops: tuple[str, ...], lim: TourLimits) -> list[str]:
        bad = []
        if len(set(stops)) != len(stops):
            bad.append("repeat")
        clo = self.closure(stops)
        if clo is None:
            return bad + ["closure"]
        cs = closure_s(self.g, clo)
        if cs > lim.closure_max_s:
            bad.append("closure")
        if self.g.oneway_s(stops) + cs + self.extra_s > lim.cycle_max_s:
            bad.append("cycle")
        if lim.max_stops is not None and sum(1 for s in stops if s not in self.dz) > lim.max_stops:
            bad.append("max_stops")
        if self.g.has_uturn(cyclic_seq(stops, clo)):
            bad.append("uturn")
        if lim.detour is not None and max(self.detours(stops)) > lim.detour:
            bad.append("detour")
        return bad

    def route(self, anchors: list[str], lim: TourLimits, stage: str | None) -> tuple[str, ...] | None:
        stops = self.g.expand(anchors)
        if stops is None:
            if stage:
                self.count(stage, "no_path_or_repeat")
            return None
        bad = self.violations(stops, lim)
        if stage:
            for r in bad or ["ok"]:
                self.count(stage, r)
        return None if bad else stops

    # ---------------------------------------------------------------- 삽입·2-opt(한 바퀴 기준)
    def insertion(self, anchors: list[str], pool: list[str], lim: TourLimits, stage: str,
                  fixed: frozenset[str]) -> tuple[str, ...] | None:
        """fixed = 움직이지 않는 앵커(양 끝·역 정류장). 붙어 있는 두 fixed 앵커 사이(Q.A → Q.E)에는 넣지 않는다."""
        stops = self.route(anchors, lim, stage)
        if stops is None:
            return None
        cover = self.b.cover_of
        cur_dem, cur_t = cover(stops), self.cycle_s(stops)
        while True:
            on = set(stops)
            best = None
            for s in pool:
                if s in on:
                    continue
                for pos in range(1, len(anchors)):
                    if anchors[pos - 1] in fixed and anchors[pos] in fixed and pos - 1 > 0 and pos < len(anchors) - 1:
                        continue
                    trial = anchors[:pos] + [s] + anchors[pos:]
                    new = self.route(trial, lim, None)
                    if new is None:
                        continue
                    d_dem = cover(new) - cur_dem
                    if d_dem <= 1e-9:
                        continue
                    d_t = self.cycle_s(new) - cur_t
                    score = math.inf if d_t <= 0 else d_dem / d_t
                    key = (score, d_dem, -pos, s)
                    if best is None or key > best[0]:
                        best = (key, trial, new)
            if best is None:
                break
            _, anchors, stops = best
            cur_dem, cur_t = cover(stops), self.cycle_s(stops)
        return self.two_opt(anchors, stops, lim, fixed)

    def two_opt(self, anchors: list[str], stops: tuple[str, ...], lim: TourLimits, fixed: frozenset[str]):
        """fixed 앵커로 나뉜 구간마다 움직일 수 있는 앵커 구간을 뒤집어 본다(한 번 훑기)."""
        cover = self.b.cover_of
        cur_t, cur_dem = self.cycle_s(stops), cover(stops)
        runs, run = [], []
        for i, a in enumerate(anchors):
            if a in fixed:
                if run:
                    runs.append(run)
                run = []
            else:
                run.append(i)
        if run:
            runs.append(run)
        for mv in runs:
            for x in range(len(mv)):
                for y in range(x + 1, len(mv)):
                    i, j = mv[x], mv[y]
                    trial = anchors[:i] + anchors[i:j + 1][::-1] + anchors[j + 1:]
                    new = self.route(trial, lim, None)
                    if new is None:
                        continue
                    t, dem = self.cycle_s(new), cover(new)
                    if t < cur_t and dem >= cur_dem - 1e-9:
                        anchors, stops, cur_t, cur_dem = trial, new, t, dem
        return stops

    # ---------------------------------------------------------------- ②-T
    def feeder(self, k: str, seed: str, lim: TourLimits, stage: str) -> tuple[str, ...] | None:
        ea = self.ends(k)
        if ea is None:
            return None
        e, a, _ = ea
        out = self.g.path_continue(None, e, seed, frozenset({a}))
        if out is None:
            self.count(stage, "no_path_or_repeat")
            return None
        back = self.g.path_continue(out[-2] if len(out) > 1 else None, seed, a, frozenset(out[:-1]))
        if back is None:
            self.count(stage, "no_path_or_repeat")
            return None
        stops = out + back[1:]
        bad = self.violations(stops, lim)
        for r in bad or ["ok"]:
            self.count(stage, r)
        return None if bad else stops
