# 만든 구 묶음과 기준 묶음(zip_v4/region/<구>_0610_v4[_test])을 비교한다.
#   PYTHONPATH=<repo>/src python validate.py --mine <폴더> --ref <폴더> [--json out.json]
# 비교: 변형별 5일 평균 합, 행 수, 망(구간·정류장), 역 지정 정류장, 흡수 노선, demand_bins(날짜별), 정밀 흡수(Absorber 기본값, 마을+경기)
import argparse, csv, gzip, hashlib, json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]/'src'))
from busstrike.planning.routegen import data as D          # noqa: E402
from busstrike.planning.routegen.absorb import Absorber, AbsorbParams  # noqa: E402


def rd(p):
    op = gzip.open if str(p).endswith('.gz') else open
    with op(p, 'rt', encoding='utf-8', newline='') as f:
        return list(csv.DictReader(f))


def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def summary(root):
    root = Path(root); o = {}
    F = rd(root/'demand'/'demand_flows.csv.gz')
    o['demand_rows'] = len(F)
    rows = D.load_flows(root)
    for v in D.VARIANTS:
        o[f'mean_{v}'] = round(D.build_demand(rows, v).total(), 1)
    o['flow_total_nw_mean'] = round(sum(float(r['flow_total']) for r in F if r['walkable'] == 'False')/5, 1)
    o['flow_unknown_nw_mean'] = round(sum(float(r['flow_unknown'] or 0) for r in F if r['walkable'] == 'False')/5, 1)
    o['rows_maeul_reach'] = sum(r['maeul_reach'] == 'True' for r in F)
    o['rows_gg_reach'] = sum(r['gg_reach'] == 'True' for r in F)
    o['rows_gg_routes_nonempty'] = sum(bool(r['gg_routes']) for r in F)
    tt = json.loads((root/'network'/'travel_times.json').read_text())
    o['segments'] = len(tt['segments']); o['network_id'] = tt['network_snapshot_id']
    o['stops_csv'] = len(rd(root/'network'/'stops.csv'))
    ss = rd(root/'network'/'station_stops.csv'); o['station_stops_rows'] = len(ss)
    ab = rd(root/'network'/'absorbing_routes.csv')
    o['absorb_maeul'] = sum(r['mode'] == 'maeul' for r in ab); o['absorb_gg'] = sum(r['mode'] == 'gg' for r in ab)
    srb = rd(root/'demand'/'stop_route_boardings.csv.gz'); o['srb_rows'] = len(srb)
    o['srb_sum_mean'] = round(sum(float(r['boardings']) for r in srb)/5, 1)
    maeul_ids = {r['route_id'] for r in ab if r['mode'] == 'maeul'}
    o['srb_maeul_absorbing_sum_mean'] = round(sum(float(r['boardings']) for r in srb if r['route_id'] in maeul_ids)/5, 1)
    A = Absorber(root, AbsorbParams())
    res = {}
    for v in D.VARIANTS:
        if v != 'affected': continue
        _, s = A.absorb(rows, True, True)
        res = {k: round(x/5, 1) for k, x in s.items()}
    o['absorb_maeul_mean'] = res['maeul']; o['absorb_gg_mean'] = res['gg']
    o['absorb_maeul_req_mean'] = res['maeul_req']; o['absorb_gg_req_mean'] = res['gg_req']
    o['absorb_residual_mean'] = round(o['mean_affected'] - res['maeul'] - res['gg'], 1)
    bins = {}
    for b in sorted((root/'bundles').iterdir()):
        db = rd(b/'demand_bins.csv'); bins[b.name] = (sum(int(r['passenger_count']) for r in db), len(db))
    o['_bins'] = bins
    o['_sha'] = {f: sha(root/f) for f in ['network/travel_times.json', 'network/station_stops.csv', 'network/stops.csv', 'network/stations.csv',
                                           'network/absorbing_routes.csv', 'registry/strike_route_registry.csv', 'gtfs/stop_times.txt']}
    return o


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--mine'); ap.add_argument('--ref'); ap.add_argument('--json')
    a = ap.parse_args()
    m, r = summary(a.mine), summary(a.ref)
    out = {'mine': a.mine, 'ref': a.ref, 'rows': []}
    print(f'{"항목":38s} {"mine":>14s} {"ref":>14s} {"diff%":>8s}')
    for k in m:
        if k.startswith('_'): continue
        x, y = m[k], r[k]
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            dp = round(100*(x-y)/y, 3) if y else (0.0 if x == y else None)
        else: dp = None if x != y else 0.0
        out['rows'].append({'item': k, 'mine': x, 'ref': y, 'diff_pct': dp})
        print(f'{k:38s} {str(x):>14s} {str(y):>14s} {str(dp):>8s}')
    same = {f: m['_sha'][f] == r['_sha'][f] for f in m['_sha']}
    out['identical_files'] = same; print('identical files:', same)
    bm, br = m['_bins'], r['_bins']
    worst = max((abs(bm[k][0]-br[k][0])/max(br[k][0], 1)*100, k) for k in br)
    out['bins_identical'] = sum(bm.get(k) == br[k] for k in br); out['bins_total'] = len(br); out['bins_worst_pct'] = round(worst[0], 3), worst[1]
    out['bins'] = {k: {'mine': bm.get(k), 'ref': br[k]} for k in br}
    print(f'demand_bins: 같은 승객·행 수 {out["bins_identical"]}/{len(br)}, 최대 승객 차 {worst[0]:.3f}% ({worst[1]})')
    if a.json: json.dump(out, open(a.json, 'w'), ensure_ascii=False, indent=1)


if __name__ == '__main__':
    main()
