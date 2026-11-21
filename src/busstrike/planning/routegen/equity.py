"""형평 지표 "탈 수 있는 수요 비율"(설계 2.2).

    탈 수 있는 수요 비율 = (평가 규칙 `reach.Reach`로 자기 역(규칙이 허용하는 대체 역 포함)까지 셔틀을 타고 갈 수 있는 수요)
                         ÷ (그 구 동네 정류장 수요)

- 수요: 배분기와 같은 셔틀 수요(정밀 흡수 뒤, 06~10시 합). OD 키(출발, 도착)마다 하루 수요.
- 동네 끝: OD 키에서 역 지정 정류장이 아닌 쪽(`home_end`, `walk.stop_weights`와 같은 규칙). 두 끝이 모두 역 지정 정류장이면
  출발 쪽. 이 규칙은 평가 규칙과 무관하게 고정이라 분모가 조합마다 같다.
- 그 구 동네 정류장 수요(분모): 동네 끝이 그 구 정류장인 키의 수요 합. 분자도 같은 키 중에서만 센다.
- 두 값
  - total: 위 정의 그대로.
  - far(역 800m 밖): 동네 끝 정류장에서 도로 도보 `far_m`(기본 800m) 안에 역 지정 정류장이 하나도 없는 정류장만으로 분모·분자를
    센다. 도보 거리는 새 망 도보 이웃(`walknet`, 정류장 투영점에서 도로를 따라)이다. 지하철까지 걸어가기 어려운 동네다.
- 시간대: 06~10시 전체. 운행 시작 시각과 무관한 공간 지표다(같은 시작 시각으로 비교하므로 두 안에 같게 작용).
- 분자는 `Reach.rideable`(설계 덮는 키 = 평가 재배정으로 탈 수 있는 승객)과 같다. 그래서 9개 조합 어디서나 계산된다.
- 이 지표는 "범위(400m 안 수요)"와 달리 방향·목적지를 본다: 셔틀 정류장이 가까워도 그 노선이 그 사람의 역(규칙상)으로 가지 않으면
  세지 않는다(검증 01 3절 3번, 지자체 r03: 넓게 덮지만 하루 4명).
"""

from __future__ import annotations

from typing import Iterable

Key = tuple[str, str]


def home_end(key: Key, station_stops: set[str]) -> str:
    """OD 키의 동네 끝 정류장(역 지정 정류장이 아닌 쪽, 둘 다 역이면 출발 쪽)."""
    o, d = key
    return d if (o in station_stops and d not in station_stops) else o


def far_stops(home_stops: Iterable[str], nb_far: dict[str, dict[str, float]], station_stops: set[str],
              far_m: float = 800.0) -> set[str]:
    """도로 도보 far_m 안에 역 지정 정류장이 없는 정류장(자기 자신이 지정 정류장이면 가깝다)."""
    out = set()
    for s in home_stops:
        near = nb_far.get(s, {s: 0.0})
        if s in station_stops or any(x in station_stops and w <= far_m + 1e-9 for x, w in near.items()):
            continue
        out.add(s)
    return out


class EquityIndex:
    """구 하나의 분모(동네 정류장 수요 전체·역 800m 밖)와 키별 무게."""

    def __init__(self, od_total: dict[Key, float], station_stops: set[str], home_stops: set[str],
                 far: set[str], far_m: float = 800.0):
        self.station_stops = set(station_stops)
        self.far_m = far_m
        self.home_of = {k: home_end(k, self.station_stops) for k in od_total}
        self.w_total = {k: v for k, v in od_total.items() if self.home_of[k] in home_stops and v > 0}
        self.w_far = {k: v for k, v in self.w_total.items() if self.home_of[k] in far}
        self.den_total = sum(self.w_total.values())
        self.den_far = sum(self.w_far.values())
        self.n_home = len(home_stops)
        self.n_far = len(far)

    @classmethod
    def build(cls, od_total: dict[Key, float], station_stops: set[str], home_stops: set[str],
              nb_far: dict[str, dict[str, float]], far_m: float = 800.0) -> "EquityIndex":
        return cls(od_total, station_stops, home_stops, far_stops(home_stops, nb_far, station_stops, far_m), far_m)

    def ratios(self, keys: Iterable[Key]) -> dict:
        """탈 수 있는 OD 키 집합 → {total, far, rideable, rideable_far, den_total, den_far}."""
        ks = set(keys)
        rt = sum(v for k, v in self.w_total.items() if k in ks)
        rf = sum(v for k, v in self.w_far.items() if k in ks)
        return {"total": rt / self.den_total if self.den_total else 0.0,
                "far": rf / self.den_far if self.den_far else 0.0,
                "rideable": round(rt, 1), "rideable_far": round(rf, 1),
                "den_total": round(self.den_total, 1), "den_far": round(self.den_far, 1)}

    def of_plan(self, reach, routes: Iterable) -> dict:
        """안(정차열들)의 두 값. reach = `reach.Reach`(그 조합의 규칙)."""
        ks: set = set()
        for seq in routes:
            ks |= set(reach.cover_keys(seq))
        return self.ratios(ks)

    def record(self) -> dict:
        return {"far_m": self.far_m, "den_total": round(self.den_total, 1), "den_far": round(self.den_far, 1),
                "home_stops": self.n_home, "far_stops": self.n_far,
                "far_share": round(self.den_far / self.den_total, 4) if self.den_total else 0.0}


def legacy_keys(routes: Iterable, od_keys, nb: dict) -> set:
    """예전 규칙(역 고정, 역 쪽도 지정 정류장 R m 안 걷기) 덮는 키(`walk.walk_cover_keys`, 참고 구현 misc2 재현용)."""
    from .walk import walk_cover_keys

    ks: set = set()
    keys = list(od_keys)
    for seq in routes:
        ks |= set(walk_cover_keys(list(seq), keys, nb))
    return ks
