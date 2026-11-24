"""배차 예산 안의 역-종점 순회(새 알고리즘, New_Algol.md 3.3, 작업 2026-11-25). `generate._tours`가 옵션일 때만 쓴다.

- 1단계 배차 예산: 노선 대수 n마다 순환 상한 n × H(`Params.budget_h_s`). 후보에 n(`Candidate.vehicles`)을 남긴다.
- 2단계 호선 단위 묶기(`Params.line_group`): 수요를 평가 규칙과 같은 꼬리표(`station_lines.csv`의 tags)로 모은다.
  끝 역 = 씨앗에서 망 거리로 가장 가까운 그 꼬리표 역(끝점 쌍이 있는 역). 동률은 역 ID.
- 3단계 골격: ②-T 피더 순회(역 E → 씨앗 → 역 A, U턴 없는 최단경로).
- 4단계 소외 우선 삽입(`Params.fill`): 골격에서 순환이 n × H에 닿을 때까지 정류장을 끼운다.
  점수 = (늘어나는 탑승 근사 + β × 늘어나는 소외 수요) ÷ 늘어나는 순환 시간(근사). β = `fill_bonus`(0이면 가산점 끔).
  - 탑승 근사(`RideApprox`): 노선 하나, 대수 n, 순환 C일 때 시간대별(운행 시작 뒤 몫) 구간 정원 근사.
    배분기 `allocate.Model`의 segment 방식과 같은 식이다(좌석 재사용, 겹치는 다른 노선은 없다고 본다).
    배차 = C/n ≤ H ≤ 대기 한도이므로 대기 항 min(1, W·n/C)은 1이다.
  - 소외 수요: 덮는 OD 키 중 동네 끝 정류장이 소외 위험 집합(`far_stops`: 역 지정 정류장 도로 도보 800m 밖)인 수요.
    덮는다는 것은 평가 규칙(동네 쪽 400m 도보)으로 탈 수 있게 된다는 뜻이라, 새로 덮이는 소외 수요는 그 노선 정류장 400m 밖에 있던
    정류장의 수요다.
  - 정류장 사이: U턴 금지면 `Graph.path_continue`(직전 정류장을 보고 U턴 없이), U턴 허용이면 망 최단경로(`Graph.expand`).
- 5단계 역 쌍 병합(`Params.merge`): 끝 역이 다른 두 순회 t₁(E₁…A₁), t₂(E₂…A₂)에서 A₁ → E₂와 A₂ → E₁이 고리 닫기 상한 안이면
  t₁ + (A₁ → E₂ 사이 정류장) + t₂를 만들고, 순환 ≤ (n₁ + n₂) × H이고 근사(n₁ + n₂)가 두 근사의 합보다 클 때만 후보로 남긴다.
"""

from __future__ import annotations

import math

from .equity import home_end
from .tour import TourLimits

EPS = 1e-9


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


class BudgetTours:
    """TourBuilder(tour.py) 위에서 배차 예산 순회를 만든다(4·5단계)."""

    def __init__(self, tb, p, approx: RideApprox):
        self.tb, self.g, self.p, self.ap = tb, tb.g, p, approx
        self.beta = float(p.fill_bonus)
        self._legs: dict[tuple, tuple | None] = {}

    def lim(self, n: int) -> TourLimits:
        p = self.p
        return TourLimits(n * p.budget_h_s, p.tour_max_stops, p.tour_detour, p.loop_closure_max_s)

    def connect(self, anchors: list[str]) -> tuple[str, ...] | None:
        """앵커를 잇는 정차열. U턴 허용이면 망 최단경로, 금지면 앵커마다 직전 정류장을 보고 U턴 없는 최단경로(양 끝 역 정류장은
        중간에 지나지 않는다). 같은 정류장이 두 번이면 None."""
        if self.g.allow_uturn:
            return self.g.expand(anchors)
        ends = frozenset({anchors[0], anchors[-1]})
        out = [anchors[0]]
        for a, b in zip(anchors, anchors[1:]):
            prev = out[-2] if len(out) > 1 else None
            key = (prev, a, b, ends)
            if key not in self._legs:
                self._legs[key] = self.g.path_continue(prev, a, b, ends - {a, b})
            leg = self._legs[key]
            if leg is None:
                return None
            out.extend(leg[1:])
        if len(set(out)) != len(out):
            return None
        return tuple(out)

    def value(self, stops: tuple[str, ...], n: int) -> tuple[int, float, float]:
        """(순환 근사, 탑승 근사, 삽입 점수의 값 = 탑승 근사 + β × 소외 수요)."""
        cyc = self.tb.cycle_s(stops)
        r, f = self.ap.evaluate(stops, n, cyc)
        return cyc, r, r + self.beta * f

    def fill(self, anchors: list[str], n: int, pool: list[str], stage: str) -> tuple[str, ...] | None:
        """골격 앵커(역 E, 씨앗, 역 A)에서 n × H까지 삽입. 동률은 (점수, 값 증가, 앞자리, 정류장 ID)."""
        lim = self.lim(n)
        stops = self.connect(anchors)
        if stops is None:
            self.tb.count(stage, "no_path_or_repeat")
            return None
        bad = self.tb.violations(stops, lim)
        for r in bad or ["ok"]:
            self.tb.count(stage, r)
        if bad:
            return None
        cur_t, _, cur_v = self.value(stops, n)
        anchors = list(anchors)
        while True:
            on = set(stops)
            best = None
            for s in pool:
                if s in on:
                    continue
                for pos in range(1, len(anchors)):
                    trial = anchors[:pos] + [s] + anchors[pos:]
                    new = self.connect(trial)
                    if new is None or self.tb.violations(new, lim):
                        continue
                    t, _, v = self.value(new, n)
                    dv = v - cur_v
                    if dv <= EPS:
                        continue
                    dt = t - cur_t
                    score = math.inf if dt <= 0 else dv / dt
                    key = (score, dv, -pos, s)
                    if best is None or key > best[0]:
                        best = (key, trial, new, t, v)
            if best is None:
                break
            _, anchors, stops, cur_t, cur_v = best
        return stops
