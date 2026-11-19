"""순환 후보(설계 6.1b, 3b단계). 셔틀은 무조건 순환 운행한다. "편도 운행 + 공차 복귀"는 없다.

한 바퀴 = 가는 길(지금의 편도 후보 정차열) + 돌아오는 길 + 고리 닫기 공차(마지막 정류장 → 첫 정류장).
변환기 S2(같은 정류장 두 번 금지) 때문에 한 바퀴의 정류장을 모두 영업으로 나열하고, 마지막 → 첫 정류장 짧은 공차로
고리를 닫는다(지자체안 전사본과 같은 표현). 길 양쪽 정류장은 ID가 다르므로 돌아오는 길은 가는 길과 겹치지 않는다.

돌아오는 길 만들기(`mode`)
- "demand"(기본): 가는 길과 같은 방식(최선 삽입)으로 수요에 맞춰 만든다.
  기준 경로 = 꺾는 정류장(가는 길 끝) → 고정 정류장들 → 첫 정류장(가상 끝, 영업열에서는 뺀다)을 U턴 없이 이은 길.
  여기에 수요 정류장(노선 역들과 수요가 있고 역에서 망 거리 R 안)을 하나씩 넣는다.
  점수 = 한 바퀴 덮는 수요 증가 ÷ 돌아오는 길 시간 증가(가는 길 삽입과 같은 식).
  고정 정류장(패턴의 반대 방향): access = 없음(꺾는 정류장이 역 주변 정류장이라 역 → 동네 수요가 바로 들어간다),
  egress = 그 역 access 지정 정류장(동네 → 역 수요), connect(i→j) = 역 i access 지정 정류장,
  multi = 경유 역 egress 지정 정류장을 반대 순서로(변형 "rev") 또는 고정 없이(변형 "direct").
- "deadhead"(비교 기준): 지금의 공차 복귀 경로(변환기 규칙의 최단 경로)의 정류장을 모두 영업으로 바꾼다.

한 바퀴 제약(`LoopLimits`)
- 방향마다(가는 길·돌아오는 길 각각) 편도 한도 `l_max_s`(그 편도 후보를 만든 L_max, 여러 역 경유는 multi_l_max_min)·
  정차 상한 `max_stops`(역 지정 정류장 제외, 기본 12)·우회율 `max_detour`(돌아오는 길은 꺾는 정류장 → 첫 정류장, 고리 닫기 포함).
- 고리 닫기 공차 ≤ `closure_max_s`(기본 300초). 시간 = 공차 경로 구간의 06~09시 최댓값 합(정차 없음).
  최종 판정은 변환기가 실제로 고르는 공차 경로(`cycle.route_cycle`의 return_stops)로 한다.
- U턴 금지는 고리를 닫는 이음(마지막 → 공차 → 첫 정류장 → 둘째 정류장)을 포함한 모든 이음에 건다(`Graph.is_uturn`).
  제자리 U턴은 막고 블록을 도는 것은 허용한다(`Graph.path_continue`).

덮는 수요는 영업열 순서를 따른다(o가 d보다 앞). 시뮬레이터는 승객을 한 영업 leg 안에서만 태우므로(엔진 alight_index),
고리를 넘어가는 OD(다음 바퀴에 내림)는 세지 않는다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .graph import Graph

LOOP_MODES = ("demand", "deadhead")


@dataclass(frozen=True)
class LoopLimits:
    l_max_s: int
    max_stops: int | None = 12
    max_detour: float = 1.6
    closure_max_s: int = 300


def designated_stops(net) -> frozenset[str]:
    """역 지정 정류장(access·egress, 모든 역). 정차 수에서 뺀다."""
    return frozenset(x for st in net.stations.values() for x in (st.access_stop, st.egress_stop) if x)


def cyclic_seq(stops: tuple[str, ...], closure: tuple[str, ...]) -> tuple[str, ...]:
    """U턴 검사용 한 바퀴 열: 영업 정류장 + 공차 중간 정류장 + 첫 정류장 + 둘째 정류장.

    closure = 마지막 정류장 → 첫 정류장 공차 경로(양 끝 포함).
    """
    tail = (stops[1],) if len(stops) > 1 else ()
    return tuple(stops) + tuple(closure[1:]) + tail


def closure_s(g: Graph, closure: tuple[str, ...]) -> int:
    """고리 닫기 공차 시간(초): 구간 06~09시 최댓값 합. 정차는 없다."""
    return sum(g.weight(a, b) for a, b in zip(closure, closure[1:]))


def leg_violations(g: Graph, leg: tuple[str, ...], end: str, station_stops: frozenset[str],
                   lim: LoopLimits) -> list[str]:
    """돌아오는 길 영업열 leg(꺾는 정류장 → 마지막 영업 정류장)과 첫 정류장 end로 닫는 이음의 위반."""
    bad = []
    if len(leg) > 1 and g.oneway_s(leg) > lim.l_max_s:
        bad.append("l_max")
    if lim.max_stops is not None and sum(1 for s in leg[1:] if s not in station_stops) > lim.max_stops:
        bad.append("max_stops")
    full = leg + (end,)
    if len(full) > 2 and g.detour(full) > lim.max_detour:
        bad.append("detour")
    if (leg[-1], end) in g._w and g.weight(leg[-1], end) > lim.closure_max_s:
        bad.append("closure")
    return bad


class LoopBuilder:
    """편도 후보(가는 길)마다 돌아오는 길을 붙여 한 바퀴 정차열을 만든다."""

    def __init__(self, builder, radius_m: float):
        self.b = builder
        self.g: Graph = builder.g
        self.net = builder.net
        self.st_stops = designated_stops(self.net)
        self.radius_m = radius_m
        self._pc: dict[tuple, tuple[str, ...] | None] = {}
        self._reach: dict[tuple[str, str], float] = {}
        by_station: dict[str, dict[str, float]] = {}
        for (_, s, k), v in builder.demand.pair.items():
            m = by_station.setdefault(k, {})
            m[s] = m.get(s, 0.0) + sum(v)
        self.by_station = by_station
        self.reasons: dict[tuple[str, str, str], int] = {}

    def _count(self, mode: str, kind: str, reason: str) -> None:
        k = (mode, kind, reason)
        self.reasons[k] = self.reasons.get(k, 0) + 1

    # ---------------------------------------------------------------- 경로
    def _hop_ok(self, prev: str | None, p: tuple[str, ...], then: str | None) -> bool:
        seq = ((prev,) if prev else ()) + p + ((then,) if then else ())
        return not any(self.g.is_uturn(a, b, c) for a, b, c in zip(seq, seq[1:], seq[2:]))

    def _continue(self, prev: str | None, a: str, b: str, then: str | None, avoid: frozenset[str]
                  ) -> tuple[str, ...] | None:
        key = (prev, a, b, then, avoid)
        if key not in self._pc:
            self._pc[key] = self.g.path_continue(prev, a, b, avoid, then)
        return self._pc[key]

    def expand_return(self, S: tuple[str, ...], anchors: list[str]) -> tuple[str, ...] | None:
        """anchors = [꺾는 정류장 S[-1], 고정·삽입 정류장…, 첫 정류장 S[0]] → 돌아오는 길 영업열(꺾는 정류장 포함,
        첫 정류장 뺌). 구간마다 망 최단경로를 쓰고, 이음에 U턴이 생기거나 가는 길·앞 구간 정류장을 지나면
        U턴 없이 그 정류장들을 피하는 최단경로(path_continue)로 바꾼다. 그래도 없으면 None."""
        used = set(S)
        out = [anchors[0]]
        prev = S[-2] if len(S) > 1 else None
        last = len(anchors) - 2
        for i, (a, b) in enumerate(zip(anchors, anchors[1:])):
            final = i == last
            if not final and b in used:
                return None
            then = S[1] if (final and len(S) > 1) else None
            p = self.g.path(a, b)
            if p is None or not self._hop_ok(prev, p, then) or any(x in used for x in (p[1:-1] if final else p[1:])):
                p = self._continue(prev, a, b, then, frozenset(used - {a, b}))
                if p is None:
                    return None
            inner = p[1:-1] if final else p[1:]
            if any(x in used for x in inner) or len(set(inner)) != len(inner):
                return None
            used.update(inner)
            out.extend(inner)
            prev = p[-2]
        return tuple(out)

    # ---------------------------------------------------------------- 수요 방식
    def _station_reach(self, k: str, s: str) -> float:
        key = (k, s)
        if key not in self._reach:
            from .generate import station_reach
            self._reach[key] = station_reach(self.g, self.net, k, s)
        return self._reach[key]

    def return_pool(self, hubs: tuple[str, ...], S: tuple[str, ...]) -> list[str]:
        """돌아오는 길에 넣을 수 있는 정류장: 노선 역들과 수요가 있고 그 역에서 망 거리 R 안(가는 길 정류장 제외)."""
        on = set(S)
        val: dict[str, float] = {}
        for k in hubs:
            if k not in self.net.stations:
                continue
            for s, v in self.by_station.get(k, {}).items():
                if v > 0 and s not in on and s in self.net.stops and self._station_reach(k, s) <= self.radius_m:
                    val[s] = val.get(s, 0.0) + v
        return sorted(val, key=lambda s: (-val[s], s))

    def fixed_anchors(self, kind: str, hubs: tuple[str, ...], S: tuple[str, ...], variant: str) -> list[str]:
        st = self.net.stations
        out: list[str] = []
        if kind == "egress":
            a = st[hubs[0]].access_stop
            out = [a] if a else []
        elif kind == "connect":
            a = st[hubs[0]].access_stop
            out = [a] if a else []
        elif kind == "multi" and variant == "rev":
            for k in reversed(hubs[:-1]):
                e = st[k].egress_stop if k in st else None
                if e:
                    out.append(e)
        on = set(S)
        return [x for x in dict.fromkeys(out) if x not in on]

    def full_ok(self, S: tuple[str, ...], ret: tuple[str, ...], lim: LoopLimits) -> list[str]:
        bad = leg_violations(self.g, ret, S[0], self.st_stops, lim)
        if self.g.has_uturn(S + ret[1:] + (S[0],) + ((S[1],) if len(S) > 1 else ())):
            bad.append("uturn")
        return bad

    def demand_return(self, kind: str, hubs: tuple[str, ...], S: tuple[str, ...], lim: LoopLimits,
                      variant: str = "") -> tuple[str, ...] | None:
        """수요 방식 돌아오는 길(꺾는 정류장 포함, 첫 정류장 뺌). 기준 경로가 제약을 못 지키면 None."""
        mode = "demand" + (f"_{variant}" if variant else "")
        anchors = [S[-1], *self.fixed_anchors(kind, hubs, S, variant), S[0]]
        ret = self.expand_return(S, anchors)
        if ret is None:
            self._count(mode, kind, "base_no_path_or_repeat")
            return None
        bad = self.full_ok(S, ret, lim)
        if bad:
            for r in bad:
                self._count(mode, kind, "base_" + r)
            return None
        cover = self.b.cover_of
        cur_dem = cover(S + ret[1:])
        cur_t = self.g.oneway_s(ret) if len(ret) > 1 else 0
        pool = self.return_pool(hubs, S)
        while True:
            on = set(S) | set(ret)
            best = None
            for s in pool:
                if s in on:
                    continue
                for pos in range(1, len(anchors)):
                    trial = anchors[:pos] + [s] + anchors[pos:]
                    new = self.expand_return(S, trial)
                    if new is None or self.full_ok(S, new, lim):
                        continue
                    d_dem = cover(S + new[1:]) - cur_dem
                    if d_dem <= 1e-9:
                        continue
                    d_t = self.g.oneway_s(new) - cur_t
                    score = math.inf if d_t <= 0 else d_dem / d_t
                    key = (score, d_dem, -pos, s)
                    if best is None or key > best[0]:
                        best = (key, trial, new)
            if best is None:
                break
            _, anchors, ret = best
            cur_dem = cover(S + ret[1:])
            cur_t = self.g.oneway_s(ret)
        self._count(mode, kind, "ok")
        return ret

    # ---------------------------------------------------------------- 비교 기준 방식
    def deadhead_return(self, kind: str, S: tuple[str, ...], back: tuple[str, ...], lim: LoopLimits
                        ) -> tuple[str, ...] | None:
        """공차 복귀 경로 back(S[-1] → S[0])의 중간 정류장을 영업으로. 가는 길과 겹치거나 제약을 어기면 None."""
        ret = tuple(back[:-1])
        if set(ret[1:]) & set(S) or len(set(ret)) != len(ret):
            self._count("deadhead", kind, "repeat")
            return None
        bad = self.full_ok(S, ret, lim)
        if bad:
            for r in bad:
                self._count("deadhead", kind, r)
            return None
        self._count("deadhead", kind, "ok")
        return ret
