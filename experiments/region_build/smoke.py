# 구 묶음 실행 확인(guro_smoke.py와 같은 방식): 스모크 계획(최대 4노선, 비교안 아님) → 변환기 → M05 → M07 검증
#   python smoke.py <root> [bundle] [fleet]     (bundle 기본: affected_<가운데 날짜>, fleet 기본: 첫 대수)
# 마지막 줄에 `SMOKE OK` 또는 `SMOKE FAIL <이유>`를 찍고, 종료 코드도 0/1.
import sys, os, json, heapq, subprocess, csv, gzip, math
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = os.environ.get('SIMSRC', str(HERE.parents[1]/'src'))
ENV = {**os.environ, 'PYTHONPATH': SRC}


def km(a, b):
    dy = (a[0]-b[0])*111; dx = (a[1]-b[1])*111*math.cos(math.radians(37.55)); return math.hypot(dx, dy)


def main():
    ROOT = Path(sys.argv[1])
    bundles = sorted(p.name for p in (ROOT/'bundles').iterdir() if p.name.startswith('affected_2'))
    BUNDLE = sys.argv[2] if len(sys.argv) > 2 else bundles[len(bundles)//2]
    B = ROOT/'bundles'/BUNDLE
    fleets = sorted(int(p.stem.split('_f')[-1]) for p in B.glob('scenario_f*.json'))
    FLEET = int(sys.argv[3]) if len(sys.argv) > 3 else fleets[0]
    SM = ROOT/'smoke'; SM.mkdir(exist_ok=True)
    tt = json.load(open(B/'travel_times.json'))
    adj = {}
    for s in tt['segments']: adj.setdefault(s['from_stop_id'], []).append((s['to_stop_id'], s['profile'][1][1]))
    def path(a, b):
        dist = {a: 0}; prev = {}; pq = [(0, a)]
        while pq:
            dv, u = heapq.heappop(pq)
            if u == b: break
            if dv > dist[u]: continue
            for v, w in adj.get(u, []):
                if dv + w < dist.get(v, 1e18): dist[v] = dv + w; prev[v] = u; heapq.heappush(pq, (dv + w, v))
        if b not in dist: return None, None
        p = [b]
        while p[-1] != a: p.append(prev[p[-1]])
        return p[::-1], dist[b]
    st = {r['stop_id']: (float(r['stop_lat']), float(r['stop_lon'])) for r in csv.DictReader(open(ROOT/'network'/'stops.csv', encoding='utf-8'))}
    date = BUNDLE[-8:]
    hub, seeds = {}, {}
    with gzip.open(ROOT/'demand'/'demand_flows.csv.gz', 'rt', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            if r['date'] != date or r['kind'] != 'access' or r['in_network'] != 'True': continue
            fl = float(r['flow']); k = (r['station_id'], r['station_stop_id'])
            hub[k] = hub.get(k, 0) + fl
            seeds.setdefault(r['station_id'], {}); seeds[r['station_id']][r['stop_id']] = seeds[r['station_id']].get(r['stop_id'], 0) + fl
    routes = []; used = set()
    for (k, hs), _ in sorted(hub.items(), key=lambda x: -x[1]):
        if k in used: continue
        for s, _ in sorted(seeds[k].items(), key=lambda x: -x[1]):
            if s not in st or hs not in st or not (1.2 <= km(st[s], st[hs]) <= 4.0): continue
            p, t = path(s, hs)
            if p and t <= 35*60 and len(set(p)) == len(p) and path(hs, s)[0]:   # 변환기 RETURN 검사: 끝 → 처음 복귀 경로 필요
                routes.append({'route_id': f'r{len(routes)+1}', 'stops': p, 'vehicles': 0}); used.add(k); break
        if len(routes) == 4: break
    if not routes:
        print('SMOKE FAIL 스모크 노선을 못 만듦(1.2~4km·35분 경로 없음)'); sys.exit(1)
    routes = routes[:FLEET]
    for i in range(FLEET): routes[i % len(routes)]['vehicles'] += 1
    plan = {'plan_id': 'plan-smoke', 'revision': 1, 'network_snapshot_id': tt['network_snapshot_id'], 'routes': routes}
    json.dump(plan, open(SM/'plan_smoke_comparison.json', 'w'), ensure_ascii=False, indent=1)
    def run(args):
        p = subprocess.run([sys.executable, '-m'] + args, env=ENV, capture_output=True, text=True)
        return p.returncode, (p.stdout[-1500:] + p.stderr[-1500:]).strip()
    rc, o = run(['busstrike.sim.adapters.comparison_plan', '--plan', str(SM/'plan_smoke_comparison.json'), '--scenario', str(B/f'scenario_f{FLEET}.json'),
                 '--travel-times', str(B/'travel_times.json'), '--out', str(SM/'plan_smoke_m05.json')])
    if rc: print(o); print(f'SMOKE FAIL 변환기 rc={rc}'); sys.exit(1)
    rc, o = run(['busstrike.sim', 'run', '--scenario', str(B/f'scenario_f{FLEET}.json'), '--run-inputs', str(B/f'run_inputs_f{FLEET}.json'),
                 '--plan', str(SM/'plan_smoke_m05.json'), '--out', str(SM/'out')])
    if rc: print(o); print(f'SMOKE FAIL M05 rc={rc}'); sys.exit(1)
    rc, o = run(['busstrike.evaluation.verify', str(SM/'out'/'run'), '--max-recompute', '200000', '--out', str(SM/'verifier_report.json')])
    rep = json.load(open(SM/'verifier_report.json')) if (SM/'verifier_report.json').exists() else {}
    m1 = json.load(open(SM/'out'/'run'/'metrics.json')).get('M1', {})
    info = {'bundle': BUNDLE, 'fleet': FLEET, 'routes': len(routes), 'verify_rc': rc, 'verify_pass': rep.get('pass'),
            'failed_checks': rep.get('failed_checks'), 'M1_total': m1.get('total'), 'M1_served': m1.get('served')}
    json.dump(info, open(SM/'smoke_summary.json', 'w'), ensure_ascii=False, indent=1)
    print(json.dumps(info, ensure_ascii=False))
    if rc or rep.get('pass') is not True:
        print(o); print(f'SMOKE FAIL M07 rc={rc} pass={rep.get("pass")} failed={rep.get("failed_checks")}'); sys.exit(1)
    print(f'SMOKE OK {BUNDLE} f{FLEET} 노선{len(routes)} M1 {m1.get("served")}/{m1.get("total")}')


if __name__ == '__main__':
    main()
