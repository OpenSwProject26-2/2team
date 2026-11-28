"""호선 단위 묶기와 탑승 근사(새 알고리즘 New_Algol.md 3.3에서 남긴 부분, 작업 2026-11-25). `generate._tours`가 옵션일 때만 쓴다.

- 호선 단위 묶기(`Params.line_group`): 수요를 평가 규칙과 같은 꼬리표(`station_lines.csv`의 tags)로 모은다.
  끝 역 = 씨앗에서 망 거리로 가장 가까운 그 꼬리표 역(끝점 쌍이 있는 역). 동률은 역 ID.
- 탑승 근사(`RideApprox`): 노선 하나, 대수 n, 순환 C일 때 시간대별(운행 시작 뒤 몫) 구간 정원 근사.
  배분기 `allocate.Model`의 segment 방식과 같은 식이다(좌석 재사용, 겹치는 다른 노선은 없다고 본다). 소외 우선 삽입이 쓴다.
- 지운 것(docs/design/pipeline.md): 배차 예산(n × H 순환 상한·대수 고정)과 그 위의 삽입기(BudgetTours), 역 쌍 병합.
  소외 우선 삽입은 배차 예산 없이 다시 잇는다(feat/pipeline-core).
"""

from __future__ import annotations

import math

from .equity import home_end

EPS = 1e-9


def line_groups(tags: dict, hubs, stations) -> dict[str, list[str]]:
    """2단계: 꼬리표 → 그 꼬리표 역들(stations 중, ID 순). 허브가 하나라도 가진 꼬리표만."""
    want = sorted({t for h in hubs for t in tags.get(h, ())})
    out = {t: sorted(k for k in stations if t in tags.get(k, ())) for t in want}
    return {t: v for t, v in out.items() if v}


def nearest_end_station(g, group: list[str], ends_of, s: str) -> str | None:
    """씨앗 s에서 망 거리로 가장 가까운 역: min(s → 그 역 A, 그 역 E → s), 동률 역 ID. 못 닿으면 None."""
    best = None
    for k in group:
        ea = ends_of(k)
        if ea is None:
            continue
        e, a, _ = ea
        d = min(g.dist_to(a).get(s, math.inf), g.dist_from(e).get(s, math.inf))
        if d < math.inf and (best is None or (d, k) < best):
            best = (d, k)
    return None if best is None else best[1]


class RideApprox:
    """노선 하나의 탑승 근사와 덮는 소외 수요. builder = generate.Builder(덮는 키는 평가 규칙 reach로 센다)."""

    def __init__(self, builder, hour_w: tuple[float, ...], seat: int, far_stops: frozenset[str] = frozenset(),
                 station_stops: frozenset[str] = frozenset()):
        self.b = builder
        self.hw = tuple(hour_w)
        self.seat = seat
        self.od = builder.demand.od
        self.far_w = {k: sum(x * w for x, w in zip(v, self.hw)) for k, v in self.od.items()
                      if far_stops and home_end(k, set(station_stops)) in far_stops}
        self._memo: dict[tuple, tuple] = {}

    def spans(self, stops: tuple[str, ...]):
        got = self._memo.get(stops)
        if got is None:
            if self.b.reach is not None:
                ks, sp = self.b.reach.spans(stops)
                pairs = [(k, x) for k, x in zip(ks, sp) if k in self.od]
            else:
                from .generate import covered_keys
                pos = {s: i for i, s in enumerate(stops)}
                pairs = [(k, (pos[k[0]], pos[k[1]])) for k in covered_keys(stops, self.od)]
            got = self._memo[stops] = (tuple(k for k, _ in pairs), tuple(x for _, x in pairs))
        return got

    def evaluate(self, stops: tuple[str, ...], n: int, cycle_s: int) -> tuple[float, float]:
        """(탑승 근사, 덮는 소외 수요). 탑승 근사 = Σ_h Σ_k d_k,h · a_h · min_{구간 ∈ [i_k, j_k)} min(1, 좌석 정원_h ÷ 재차_h)."""
        keys, spans = self.spans(stops)
        far = sum(self.far_w.get(k, 0.0) for k in keys)
        if not keys or cycle_s <= 0:
            return 0.0, far
        cap = n * 3600.0 / cycle_s * self.seat
        S = max(b for _, b in spans)
        total = 0.0
        for h, w in enumerate(self.hw):
            if w <= 0:
                continue
            loads = [self.od[k][h] * w for k in keys]
            diff = [0.0] * (S + 1)
            for (a, b), x in zip(spans, loads):
                diff[a] += x
                diff[b] -= x
            g, acc = [], 0.0
            for i in range(S):
                acc += diff[i]
                g.append(1.0 if acc <= cap + EPS else cap / acc)
            total += sum(x * min(g[a:b]) for (a, b), x in zip(spans, loads))
        return total, far
