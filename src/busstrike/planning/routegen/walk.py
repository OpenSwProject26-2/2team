"""걸어서 닿는 셔틀 정류장(도보 R m 안)을 쓰는 계산: 덮는 범위, 걷기 반영 OD 덮기, 형평 먼저 노선 고르기, 수요 재배정.

도보 이웃 `nb`는 {정류장: {걸어서 닿는 정류장: 도보 거리 m}}이고 자기 자신(0m)을 포함한다. 도로를 따라 잰 거리여야 한다
(`experiments/routegen_lab/walknet.py`). 이 모듈은 거리를 재지 않고 받은 이웃만 쓴다.

- 덮는 범위: 셔틀 정류장 R m 안에 드는 동네 정류장 수요 ÷ 전체.
- 걷기 반영 OD 덮기: 승객 정류장 o에서 걸어서 닿는 셔틀 정류장 s가 있고, 같은 노선에서 s 뒤에 목적지(역 지정 정류장) d에서
  걸어서 닿는 정류장 t가 있으면 덮는다(egress는 역 쪽이 앞). 걷지 않는 경우(o, d가 노선에 그대로 있음)도 포함한다.
- 형평 먼저: 덮는 범위가 목표에 닿을 때까지 "(새로 덮는 수요 + λ × 새로 탈 수 있게 되는 수요) ÷ 최소 대수"가 큰 노선부터
  최소 대수(n0)로 연다. 덮는 수요 = 걸어서 닿는 동네 정류장의 수요(그 노선이 그 사람의 역으로 가는지는 따지지 않음),
  탈 수 있는 수요 = 걷기 반영 OD 덮기로 그 노선을 타고 가려는 역까지 갈 수 있는 수요. λ = 0이면 범위만 본다.
- 수요 재배정(시뮬레이터 입력): 직접 탈 수 없는 승객을 걷는 거리 합이 가장 짧은 (s, t)로 옮긴다. 못 옮기면 그대로 둔다.
  도보 시간은 넣지 않는다(시간대 묶음 수요라 분 단위 이동을 표현하지 않음).

2단계(2026-11-19)부터 평가 규칙(목적 역 대체 · 역 도착 = 역 주변 정류장 목록 · 동네 쪽 걷기 · 도보 시간)은 `reach.py`가 원본이다.
이 모듈의 `walk_cover_keys`·`reassign`은 예전 규칙(역 고정, 역 쪽도 지정 정류장 R m 안 걷기)이며 재현용(`--legacy-rule`)으로 남긴다.
"""

from __future__ import annotations

from collections import defaultdict


def covered_stops(stops: set[str], nb: dict[str, dict[str, float]], demand_stops) -> set[str]:
    """셔틀 정류장 집합에서 걸어서 닿는 수요 정류장."""
    return {o for o in demand_stops if any(s in stops for s in nb.get(o, {o: 0.0}))}


def coverage(stops: set[str], nb: dict, weight: dict[str, float]) -> float:
    tot = sum(weight.values())
    return sum(weight[o] for o in covered_stops(stops, nb, weight)) / tot if tot else 0.0


def _pairs(seq: list[str], a_near: dict[str, float], b_near: dict[str, float]):
    """seq에서 a 쪽 정류장 i < b 쪽 정류장 j인 (걷기 합, s, t) 후보."""
    pos = {s: i for i, s in enumerate(seq)}
    A = [(w, s) for s, w in a_near.items() if s in pos]
    B = [(w, t) for t, w in b_near.items() if t in pos]
    for wa, s in A:
        for wb, t in B:
            if pos[s] < pos[t]:
                yield wa + wb, s, t


def walk_cover_keys(seq: list[str], od_keys, nb: dict) -> tuple[tuple[str, str], ...]:
    """걷기를 넣었을 때 이 정차열이 덮는 OD 키(출발, 도착)."""
    pos = set(seq)
    near_cache: dict[str, dict[str, float]] = {}

    def near(x: str) -> dict[str, float]:
        if x not in near_cache:
            near_cache[x] = {s: w for s, w in nb.get(x, {x: 0.0}).items() if s in pos}
        return near_cache[x]
    out = []
    for o, d in od_keys:
        a, b = near(o), near(d)
        if a and b and next(_pairs(seq, a, b), None) is not None:
            out.append((o, d))
    return tuple(sorted(out))


def coverage_first(cands: list[tuple[str, list[str], int]], nb: dict, weight: dict[str, float],
                   target: float, budget: int, ride: dict[str, set] | None = None,
                   od_total: dict | None = None, lam: float = 0.0) -> dict:
    """형평 먼저: 목표 덮는 범위에 닿을 때까지 노선을 최소 대수로 연다.

    cands: (노선 ID, 정차열, 최소 대수 n0). ride: 노선 → 걷기 반영으로 덮는 OD 키 집합, od_total: OD 키 → 하루 수요.
    돌려주는 값: fixed(노선 → 대수), coverage(달성), ride(고른 노선으로 탈 수 있는 수요), steps(고른 순서).
    예산 안에서 목표에 못 닿으면 닿는 데까지만 연다.
    """
    ride = ride or {}
    od_total = od_total or {}
    rideable: set = set()
    tot = sum(weight.values())
    reach = {rid: covered_stops(set(seq), nb, weight) for rid, seq, _ in cands}
    n0 = {rid: k for rid, _, k in cands}
    covered: set[str] = set()
    fixed: dict[str, int] = {}
    steps = []
    used = 0
    while tot and sum(weight[o] for o in covered) / tot < target:
        best = None
        for rid in sorted(reach):
            if rid in fixed or used + n0[rid] > budget:
                continue
            gain = sum(weight[o] for o in reach[rid] - covered)
            if gain <= 0:
                continue
            rg = sum(od_total.get(k, 0.0) for k in ride.get(rid, ()) if k not in rideable) if lam else 0.0
            key = ((gain + lam * rg) / n0[rid], gain, rid)
            if best is None or key > best[0]:
                best = (key, rid)
        if best is None:
            break
        rid = best[1]
        fixed[rid] = n0[rid]
        used += n0[rid]
        covered |= reach[rid]
        rideable |= set(ride.get(rid, ()))
        steps.append({"route": rid, "vehicles": n0[rid], "coverage": round(sum(weight[o] for o in covered) / tot, 4)})
    return {"fixed": fixed, "coverage": sum(weight[o] for o in covered) / tot if tot else 0.0, "steps": steps,
            "vehicles": used, "ride": round(sum(od_total.get(k, 0.0) for k in rideable), 1)}


def reassign(rows: list[dict], routes: list[list[str]], nb: dict, station_stops: set[str]) -> tuple[list[dict], dict]:
    """수요 묶음 행({origin, destination, passenger_count, ...})을 걸어서 탈 수 있는 셔틀 정류장으로 옮긴다.

    access(도착이 역 지정 정류장)·egress(출발이 역 지정 정류장) 모두 양 끝에서 걷기를 허용한다.
    돌려주는 값: (새 행, 요약 {direct, walked, none, walk_m_avg}). 같은 (출발, 도착, 시각) 행은 합친다.
    """
    best: dict[tuple[str, str], tuple[float, str, str] | None] = {}
    stats = {"direct": 0, "walked": 0, "none": 0, "walk_m_sum": 0.0}
    merged: dict[tuple, dict] = {}
    for r in rows:
        o, d, c = r["origin"], r["destination"], int(r["passenger_count"])
        key = (o, d)
        if key not in best:
            cand = None
            for seq in routes:
                for w, s, t in _pairs(seq, nb.get(o, {o: 0.0}), nb.get(d, {d: 0.0})):
                    if cand is None or (w, s, t) < cand:
                        cand = (w, s, t)
            best[key] = cand
        cand = best[key]
        if cand is None:
            stats["none"] += c
            s, t = o, d
        else:
            w, s, t = cand
            if w == 0:
                stats["direct"] += c
            else:
                stats["walked"] += c
                stats["walk_m_sum"] += w * c
        k2 = (s, t, r["bin_start_s"], r["bin_width_s"])
        if k2 in merged:
            merged[k2]["passenger_count"] = str(int(merged[k2]["passenger_count"]) + c)
        else:
            merged[k2] = {**r, "origin": s, "destination": t}
    walked = stats["walked"]
    stats["walk_m_avg"] = round(stats.pop("walk_m_sum") / walked, 1) if walked else 0.0
    return list(merged.values()), stats


def stop_weights(od: dict[tuple[str, str], list[float]], station_stops: set[str]) -> dict[str, float]:
    """동네 정류장별 셔틀 수요(역 쪽이 아닌 끝 기준)."""
    w: dict[str, float] = defaultdict(float)
    for (o, d), hs in od.items():
        s = d if o in station_stops and d not in station_stops else o
        w[s] += sum(hs)
    return dict(w)
