"""규칙의 상대 기준(설계 15절 설정값 정책) — 그 구(그 파업 시나리오)의 입력에서 규칙 값을 계산한다.

설정은 세 종류다.
- 입력: 대수, 운행 시작 시각, 필수 시설, 수요, 파업 노선. 구마다 다르다(구별 덮어쓰기 허용).
- 규칙: 허브 기준, 허브 개수, 권역 반경, 노선 길이 상한. 절댓값이 아니라 아래 식으로 그 구 입력에서 계산한다.
  식의 상수는 구로에서 지금 절댓값을 재현하도록 한 번 정해 고정하고(설계 9절), 모든 구에 같게 쓴다.
- 정책 상수: 우회율, 대기 한도, 회차, 필수 시설 반경 등. 모든 구 같은 값.

규칙 식(상수 = gu_params.json default.rules, 2026-11-21 구로 보정 `guro_exp_work/routegen_integrated/settings/`)
- 허브 기준 `hub_share`: 역을 셔틀 수요 큰 순으로 더해 전체의 hub_share(0.95)가 될 때까지(`generate.hub_threshold`).
  구로 100%에서 예전 기준(하루 135명)과 같은 24개 역.
- 허브 최소 개수 `hub_min_count` = ⌈가장 적은 대수 ÷ hub_per_vehicles(4)⌉. 구로 11대 → 3(v2 값).
- 권역 반경 `radius_m` = 역까지 셔틀 수요의 직선거리(수요 가중) radius_quantile(0.999) 분위수. 구로 2,990m(예전 3,000m).
- 노선 길이 상한 `l_max_min` = 역까지 도로망 최단 시간(수요 가중) length_quantile(0.5) 분위수 × length_mult(1.531, 2.296),
  분 단위 반올림. 구로 20·30분(예전 값). 여러 역 경유 `multi_l_max_min` = 같은 시간 × multi_length_mult(3.06), 구로 40분.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from .data import Demand, Network
from .graph import Graph


@dataclass(frozen=True)
class RuleConstants:
    hub_share: float = 0.95
    hub_per_vehicles: int = 4
    radius_quantile: float = 0.999
    length_quantile: float = 0.5
    length_mult: tuple[float, float] = (1.531, 2.296)
    multi_length_mult: float = 3.06

    @classmethod
    def from_dict(cls, d: dict | None) -> "RuleConstants":
        d = {k: (tuple(v) if isinstance(v, list) else v) for k, v in (d or {}).items() if not k.startswith("_")}
        return cls(**d)


def weighted_quantile(pairs: list[tuple[float, float]], q: float) -> float | None:
    """(값, 무게) 목록의 q분위수: 값 순으로 무게를 더해 전체의 q 이상이 되는 첫 값."""
    pairs = sorted(p for p in pairs if p[1] > 0)
    if not pairs:
        return None
    tot = sum(w for _, w in pairs)
    acc = 0.0
    for v, w in pairs:
        acc += w
        if acc >= q * tot - 1e-12:
            return v
    return pairs[-1][0]


def demand_scales(demand: Demand, net: Network, g: Graph) -> dict:
    """역까지 셔틀 수요의 직선거리·도로망 시간 (값, 무게) 목록."""
    dist, tim = [], []
    trees: dict[str, dict] = {}
    for (kind, s, k), v in sorted(demand.pair.items()):
        w = sum(v)
        if w <= 0 or s not in net.stops or k not in net.stations:
            continue
        dist.append((net.station_stop_dist_m(k, s), w))
        st = net.stations[k]
        a, b = (s, st.access_stop) if kind == "access" else (st.egress_stop, s)
        if not a or not b:
            continue
        hit = trees.setdefault(a, g.tree(a, capped=False)).get(b)
        if hit:
            tim.append((hit[0], w))
    return {"dist": dist, "time": tim}


def relative_rules(demand: Demand, net: Network, g: Graph, fleets: list[int], c: RuleConstants = RuleConstants()
                   ) -> dict:
    """그 구 입력으로 규칙 값을 계산한다. 돌려주는 값: Params 덮어쓰기 값 + 근거(분위수 값)."""
    sc = demand_scales(demand, net, g)
    R = weighted_quantile(sc["dist"], c.radius_quantile)
    T = weighted_quantile(sc["time"], c.length_quantile)
    out = {"hub_share": c.hub_share,
           "hub_min_count": max(1, math.ceil(min(fleets) / c.hub_per_vehicles)) if fleets else 1}
    if R is not None:
        out["radius_m"] = float(round(R))
    if T is not None:
        out["l_max_min"] = tuple(max(1, round(T * m / 60)) for m in c.length_mult)
        out["multi_l_max_min"] = max(1, round(T * c.multi_length_mult / 60))
    return {"params": out, "basis": {"constants": asdict(c), "radius_quantile_m": R, "time_quantile_s": T,
                                     "n_pairs": len(sc["dist"])}}
