"""Model._route 속도 수정(열린 겹침 노선만 미리 추림)이 결과를 비트 단위로 바꾸지 않는지: 예전 식을 그대로 옮긴 기준과 == 비교."""
import random

from busstrike.planning.routegen.allocate import EPS, AllocParams, Model


def _reference(m: Model, r: str, n: dict) -> tuple:
    W, seat, kappa = m.p.wait_s, m.p.seat, m.p.kappa
    fr = n[r] / m.cycle[r]
    cap = fr * 3600 * seat
    loads = []
    for ki in m.cover[r]:
        fk = fr
        for x in m.overlap[r]:
            nx = n.get(x, 0)
            if nx > 0 and ki in frozenset(m.cover[x]):
                fk += nx / m.cycle[x]
        share = kappa * min(1.0, W * fk) * fr / fk
        loads.append([m.d[ki][h] * share for h in range(4)])
    raw = [sum(x[h] for x in loads) for h in range(4)]
    sp = m.spans[r] if m.p.cap_mode == "segment" else None
    if sp is None or not sp:
        kept = [min(x, cap) for x in raw]
    else:
        S = max(b for _, b in sp)
        kept = [0.0] * 4
        for h in range(4):
            diff = [0.0] * (S + 1)
            for (a, b), x in zip(sp, loads):
                diff[a] += x[h]
                diff[b] -= x[h]
            g, acc = [], 0.0
            for s in range(S):
                acc += diff[s]
                g.append(1.0 if acc <= cap + EPS else cap / acc)
            kept[h] = sum(x[h] * min(g[a:b]) for (a, b), x in zip(sp, loads))
    return kept, sum(raw) - sum(kept)


def _model(seed: int, cap_mode: str) -> Model:
    rng = random.Random(seed)
    keys = [(f"o{i}", f"d{j}") for i in range(12) for j in range(6)]
    od = {k: [rng.uniform(0, 40) for _ in range(4)] for k in keys}
    routes = []
    for i in range(25):
        ks = rng.sample(keys, rng.randint(3, 20))
        spans = []
        for _ in ks:
            a = rng.randint(0, 8)
            spans.append((a, a + rng.randint(1, 6)))
        routes.append((f"r{i:02d}", rng.randint(900, 4000), ks, spans))
    return Model(routes, od, AllocParams(cap_mode=cap_mode, seat=rng.choice([20, 45])))


def test_route_matches_reference_bitwise():
    for seed in range(8):
        for mode in ("segment", "route"):
            m = _model(seed, mode)
            rng = random.Random(100 + seed)
            for _ in range(30):
                n = {r: rng.randint(1, 4) for r in rng.sample(m.ids, rng.randint(1, 12))}
                for r in n:
                    m._rcache.clear()
                    assert m._route(r, n) == _reference(m, r, n)
