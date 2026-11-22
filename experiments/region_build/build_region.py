# 서울 아무 구나 06~10시 M05·M07 입력 묶음(v4 구 묶음과 같은 폴더 형식)을 만든다.
#
# guro_build.py(v4 구 묶음 생성기, zip_v4/region/<구>_0610_v4/scripts/)를 CLI로 옮긴 것이다. 계산 순서·규칙은 그대로다.
#   python build_region.py --prepare                          # 원자료 캐시(한 번)
#   python build_region.py --slug guro --gu 구로구 --prefix 116 --split exp --fleets 11,13 --out <폴더>
#
# 원자료(이 PC에서 찾은 것, sources.csv에 sha256 기록)
#   02 버스 GTFS v1.1    Desktop/공소/busstrike_data_release_20261111.zip 안 data/02_…_v1.1.zip (stop_times.txt sha 기준 묶음과 같음)
#   04 구간 운행시간     Desktop/공소/가공본/04_data_segment-traveltime_weekday_20260921-1025_h06-10_v1.csv (같음)
#   TPSS hpasngr 10/26~11/01, hturn 10/26~11/01  Downloads/ (같음)
#   서울 수요 벡터·패턴·역  zip_v4/seoul/seoul_routegen_inputs_0610_v4 (exp·test)
#   역 좌표: 서울 입력 network/stations.csv + 09 철도 GTFS stops.txt (기준 묶음의 stations_all.csv는 없음)
#   정류장 ID 앞 3자리 → 구: 서울 입력 district_fleet.csv의 stop_prefix (기준 묶음의 xlsx는 없음)
#
# 실험(exp) 주간은 기준 묶음과 같은 원자료라 같은 계산이다.
# 테스트(test) 주간 TPSS hpasngr·hturn(11/02~08)은 이 PC에 없다. 그래서 아래를 근사한다(README·build_stats `approximations`).
#   - 파업 노선 승차 비율 sa: 테스트 날짜의 마을 외 비율 u(서울 test 벡터 f0/flow, 정류장·날짜·시간별, TPSS 테스트 값 그대로)
#       × 실험 주간 (파업 노선 승차 ÷ 마을 외 승차)(정류장·시간별 5일 합, hpasngr 실험 주간). flow_unknown도 같은 방식.
#   - gg_reach: 서울 test 벡터의 gg_reach(테스트 주간 hturn으로 이미 판정된 값) 그대로.
#     gg_routes: 실험 주간 hturn 순서·운행으로 찾은 노선 중 gg_reach가 참인 행만. 참인데 노선이 없으면 순서만 맞는 노선.
#   - stop_route_boardings·demand_points: 실험 주간 값(5일 평균) × (테스트 날짜 정류장 수요량 ÷ 실험 주간 평균),
#       정류장 수요량 = 서울 벡터의 access flow + local + no_rail 합(= 승차량 × 날짜 무관 비율이라 비가 승차량 비와 같다).
#   - absorbing_routes의 경기 노선 순서·운행합: 실험 주간 hturn.
import sys, os, json, gzip, shutil, hashlib, argparse, zipfile, time
import numpy as np, pandas as pd
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PROJ = Path('/mnt/c/Users/ddonggojin/orca/projects')
WIN = Path('/mnt/c/Users/ddonggojin')
DEF = {
    'gtfs_zip': WIN/'Desktop/공소/busstrike_data_release_20261111.zip',   # 안의 data/02_…_v1.1.zip
    'gtfs_member': 'data/02_data_gtfs-seoul-bus-approx_svc20261102_h06-10_v1.1.zip',
    'segtt': WIN/'Desktop/공소/가공본/04_data_segment-traveltime_weekday_20260921-1025_h06-10_v1.csv',
    'rail_zip': WIN/'Desktop/공소/가공본/09_data_gtfs-seoul-rail_weekday-20261102-06_tt20260901_v1.zip',
    'hp_exp': WIN/'Downloads/tpss_sta_route_hpasngr_2026.10.26-11.01.zip',
    'hturn_exp': WIN/'Downloads/tpss_sta_route_hturn_2026.10.26-11.01.zip',
    'hp_test': None, 'hturn_test': None,      # 11/02~08 주간 파일(있으면 근사 대신 그대로 쓴다)
    'seoul': PROJ/'guro_exp_work/zip_v4/seoul/seoul_routegen_inputs_0610_v4',
    'cache': Path.home()/'.cache/region_build',
}
SPLIT_DATES = {'exp': ['20261026', '20261027', '20261028', '20261029', '20261030'],
               'test': ['20261102', '20261103', '20261104', '20261105', '20261106']}
FAR_M = 400
REGISTRY_VERSION = 'v0-rule_only'
WS, WE, BIN = 6*3600, 10*3600, 3600
HOURS = [6, 7, 8, 9]
SEED = 42; PAT = 'exp20261026-30_k5'
GG_MIN_RUNS = 1.0
POLICY = {'max_wait_s': 1200, 'max_journey_s': 3600, 'allowed_transfers': 0, 'arrival_method': 'systematic',
          'dwell_base_s': 10, 'dwell_per_boarding_s': 0, 'dwell_per_alighting_s': 0, 'turnaround_s': 180,
          'cycle_rest_s': 0, 'unknown_link': 'error'}
STATE = {'서울간선버스': 'affected', '서울지선버스': 'affected', '서울광역버스': 'affected', '서울순환버스': 'affected',
         '서울마을버스': 'unaffected'}
HPCOLS = [f'승차_승객_수_{h:02d}시' for h in HOURS]


def km(la1, lo1, la2, lo2):
    dy = (la2-la1)*111.0; dx = (lo2-lo1)*111.0*np.cos(np.radians(37.55)); return np.sqrt(dx*dx+dy*dy)
def sec(t): h, m, s = map(int, t.split(':')); return h*3600+m*60+s
def log(*a): print(f'[{time.strftime("%H:%M:%S")}]', *a, flush=True)
def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for ch in iter(lambda: f.read(1 << 20), b''): h.update(ch)
    return h.hexdigest()


# ======================= 캐시(한 번) =======================
def read_hp_zip(path, dates=None):
    z = zipfile.ZipFile(path)
    hp = pd.read_csv(z.open(z.infolist()[0]), encoding='cp949', dtype=str, usecols=['기준_날짜', '노선_ID', '정류장_ID'] + HPCOLS)
    return hp if dates is None else hp[hp['기준_날짜'].isin(dates)]


def gg_table(path):
    """guro_build.py 6절의 hturn 가공 그대로: 경기 노선(ID 1로 시작 안 함) 평일 평균 시간당 운행, 정류장 순서 최소·최대."""
    _z = zipfile.ZipFile(path); _ht = pd.read_csv(_z.open(_z.infolist()[0]), encoding='cp949', dtype=str)
    _ht = _ht[~_ht['노선_ID'].str.startswith('1')]; _wk = [d for d in sorted(_ht['기준_날짜'].unique()) if pd.Timestamp(d).day_of_week < 5]
    _ht = _ht[_ht['기준_날짜'].isin(_wk)]
    for _h in HOURS: _ht[f'r{_h}'] = pd.to_numeric(_ht[f'버스운행횟수_{_h:02d}시'], errors='coerce').fillna(0)
    _ht['ord'] = pd.to_numeric(_ht['정류장_순서'], errors='coerce')
    GG = _ht.groupby(['노선_ID', '정류장_ID']).agg(omin=('ord', 'min'), omax=('ord', 'max'), **{f'r{_h}': (f'r{_h}', 'sum') for _h in HOURS}).reset_index()
    for _h in HOURS: GG[f'r{_h}'] = GG[f'r{_h}'] / len(_wk)
    return GG, _wk


def prepare(a):
    C = Path(a.cache); C.mkdir(parents=True, exist_ok=True)
    G = C/'gtfs'
    if not (G/'stop_times.txt').exists():
        log('extract GTFS'); z = zipfile.ZipFile(a.gtfs_zip)
        if a.gtfs_member and a.gtfs_member in z.namelist(): z = zipfile.ZipFile(z.open(a.gtfs_member))
        z.extractall(G)
    if not (C/'st.pkl').exists():
        log('stop_times')
        st = pd.read_csv(G/'stop_times.txt', dtype=str); st['t'] = st.departure_time.map(sec); st['q'] = st.stop_sequence.astype(int)
        st = st.sort_values(['trip_id', 'q']); st['nx'] = st.groupby('trip_id').stop_id.shift(-1); st['nt'] = st.groupby('trip_id').t.shift(-1)
        st.to_pickle(C/'st.pkl')
    R = C/'rail'
    if not (R/'stops.txt').exists():
        log('rail stops'); R.mkdir(exist_ok=True)
        z = zipfile.ZipFile(a.rail_zip); inner = zipfile.ZipFile(z.open('gtfs_rail.zip'))
        (R/'stops.txt').write_bytes(inner.read('stops.txt'))
    for split in ('exp', 'test'):
        hp_src = a.hp_exp if split == 'exp' else a.hp_test
        if hp_src and not (C/f'hp_{split}.pkl').exists():
            log('hpasngr', split); read_hp_zip(hp_src).to_pickle(C/f'hp_{split}.pkl')
        ht_src = a.hturn_exp if split == 'exp' else a.hturn_test
        if ht_src and not (C/f'gg_{split}.pkl').exists():
            log('hturn', split); GG, wk = gg_table(ht_src); GG.to_pickle(C/f'gg_{split}.pkl'); json.dump(wk, open(C/f'gg_{split}_weekdays.json', 'w'))
        V = Path(a.seoul)/'demand'/split
        D = C/f'vec_{split}'
        if not (D/'done').exists():
            D.mkdir(exist_ok=True)
            fl = pd.read_csv(Path(a.seoul)/'district_fleet.csv', dtype=str)
            pfx = sorted(fl.stop_prefix)
            agg = []
            for name in ('access', 'egress'):
                log('vectors', split, name)
                X = pd.read_csv(V/f'{name}_vectors_seoul.csv.gz', dtype={'stop': str, 'station': str, 'date': str})
                if name == 'access':
                    agg.append(X.groupby(['date', 'hour', 'stop'])[['flow', 'f0']].sum())
                p3 = X.stop.str[:3]
                for p in pfx: X[p3 == p].to_pickle(D/f'{name}_{p}.pkl')
            LN = pd.read_csv(V/'local_norail_seoul.csv.gz', dtype={'stop': str, 'date': str})
            for p in pfx: LN[LN.stop.str[:3] == p].to_pickle(D/f'local_norail_{p}.pkl')
            # 정류장·날짜·시간 표: u = 마을 외 승차 비율(f0/flow), vol = access flow + local + no_rail(승차량 × 날짜 무관 비율)
            U = agg[0].copy(); U['u'] = np.where(U.flow > 0, U.f0/U.flow, np.nan)
            LN['x'] = LN.local + LN.no_rail
            U['vol'] = U.flow.add(LN.groupby(['date', 'hour', 'stop']).x.sum(), fill_value=0)
            vol = U.flow.add(LN.groupby(['date', 'hour', 'stop']).x.sum(), fill_value=0)
            U = pd.DataFrame({'u': U.u.reindex(vol.index), 'vol': vol}).reset_index()
            U.to_pickle(D/'stop_table.pkl')
            (D/'done').write_text('ok')
    log('prepare done', C)


# ======================= 구 묶음 =======================
def build(a):
    C = Path(a.cache)
    sys.path.insert(0, str(a.simsrc))
    from busstrike.sim import io as sio
    from busstrike.sim.models import DemandFlow
    from busstrike.sim.arrivals import realize_bins, write_demand_bins, build_run_inputs
    OUT = Path(a.out); OUT.mkdir(parents=True, exist_ok=True)
    SEO = Path(a.seoul)
    SPLIT = a.split; DATES = SPLIT_DATES[SPLIT]; SLUG = a.slug; GU_NAME = a.gu; PFX = a.prefix; BUF_KM = a.buf_km
    FLEETS = [int(x) for x in a.fleets.split(',')]
    NET = f'{SLUG}-buf2km-0610-v2'
    PROFILE = f'{SLUG}-0610-{SPLIT}-tpss{DATES[0]}_{DATES[-1][-2:]}-pattern-exp5k5-v4'
    EXACT = SPLIT == 'exp' or (a.hp_test and a.hturn_test)
    HPK = 'exp' if SPLIT == 'exp' or not a.hp_test else 'test'
    GGK = 'exp' if SPLIT == 'exp' or not a.hturn_test else 'test'
    GTFS = C/'gtfs'
    stats = {'builder': 'experiments/region_build/build_region.py', 'slug': SLUG, 'gu': GU_NAME, 'prefix': PFX, 'fleets': FLEETS}
    approx = {}
    fl = pd.read_csv(SEO/'district_fleet.csv', dtype=str); GU = dict(zip(fl.stop_prefix, fl.gu))
    def gu_of(s): return GU.get(str(s)[:3], '서울외')

    # ---------------- 1. 영역과 GTFS 클립 ----------------
    stops = pd.read_csv(GTFS/'stops.txt', dtype=str); stops['la'] = stops.stop_lat.astype(float); stops['lo'] = stops.stop_lon.astype(float)
    S = stops.set_index('stop_id')
    g = stops[stops.stop_id.str[:3] == PFX]
    if len(g) == 0: raise SystemExit(f'정류장 ID 앞 3자리 {PFX}인 정류장이 GTFS에 없다')
    d = np.min(km(stops.la.values[:, None], stops.lo.values[:, None], g.la.values[None, :], g.lo.values[None, :]), axis=1)
    area = set(stops.stop_id[d <= BUF_KM])
    st = pd.read_pickle(C/'st.pkl')
    c = st[st.stop_id.isin(area) & st.t.between(WS, WE)]
    n = c.groupby('trip_id').size(); keep = n[n >= 2].index; c = c[c.trip_id.isin(keep)]
    GD = OUT/'gtfs'; GD.mkdir(exist_ok=True)
    c[['trip_id', 'arrival_time', 'departure_time', 'stop_id', 'stop_sequence', 'timepoint']].to_csv(GD/'stop_times.txt', index=False)
    trips = pd.read_csv(GTFS/'trips.txt', dtype=str); trips = trips[trips.trip_id.isin(keep)]; trips.to_csv(GD/'trips.txt', index=False)
    routes = pd.read_csv(GTFS/'routes.txt', dtype=str); routes = routes[routes.route_id.isin(trips.route_id)]; routes.to_csv(GD/'routes.txt', index=False)
    stops[stops.stop_id.isin(c.stop_id)][['stop_id', 'stop_name', 'stop_lat', 'stop_lon']].to_csv(GD/'stops.txt', index=False)
    for f in ['agency.txt', 'calendar_dates.txt']: shutil.copy(GTFS/f, GD)
    fi = pd.read_csv(GTFS/'feed_info.txt', dtype=str); src_version = fi.feed_version.iloc[0]
    fi['feed_version'] = f'{SLUG}-buf2km-0610-from-{src_version}'; fi.to_csv(GD/'feed_info.txt', index=False)
    stats['gtfs'] = {'source_feed_version': src_version, 'area_stops': len(area), 'guro_stops': len(g), 'routes': len(routes),
                     'trips': len(trips), 'stop_times': len(c), 'stops_in_clip': int(c.stop_id.nunique())}
    log(SLUG, SPLIT, 'gtfs', stats['gtfs'])

    # ---------------- 2. travel_times.json (guro_build.py와 같은 규칙, 04 실측 중앙값) ----------------
    e = st[st.nx.notna() & st.stop_id.isin(area) & st.nx.isin(area)].copy(); e['dt'] = e.nt - e.t
    e = e[e.dt > 0]; e['h'] = e.t // 3600
    allmed = e.groupby(['stop_id', 'nx']).dt.median()
    hmed = e[e.h.isin(HOURS)].groupby(['stop_id', 'nx', 'h']).dt.median().to_dict()
    del e
    TH = [6, 7, 8, 9, 10]
    sg = pd.read_csv(a.segtt, dtype={'route_id': str, 'from_stop': str, 'to_stop': str})
    sg = sg[sg.from_stop.isin(area) & sg.to_stop.isin(area)]
    OBS = {}
    for h in TH:
        m = sg[['from_stop', 'to_stop', f'h{h:02d}_median_s', f'h{h:02d}_n']].dropna()
        m = m[m[f'h{h:02d}_n'] > 0]
        for (a_, b_), grp in m.groupby(['from_stop', 'to_stop']):
            v = grp[f'h{h:02d}_median_s'].values.astype(float); w = grp[f'h{h:02d}_n'].values.astype(float)
            o = np.argsort(v); cw = np.cumsum(w[o])
            OBS[(a_, b_, h)] = (float(v[o][np.searchsorted(cw, cw[-1]/2)]), int(w.sum()), len(v))
    segs = []; SEG = {}; src_rows = []
    filled = {'observed': 0, 'neighbor_hour': 0, 'other_hour': 0, 'gtfs_derived': 0}
    for (a_, b_), m in allmed.items():
        prof = []
        for h in HOURS:
            if (a_, b_, h) in OBS:
                v, nd_, nr = OBS[(a_, b_, h)]; src = 'observed'
            else:
                near = [OBS[(a_, b_, h2)][0] for h2 in (h-1, h+1) if (a_, b_, h2) in OBS]
                other = [OBS[(a_, b_, h2)][0] for h2 in TH if (a_, b_, h2) in OBS]
                nd_, nr = 0, 0
                if near: v = float(np.mean(near)); src = 'neighbor_hour'
                elif other: v = float(np.median(other)); src = 'other_hour'
                else:
                    v = hmed.get((a_, b_, h), np.nan)
                    if np.isnan(v):
                        near = [hmed.get((a_, b_, h2), np.nan) for h2 in (h-1, h+1)]; near = [x for x in near if not np.isnan(x)]
                        v = float(np.mean(near)) if near else m
                    src = 'gtfs_derived'
            filled[src] += 1
            t = max(1, int(round(v)))
            prof.append([h*3600, t])
            src_rows.append({'from_stop_id': a_, 'to_stop_id': b_, 'hour': h, 'travel_s': t, 'source': src, 'n_days': nd_, 'n_routes': nr})
        segs.append({'from_stop_id': a_, 'to_stop_id': b_, 'distance_m': int(round(1000*km(S.la[a_], S.lo[a_], S.la[b_], S.lo[b_]))), 'profile': prof})
        SEG[(a_, b_)] = prof[1][1]
    segs.sort(key=lambda s: (s['from_stop_id'], s['to_stop_id']))
    (OUT/'network').mkdir(exist_ok=True)
    TT_DOC = {'schema_version': 1, 'network_snapshot_id': NET, 'valid_from_s': WS, 'valid_until_s': WE + 2*3600, 'segments': segs}
    sio.write_bytes(OUT/'network'/'travel_times.json', sio.json_bytes(TT_DOC))
    nodes = set(x for x, _ in SEG) | set(y for _, y in SEG)
    _sr = pd.DataFrame(src_rows)
    _sr.sort_values(['from_stop_id', 'to_stop_id', 'hour']).to_csv(OUT/'network'/'travel_time_sources.csv', index=False)
    stats['travel_times'] = {'segments': len(segs), 'nodes': len(nodes), 'profile_cells': filled,
                             'not_observed_cells_by_hour': {int(k): int(v) for k, v in _sr[_sr.source != 'observed'].hour.value_counts().sort_index().items()},
                             'segments_with_any_not_observed': int(_sr[_sr.source != 'observed'][['from_stop_id', 'to_stop_id']].drop_duplicates().shape[0]),
                             'value_basis': '04 평일 중앙값 2026-09-21~10-25(수요 기간 10/26~30 아님), 정차 포함 여부 미확인',
                             'distance_basis': 'straight_line (정류장 좌표 직선거리, 실제 주행거리 아님)'}
    st_area = stops[stops.stop_id.isin(nodes)].copy(); st_area['gu'] = st_area.stop_id.map(gu_of)
    st_area[['stop_id', 'stop_name', 'stop_lat', 'stop_lon', 'gu']].to_csv(OUT/'network'/'stops.csv', index=False)
    log(SLUG, SPLIT, 'travel times', len(segs))

    # ---------------- 3. 역 지정 승강 정류장 ----------------
    # 역 좌표: 서울 입력 stations.csv(서울 망 근처 역 전부) + 철도 GTFS(09) 정류장. 기준 묶음의 stations_all.csv 대신.
    ST = pd.concat([pd.read_csv(SEO/'network'/'stations.csv', dtype={'stop_id': str}),
                    pd.read_csv(C/'rail'/'stops.txt', dtype={'stop_id': str})]).drop_duplicates('stop_id'); ST = ST[ST.stop_lat.notna()]
    nd = stops[stops.stop_id.isin(nodes)]
    cand = {}
    for r in ST.itertuples():
        dd = km(r.stop_lat, r.stop_lon, nd.la.values, nd.lo.values)
        m = np.where(dd <= 0.25)[0]
        if len(m) == 0 and dd.min() <= 0.5: m = np.array([dd.argmin()])
        if len(m): cand[r.stop_id] = {nd.stop_id.values[i]: int(round(dd[i]*1000)) for i in m}
    tr = pd.read_csv(SEO/'patterns'/'transfer_k5.csv', dtype=str); tr['n'] = tr.t.astype(float)
    acc_t = tr[(tr.alight_mode == 'bus') & (tr.board_mode == 'rail')].groupby(['board_id', 'alight_id']).n.sum()
    egr_t = tr[(tr.alight_mode == 'rail') & (tr.board_mode == 'bus')].groupby(['alight_id', 'board_id']).n.sum()
    SNAME = dict(zip(ST.stop_id, ST.stop_name))
    rows = []
    for k, cs in cand.items():
        for kind, T in (('access', acc_t), ('egress', egr_t)):
            sub = T.loc[k] if k in T.index.get_level_values(0) else pd.Series(dtype=float)
            counts = {s: float(sub.get(s, 0.0)) for s in cs}
            if max(counts.values()) > 0:
                best = sorted(cs, key=lambda s: (-counts[s], cs[s], s))[0]; rule = 'transfer_max'
            else:
                best = sorted(cs, key=lambda s: (cs[s], s))[0]; rule = 'nearest'
            rows.append({'station_id': k, 'station_name': SNAME.get(k, ''), 'kind': kind, 'station_stop_id': best, 'rule': rule,
                         'transfers_exp5_0609': round(counts[best], 1), 'dist_m': cs[best],
                         'candidates': ' '.join(sorted(cs))})
    SS = pd.DataFrame(rows).sort_values(['station_id', 'kind'])
    SS['status'] = np.where(SS.dist_m <= FAR_M, 'ok', 'too_far')
    SS.to_csv(OUT/'network'/'station_stops.csv', index=False)
    DESIG = {(r.station_id, r.kind): r.station_stop_id for r in SS.itertuples() if r.status == 'ok'}
    FAR = {(r.station_id, r.kind) for r in SS.itertuples() if r.status == 'too_far'}
    stats['station_stops'] = {'stations': int(SS.station_id.nunique()), 'rules': SS.rule.value_counts().to_dict(),
                              'too_far_rule_m': FAR_M, 'too_far': SS[SS.status == 'too_far'][['station_name', 'kind', 'dist_m']].to_dict('records')}

    # ---------------- 4. 파업 노선 등록부(초안) ----------------
    reg = routes[['route_id', 'route_short_name', 'route_desc']].rename(columns={'route_short_name': 'route_name', 'route_desc': 'route_type'}).copy()
    reg['operator_id'] = ''; reg['operator_name'] = ''; reg['jurisdiction'] = '서울'
    reg['strike_state'] = reg.route_type.map(STATE).fillna('unknown')
    reg['valid_from'] = '2026-01-13'; reg['valid_to'] = '2026-01-14'
    reg['source_url'] = 'GTFS route_desc 기준 규칙 분류(공식 등록부 대조 전)'; reg['match_status'] = 'rule_only'
    reg['registry_version'] = REGISTRY_VERSION
    reg = reg[['route_id', 'route_name', 'operator_id', 'operator_name', 'jurisdiction', 'route_type', 'strike_state', 'valid_from', 'valid_to', 'source_url', 'match_status', 'registry_version']]
    (OUT/'registry').mkdir(exist_ok=True); reg.sort_values('route_id').to_csv(OUT/'registry'/'strike_route_registry.csv', index=False)
    stats['registry'] = reg.strike_state.value_counts().to_dict(); stats['registry_version'] = REGISTRY_VERSION
    ALLR = pd.read_csv(GTFS/'routes.txt', dtype=str); STATE_ALL = dict(zip(ALLR.route_id, ALLR.route_desc.map(STATE).fillna('unknown')))

    # ---------------- 5. 정류장·시간별 파업 노선 승차 비율 ----------------
    hp_all = pd.read_pickle(C/f'hp_{HPK}.pkl')
    hp_dates = DATES if HPK == SPLIT else SPLIT_DATES['exp']
    hp = hp_all[hp_all['기준_날짜'].isin(hp_dates)].copy()
    hp['state'] = hp['노선_ID'].map(STATE_ALL).fillna('unknown')
    hp.loc[~hp['노선_ID'].isin(STATE_ALL), 'state'] = 'not_in_gtfs'
    long = hp.melt(id_vars=['기준_날짜', '정류장_ID', 'state'], value_vars=HPCOLS, var_name='h', value_name='b')
    long['hour'] = long.h.str[-3:-1].astype(int); long['b'] = pd.to_numeric(long.b, errors='coerce').fillna(0)
    P = long.groupby(['기준_날짜', '정류장_ID', 'hour', 'state']).b.sum().unstack(fill_value=0)
    for col in ['affected', 'unaffected', 'unknown', 'not_in_gtfs']:
        if col not in P: P[col] = 0.0
    tot = P[['affected', 'unaffected', 'unknown', 'not_in_gtfs']].sum(axis=1)
    if HPK == SPLIT:
        P['sa'] = np.where(tot > 0, P.affected/tot, np.nan); P['su'] = np.where(tot > 0, P.unknown/tot, np.nan)
        SHARE = P[['sa', 'su']].to_dict('index')
        def share(d_, s, h):
            v = SHARE.get((d_, s, h)); return (1.0, 0.0) if v is None or np.isnan(v['sa']) else (v['sa'], v['su'])
    else:
        # 근사: sa = u_test(날짜·정류장·시간) × (실험 주간 파업 노선 승차 ÷ 마을 외 승차)(정류장·시간)
        Q = P.groupby(level=[1, 2])[['affected', 'unaffected', 'unknown', 'not_in_gtfs']].sum()
        nm = Q.affected + Q.unknown + Q.not_in_gtfs; t5 = nm + Q.unaffected
        KA = dict(zip(Q.index, np.where(nm > 0, Q.affected/nm.where(nm > 0, 1), 1.0)))
        KU = dict(zip(Q.index, np.where(nm > 0, Q.unknown/nm.where(nm > 0, 1), 0.0)))
        UEXP = dict(zip(Q.index, np.where(t5 > 0, nm/t5.where(t5 > 0, 1), np.nan)))
        UT = pd.read_pickle(C/f'vec_{SPLIT}'/'stop_table.pkl')
        UTEST = {(r.date, r.stop, r.hour): r.u for r in UT.itertuples() if not np.isnan(r.u)}
        cnt = {'u_test': 0, 'u_exp_fallback': 0, 'default_1': 0}
        def share(d_, s, h):
            key = (s, h)
            if key not in KA and (d_, s, h) not in UTEST:
                cnt['default_1'] += 1; return (1.0, 0.0)
            u = UTEST.get((d_, s, h))
            if u is None:
                u = UEXP.get(key, np.nan); cnt['u_exp_fallback'] += 1
                if u is None or np.isnan(u): u = 1.0
            else: cnt['u_test'] += 1
            return (u*KA.get(key, 1.0), u*KU.get(key, 0.0))
        approx['flow_share'] = ('test 주간 hpasngr 없음. sa = u_test × κ, u_test = 서울 test 벡터 f0/flow(정류장·날짜·시간, 테스트 TPSS 그대로), '
                                'κ = 실험 주간 hpasngr의 (파업 노선 승차 ÷ 마을버스 외 승차)(정류장·시간, 5일 합). flow_unknown도 같은 방식(미확인 노선 몫).')
        approx['_share_counts'] = cnt

    # ---------------- 6. 대체 노선(마을·경기)과 흡수 판정 ----------------
    R_ALL = pd.read_csv(GTFS/'routes.txt', dtype=str); MAEUL = set(R_ALL.route_id[R_ALL.route_desc == '서울마을버스'])
    TALL = pd.read_csv(GTFS/'trips.txt', dtype=str)
    sm = st[st.trip_id.isin(TALL.trip_id[TALL.route_id.isin(MAEUL)])]
    fa = sm.groupby('trip_id').t.agg(['min', 'max']); act = fa[(fa['max'] >= WS) & (fa['min'] <= WE)].index
    smA = sm[sm.trip_id.isin(act)].merge(TALL[['trip_id', 'route_id']], on='trip_id')
    pat_rows = smA.groupby(['route_id', 'trip_id']).stop_id.apply(tuple).reset_index()
    trips_cnt = pat_rows.groupby('route_id').size()
    PATS = pat_rows.drop_duplicates(['route_id', 'stop_id']).rename(columns={'stop_id': 'seq'}).reset_index(drop=True)
    M_BY_STOP = {}
    for i, r in enumerate(PATS.itertuples()):
        for j, x in enumerate(r.seq): M_BY_STOP.setdefault(x, []).append((i, j))
    PSEQ = [dict((x, j) for j, x in enumerate(r.seq)) for r in PATS.itertuples()]; PROUTE = list(PATS.route_id)
    GG = pd.read_pickle(C/f'gg_{GGK}.pkl')
    REC = {}
    for _r in GG.itertuples(index=False):
        REC.setdefault(_r[1], []).append((_r[0], _r[2], _r[3], {h: getattr(_r, f'r{h}') for h in HOURS}))
    alls = stops.stop_id.values; ala = stops.la.values; alo = stops.lo.values
    _ns, _nk = {}, {}
    def NS(s):
        if s not in _ns: _ns[s] = set(alls[km(S.la[s], S.lo[s], ala, alo) <= 0.2]) if s in S.index else {s}
        return _ns[s]
    STC = ST.set_index('stop_id')
    def NK(k):
        if k not in _nk: _nk[k] = set(alls[km(STC.stop_lat[k], STC.stop_lon[k], ala, alo) <= 0.3]) if k in STC.index else set()
        return _nk[k]
    def maeul_routes(o_set, d_set):
        out = set()
        for x in o_set:
            for pi, j in M_BY_STOP.get(x, ()):
                p = PSEQ[pi]
                if any(p.get(y, -1) > j for y in d_set): out.add(PROUTE[pi])
        return out
    def gg_routes(a_set, b_set, h, min_runs=GG_MIN_RUNS):
        A, B = {}, {}
        for x in a_set:
            for rt, omin, omax, runs in REC.get(x, ()):
                if (runs[h] >= min_runs) if min_runs is not None else True: A[rt] = min(A.get(rt, 1e9), omin)
        if not A: return set()
        for x in b_set:
            for rt, omin, omax, runs in REC.get(x, ()):
                if rt in A: B[rt] = max(B.get(rt, -1), omax)
        return {rt for rt, o in A.items() if rt in B and o < B[rt]}

    # ---------------- 7. 수요(구 소유분, 도보 행 포함) ----------------
    VD = C/f'vec_{SPLIT}'
    X = pd.concat([pd.read_pickle(VD/f'access_{PFX}.pkl').assign(kind='access'), pd.read_pickle(VD/f'egress_{PFX}.pkl').assign(kind='egress')])
    stats['demand_raw'] = {'rows': len(X), 'flow_5day_avg': round(X.flow.sum()/5, 1), 'walkable_5day_avg': round(X[X.walkable == True].flow.sum()/5, 1)}
    X['station_stop_id'] = [DESIG.get((k, kd)) for k, kd in zip(X.station, X.kind)]
    nod = X.station_stop_id.isna()
    far = pd.Series([(k, kd) in FAR for k, kd in zip(X.station, X.kind)], index=X.index)
    stats['demand_drop_no_station_stop_5day_avg'] = round(X[nod & ~far & (X.walkable != True)].flow.sum()/5, 1)
    _f = X[far & (X.walkable != True)]
    stats['demand_drop_station_stop_too_far_5day_avg'] = {'all_routes': round(_f.flow.sum()/5, 1),
        'by_station': (_f.groupby(_f.station.map(lambda k: SNAME.get(k, k))).flow.sum()/5).round(1).to_dict()}
    X = X[~nod]; X = X[X.stop != X.station_stop_id]
    vec_gg = X[['kind', 'stop', 'station', 'hour', 'gg_reach']].drop_duplicates(['kind', 'stop', 'station', 'hour'])
    pairs = X[['kind', 'stop', 'station']].drop_duplicates()
    mr = []
    for kd, s_, k in zip(pairs.kind, pairs.stop, pairs.station):
        a_, b_ = (NS(s_), NK(k)) if kd == 'access' else (NK(k), NS(s_))
        mr.append(' '.join(sorted(maeul_routes(a_, b_))))
    pairs['maeul_routes'] = mr; pairs['maeul_reach'] = pairs.maeul_routes != ''
    ph = X[['kind', 'stop', 'station', 'hour']].drop_duplicates()
    gr = []
    for kd, s_, k, h in zip(ph.kind, ph.stop, ph.station, ph.hour):
        a_, b_ = (NS(s_), NK(k)) if kd == 'access' else (NK(k), NS(s_))
        gr.append(' '.join(sorted(gg_routes(a_, b_, int(h)))))
    ph['gg_routes'] = gr
    if GGK == SPLIT:
        ph['gg_reach'] = ph.gg_routes != ''
        q = ph.merge(vec_gg, on=['kind', 'stop', 'station', 'hour'], suffixes=('', '_vec'))
        stats['gg_reach_agree_with_seoul_vectors'] = round(float((q.gg_reach == (q.gg_reach_vec == True)).mean()), 5)
    else:
        # 근사: gg_reach는 서울 test 벡터(테스트 주간 hturn 판정) 그대로, 노선 목록은 실험 주간 hturn
        ph = ph.merge(vec_gg, on=['kind', 'stop', 'station', 'hour'], how='left'); ph['gg_reach'] = ph.gg_reach == True
        miss = ph.gg_reach & (ph.gg_routes == '')
        fb = []
        for kd, s_, k, h in zip(ph.kind[miss], ph.stop[miss], ph.station[miss], ph.hour[miss]):
            a_, b_ = (NS(s_), NK(k)) if kd == 'access' else (NK(k), NS(s_))
            r_ = gg_routes(a_, b_, int(h), 1e-9) or gg_routes(a_, b_, int(h), None)
            fb.append(' '.join(sorted(r_)))
        ph.loc[miss, 'gg_routes'] = fb
        ph.loc[~ph.gg_reach, 'gg_routes'] = ''
        approx['gg_routes'] = ('test 주간 hturn 없음. gg_reach = 서울 test 벡터 값(테스트 주간 hturn 판정). gg_routes = 실험 주간 hturn의 순서+그 시간 운행 1회 이상 노선 '
                               '(gg_reach 참인 행만). 참인데 그런 노선이 없으면 순서만 맞는 노선(운행>0, 없으면 운행 무관).')
        approx['_gg_rows'] = {'reach_true': int(ph.gg_reach.sum()), 'routes_from_fallback': int(miss.sum()),
                              'reach_true_but_no_route': int((ph.gg_reach & (ph.gg_routes == '')).sum())}
    X = X.drop(columns=[c_ for c_ in ['maeul_reach', 'gg_reach', 'gg_runs_h', 'f2g'] if c_ in X]).merge(pairs, on=['kind', 'stop', 'station'], how='left').merge(ph, on=['kind', 'stop', 'station', 'hour'], how='left')
    sh = [share(d_, s if kd == 'access' else ss, h) for d_, s, ss, kd, h in zip(X.date, X.stop, X.station_stop_id, X.kind, X.hour)]
    X['flow_affected'] = X.flow.values * np.array([x for x, _ in sh]); X['flow_unknown'] = X.flow.values * np.array([u for _, u in sh])
    X['in_network'] = X.stop.isin(nodes)
    F = X.groupby(['date', 'hour', 'kind', 'stop', 'station', 'station_stop_id', 'source'], as_index=False).agg(
        flow_total=('flow', 'sum'), flow=('flow_affected', 'sum'), flow_unknown=('flow_unknown', 'sum'),
        walkable=('walkable', 'first'), maeul_reach=('maeul_reach', 'first'), gg_reach=('gg_reach', 'first'),
        maeul_routes=('maeul_routes', 'first'), gg_routes=('gg_routes', 'first'), dist_km=('dist_km', 'first'), in_network=('in_network', 'first'))
    F['gu'] = GU_NAME; F['station_name'] = F.station.map(SNAME); F['stop_name'] = F.stop.map(S.stop_name)
    F['walkable'] = F.walkable == True
    for col in ['flow_total', 'flow', 'flow_unknown']: F[col] = F[col].round(6)
    F = F.rename(columns={'stop': 'stop_id', 'station': 'station_id'})
    (OUT/'demand').mkdir(exist_ok=True); F.to_csv(OUT/'demand'/'demand_flows.csv.gz', index=False)
    NW = F[~F.walkable]
    def avg(df): return round(df.flow.sum()/5, 1)
    stats['demand'] = {
        'funnel_5day_avg': {'all_routes_not_walkable': round(NW.flow_total.sum()/5, 1), 'affected': avg(NW),
                            'after_maeul': avg(NW[~NW.maeul_reach]), 'after_gg': avg(NW[~NW.gg_reach]),
                            'after_maeul_gg': avg(NW[~NW.maeul_reach & ~NW.gg_reach])},
        'by_kind_affected_5day_avg': (NW.groupby('kind').flow.sum()/5).round(1).to_dict(),
        'flow_unknown_5day_avg': round(NW.flow_unknown.sum()/5, 1),
        'flow_stop_not_in_network_5day_avg': avg(NW[~NW.in_network]),
        'by_hour_affected_5day_avg': {int(k): v for k, v in (NW.groupby('hour').flow.sum()/5).round(1).items()}}
    log(SLUG, SPLIT, 'demand', stats['demand']['funnel_5day_avg'])

    # ---------------- 8. 보조 자료 ----------------
    need = set(F.stop_id) | set(F.station_stop_id)
    lr = hp[hp['정류장_ID'].isin(need)].melt(id_vars=['기준_날짜', '노선_ID', '정류장_ID'], value_vars=HPCOLS, var_name='h', value_name='boardings')
    lr['hour'] = lr.h.str[-3:-1].astype(int); lr['boardings'] = pd.to_numeric(lr.boardings, errors='coerce').fillna(0)
    lr = lr[lr.boardings > 0].rename(columns={'기준_날짜': 'date', '노선_ID': 'route_id', '정류장_ID': 'stop_id'})
    if HPK != SPLIT:
        # 근사: 실험 주간 5일 평균 × (테스트 날짜 정류장 수요량 ÷ 실험 주간 평균), 정류장 수요량은 서울 벡터에서
        VE = pd.read_pickle(C/'vec_exp'/'stop_table.pkl'); VT = pd.read_pickle(C/f'vec_{SPLIT}'/'stop_table.pkl')
        ve = (VE.groupby(['stop', 'hour']).vol.sum()/5).rename('ve').reset_index()
        vt = VT[VT.stop.isin(need) | VT.stop.str[:3].eq(PFX)][['date', 'stop', 'hour', 'vol']].rename(columns={'vol': 'vt'})
        sc_ = vt.merge(ve, on=['stop', 'hour'], how='left'); sc_['scale'] = np.where(sc_.ve > 0, sc_.vt/sc_.ve.where(sc_.ve > 0, 1), np.nan)
        m5 = (lr.groupby(['stop_id', 'route_id', 'hour']).boardings.sum()/5).rename('b5').reset_index()
        grid = pd.DataFrame({'date': DATES}).merge(m5, how='cross')
        grid = grid.merge(sc_[['date', 'stop', 'hour', 'scale']].rename(columns={'stop': 'stop_id'}), on=['date', 'stop_id', 'hour'], how='left')
        n_noscale = int(grid.scale.isna().sum()); grid['scale'] = grid.scale.fillna(1.0)
        grid['boardings'] = (grid.b5*grid.scale).round(3)
        lr = grid[grid.boardings > 0][['date', 'hour', 'stop_id', 'route_id', 'boardings']]
        approx['stop_route_boardings'] = ('test 주간 hpasngr 없음. 실험 주간 정류장·노선·시간 승차 5일 평균 × (그 테스트 날짜 정류장 수요량 ÷ 실험 주간 평균). '
                                          '수요량 = 서울 벡터 access flow + local + no_rail(= TPSS 승차 × 날짜 무관 비율). 소수 3자리.')
        approx['_srb_rows_without_scale_used_1'] = n_noscale
        SCALE = sc_.groupby(['stop', 'hour']).apply(lambda q: q.vt.sum()/(5*q.ve.iloc[0]) if q.ve.iloc[0] > 0 else np.nan, include_groups=False).to_dict()
    lr['strike_state'] = lr.route_id.map(STATE_ALL).fillna('not_in_gtfs')
    lr[['date', 'hour', 'stop_id', 'route_id', 'boardings', 'strike_state']].sort_values(['date', 'hour', 'stop_id', 'route_id']).to_csv(OUT/'demand'/'stop_route_boardings.csv.gz', index=False)
    used_m = sorted(set(' '.join(F.maeul_routes.fillna('')).split())); used_g = sorted(set(' '.join(F.gg_routes.fillna('')).split()))
    RN = dict(zip(R_ALL.route_id, R_ALL.route_short_name))
    ab = []
    for r in used_m:
        seq = PATS[PATS.route_id == r].seq
        ab.append({'route_id': r, 'route_name': RN.get(r, ''), 'mode': 'maeul', 'order_known': True,
                   'stops': ' | '.join(' '.join(s) for s in seq), 'trips_0610': int(trips_cnt.get(r, 0))})
    for r in used_g:
        gx = GG[GG['노선_ID'] == r].sort_values('omin')
        ab.append({'route_id': r, 'route_name': '', 'mode': 'gg', 'order_known': True,
                   'stops': ' '.join(gx['정류장_ID']), 'trips_0610': round(float(gx[[f'r{h}' for h in HOURS]].max().sum()), 1)})
    pd.DataFrame(ab, columns=['route_id', 'route_name', 'mode', 'order_known', 'stops', 'trips_0610']).to_csv(OUT/'network'/'absorbing_routes.csv', index=False)
    stats['absorbing_routes'] = {'maeul': len(used_m), 'gg': len(used_g)}
    if GGK != SPLIT:
        approx['absorbing_routes_gg'] = 'test 주간 hturn 없음. 경기 노선 정류장 순서·06~09시 운행합(trips_0610)은 실험 주간(10/26~30) hturn.'
    STo = ST[ST.stop_id.isin(list(cand))][['stop_id', 'stop_name', 'stop_lat', 'stop_lon']].rename(columns={'stop_id': 'station_id', 'stop_name': 'station_name'})
    STo.to_csv(OUT/'network'/'stations.csv', index=False)
    dp = long[long['정류장_ID'].str[:3] == PFX].groupby(['정류장_ID', 'hour', 'state']).b.sum().unstack(fill_value=0)/5
    if HPK != SPLIT:
        sc2 = np.array([SCALE.get((s, h), np.nan) for s, h in dp.index]); sc2 = np.where(np.isnan(sc2), 1.0, sc2)
        dp = dp.mul(sc2, axis=0)
        approx['demand_points'] = 'test 주간 hpasngr 없음. 실험 주간 5일 평균 × (테스트 주간 정류장 수요량 합 ÷ 실험 주간), 정류장·시간별.'
    dp = dp.reset_index().rename(columns={'정류장_ID': 'stop_id'})
    dpw = dp.pivot_table(index='stop_id', columns='hour', values=[c_ for c_ in ['affected', 'unaffected', 'unknown', 'not_in_gtfs'] if c_ in dp], fill_value=0)
    dpw.columns = [f'w_{x}_h{h:02d}' for x, h in dpw.columns]; dpw = dpw.reset_index()
    dpw['w_total'] = dpw[[c_ for c_ in dpw.columns if c_.startswith('w_')]].sum(axis=1)
    dpw = dpw.merge(stops[['stop_id', 'stop_name', 'stop_lat', 'stop_lon']], on='stop_id', how='left'); dpw['gu'] = GU_NAME; dpw['n_days'] = 5
    dpw.round(2).to_csv(OUT/'demand'/'demand_points.csv', index=False)
    LN = pd.read_pickle(VD/f'local_norail_{PFX}.pkl')
    LN.rename(columns={'stop': 'stop_id'}).to_csv(OUT/'demand'/'local_norail.csv.gz', index=False)

    # ---------------- 9. 시나리오·수요 칸·run_inputs ----------------
    stop_area = {s: gu_of(s) for s in sorted(nodes | set(F.stop_id))}
    strike_ids = sorted(reg[reg.strike_state == 'affected'].route_id)
    def scenario(fleet, sid, dt='20261028'):
        return {'scenario_id': sid, 'service_date': f'{dt[:4]}-{dt[4:6]}-{dt[6:]}', 'timezone': 'Asia/Seoul', 'network_snapshot_id': NET,
                'demand_profile_id': PROFILE, 'strike_route_ids': strike_ids,
                'operating_window': {'start_s': WS, 'end_s': WE},
                'fleet': {'authorized_count': fleet, 'vehicles': [{'vehicle_id': f'V{i:03d}', 'vehicle_class': 'charter45', 'seat_capacity': 45,
                          'standing_capacity': 0, 'available_start_s': WS, 'available_end_s': WE + 3600, 'initial_stop_id': None,
                          'end_stop_id': None} for i in range(1, fleet+1)]},
                'policy': POLICY, 'stop_area': stop_area}
    VARIANTS = {'affected': (False, False), 'affected_maeul': (True, False), 'affected_gg': (False, True), 'affected_maeul_gg': (True, True)}
    shutil.rmtree(OUT/'bundles', ignore_errors=True)
    bundles = []
    sc0 = sio.scenario_from_dict(scenario(FLEETS[0], 'tmp'))
    for var, (mo, go) in VARIANTS.items():
        for dt in DATES:
            bdir = OUT/'bundles'/f'{var}_{dt}'; bdir.mkdir(parents=True, exist_ok=True)
            x = NW[NW.date == dt]
            if mo: x = x[~x.maeul_reach]
            if go: x = x[~x.gg_reach]
            flows = [DemandFlow(r.kind, r.stop_id, r.station_stop_id, int(r.hour)*3600, f'{r.flow:.6f}') for r in x.itertuples() if r.flow > 0]
            shutil.copy(OUT/'network'/'travel_times.json', bdir/'travel_times.json')
            for fleet in FLEETS:
                sio.write_bytes(bdir/f'scenario_f{fleet}.json', sio.json_bytes(scenario(fleet, f'sc-{SLUG}-0610-{SPLIT}-{var}-{dt}-f{fleet}', dt)))
            bins = realize_bins(flows, sc0.policy, sc0.window, SEED, BIN)
            write_demand_bins(bins, bdir/'demand_bins.csv')
            for fleet in FLEETS:
                build_run_inputs(f'bundle-{SLUG}-0610-{SPLIT}-{var}-{dt}-f{fleet}', bdir/f'scenario_f{fleet}.json', bdir/'demand_bins.csv',
                                 bdir/'travel_times.json', SEED, bdir/f'run_inputs_f{fleet}.json')
            bundles.append({'bundle': f'{var}_{dt}', 'flow_sum': round(sum(float(f.flow) for f in flows), 1),
                            'passengers': sum(b.passenger_count for b in bins), 'bin_rows': len(bins)})
    stats['bundles'] = bundles

    # ---------------- 10. 출처·통계·README ----------------
    VEC = SEO/'demand'/SPLIT
    srcs = [('bus_gtfs', GTFS/'stop_times.txt', '서울 버스 GTFS 근사(운행일 11/02, 06~10시) v1.1. routes·trips·stops 같은 폴더', '팀 자료 배포 02 (busstrike_data_release_20261111)'),
            ('segment_traveltime', a.segtt, '노선·구간·시간대별 운행시간 평일 중앙값 2026-09-21~10-25(21일)', '팀 자료 배포 04, 원자료 서울 OA-21217'),
            ('demand_vectors', VEC/'access_vectors_seoul.csv.gz', f'정류장↔역 수요 벡터 06~10시({DATES[0]}~{DATES[-1]})', 'seoul_routegen_inputs_0610_v4 (seoul_vec_v4.py + gg_mask_v2.py 출력, egress·local_norail 같은 폴더)'),
            ('boardings', a.hp_exp if HPK == 'exp' else a.hp_test, 'TPSS 정류장·노선별 시간대 승차' + (f' (사용 날짜 {DATES[0]}~{DATES[-1]})' if HPK == SPLIT else ' 실험 주간 10/26~30 (테스트 주간 근사용)'), 'TPSS hpasngr OA-21219'),
            ('transfers', SEO/'patterns'/'transfer_k5.csv', '버스↔지하철 환승 집계 (실험 주간 10/26~30, 06~09시 합산, 5명 미만 칸 제거)', 'TAIMS 교통카드 집계(seoul_transit_data/patterns), 카드 ID 없음'),
            ('gg_runs', a.hturn_exp if GGK == 'exp' else a.hturn_test, '경기버스 정류장 순서·시간대 운행횟수 (평일 평균)' + ('' if GGK == SPLIT else ', 실험 주간(테스트 주간 근사용)'), 'TPSS hturn OA-21220'),
            ('stations', SEO/'network'/'stations.csv', '철도역 목록·좌표 (서울 입력 stations.csv + 09 철도 GTFS stops.txt; 기준 묶음의 stations_all.csv 대신)', 'seoul_routegen_inputs_0610_v4, 팀 GTFS 철도(09)'),
            ('gu_map', SEO/'district_fleet.csv', '정류장 ID 앞 3자리 → 자치구 (stop_prefix 열; 기준 묶음의 xlsx 대신)', 'seoul_routegen_inputs_0610_v4')]
    pd.DataFrame([{'name': n_, 'file': Path(p).name, 'bytes': os.path.getsize(p), 'sha256': sha(p), 'content': c_, 'origin': o_}
                  for n_, p, c_, o_ in srcs]).to_csv(OUT/'sources.csv', index=False)
    stats['split'] = {'name': SPLIT, 'dates': DATES, 'demand_profile_id': PROFILE, 'station_stop_pattern_date': PAT}
    stats['exact_inputs'] = bool(EXACT)
    stats['approximations'] = approx
    stats['fleet_rule'] = a.fleet_rule
    json.dump(stats, open(OUT/'build_stats.json', 'w'), ensure_ascii=False, indent=1, default=lambda o: o.item() if hasattr(o, 'item') else str(o))
    (OUT/'scripts').mkdir(exist_ok=True)
    for f in ('build_region.py', 'smoke.py', 'set_fleets.py'):
        if (HERE/f).exists(): shutil.copy(HERE/f, OUT/'scripts'/f)
    write_readme(OUT, stats, a)
    log(SLUG, SPLIT, 'done', OUT)


def write_readme(OUT, stats, a):
    sp = stats['split']['name']; fl = stats['fleets']; fu = stats['demand']['funnel_5day_avg']
    week = '10/26~30 (실험)' if sp == 'exp' else '11/02~06 (테스트)'
    hw = '10/26~30' if sp == 'exp' else '11/02~06'
    B = f'bundles/affected_{stats["split"]["dates"][2]}'
    far = sorted({x['station_name'] for x in stats['station_stops']['too_far']})
    ap = {k: v for k, v in stats['approximations'].items() if not k.startswith('_')}
    L = [f'# {a.gu} 06~10시 시뮬레이터 입력 묶음 v4 ({"실험" if sp == "exp" else "테스트"})', '',
         f'M05(`busstrike.sim`)로 바로 돌릴 수 있는 {a.gu} 입력이다. 수요 날짜 {week}, 운영·측정 06:00~10:00, 차량 가용 11:00까지, 대수 {"·".join(map(str, fl))}대.',
         '데이터 파일은 Git에 올리지 않는다. 카드 ID는 없고 모두 집계값이다.', '',
         '`experiments/region_build/build_region.py`(guro_build.py v4를 구 일반화·CLI로 옮긴 것)로 만들었다. 폴더 형식·열·규칙은 zip_v4/region의 구로·강동·강서·동작 v4 묶음과 같다.', '',
         '## v3 → v4 바뀐 점',
         '- **수요 비율**: 11/02(테스트 주간) 공개 OD·환승 → **실험 주간 10/26~30 교통카드 집계**(5일·06~09시 합산, 5명 미만 칸 제거 후 비례 재분배). 테스트 묶음도 같은 실험 주간 비율 × 그 주 TPSS 승차라서 테스트 자료가 비율에 섞이지 않는다.',
         '- **역 지정 정류장**: 실험 주간 환승 최다 정류장 (`network/station_stops.csv`의 `transfers_exp5_0609`).',
         f'- **경기버스 흡수(`gg_reach`)**: 순서(타는 쪽 → 내리는 쪽) + 그 시간대 평일 평균 운행 1회 이상 (TPSS `hturn`, {hw}). 판정이 시간대마다 다를 수 있다. `network/absorbing_routes.csv`에 경기 노선 정류장 순서와 06~09시 운행 합을 남김.'
         + (' (테스트 주간 hturn 원본이 없어 판정은 서울 test 벡터 값, 노선 목록·순서·운행합은 실험 주간 hturn — 아래 근사 절)' if 'gg_routes' in stats['approximations'] else ''),
         f'- 망 ID `{a.slug}-buf2km-0610-v2`, 구간시간 만드는 법은 v3와 같다(04 실측 중앙값).', '',
         '## 5일 평균 셔틀 대상 (도보 가능 제외, 파업 노선 승객)', '| 변형 | 마을버스 | 경기버스 | 하루 평균 |', '|---|---|---|---:|',
         f'| affected | | | {fu["affected"]:,.1f} |', f'| affected_maeul | ✓ | | {fu["after_maeul"]:,.1f} |',
         f'| affected_gg | | ✓ | {fu["after_gg"]:,.1f} |', f'| affected_maeul_gg | ✓ | ✓ | {fu["after_maeul_gg"]:,.1f} |', '',
         '## 망·역', f'- 구간 {stats["travel_times"]["segments"]:,}개, 칸 출처 {stats["travel_times"]["profile_cells"]}',
         f'- 역 지정 정류장 규칙 {stats["station_stops"]["rules"]}, 400m 초과로 제외한 역: {", ".join(far) if far else "없음"}',
         f'- 흡수 노선: 마을 {stats["absorbing_routes"]["maeul"]}개, 경기 {stats["absorbing_routes"]["gg"]}개', '',
         '## 대수', f'- {a.fleet_rule}',
         '- 대수만 바꿀 때: `python3 scripts/set_fleets.py --root . --fleets a,b` (수요·망은 그대로, scenario_f*·run_inputs_f*만 다시 만든다).', '']
    if ap:
        L += ['## 근사한 칸 (이 PC에 테스트 주간 TPSS hpasngr·hturn 11/02~08이 없음)'] + [f'- `{k}`: {v}' for k, v in ap.items()] + \
             ['- 건수·비교는 `build_stats.json`의 `approximations`. 실험 주간 묶음은 기준 묶음과 같은 원자료라 근사가 없다.', '']
    else:
        L += ['## 원자료', '- 이 묶음은 근사 없이 기준 묶음(guro_build.py)과 같은 원자료로 만들었다. 차이: 역 좌표는 서울 입력 stations.csv + 철도 GTFS, 구 이름표는 district_fleet.csv의 stop_prefix(`sources.csv`).', '']
    L += ['## 실행 확인', '- `smoke/`: 스모크 계획으로 변환기 → M05 → M07을 돌린 결과(`scripts/smoke.py`).', '',
          '## 실행', '```sh', f'B={B}',
          f'PYTHONPATH=<repo>/src python3 -m busstrike.sim.adapters.comparison_plan --plan <plan.json> \\',
          f'    --scenario $B/scenario_f{fl[0]}.json --travel-times $B/travel_times.json --out plan_m05.json',
          f'PYTHONPATH=<repo>/src python3 -m busstrike.sim run --scenario $B/scenario_f{fl[0]}.json \\',
          f'    --run-inputs $B/run_inputs_f{fl[0]}.json --plan plan_m05.json --out out/',
          'PYTHONPATH=<repo>/src python3 -m busstrike.evaluation.verify out/run --max-recompute 200000', '```', '',
          '폴더 구성과 열 설명은 v3 README와 같다(`gtfs/`, `network/`, `registry/`, `demand/`, `bundles/`, `smoke/`, `scripts/`, `sources.csv`, `build_stats.json`).']
    (OUT/'README.md').write_text('\n'.join(L) + '\n', encoding='utf-8')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--prepare', action='store_true')
    ap.add_argument('--slug'); ap.add_argument('--gu'); ap.add_argument('--prefix')
    ap.add_argument('--split', choices=['exp', 'test'], default='exp')
    ap.add_argument('--fleets', default='11,13'); ap.add_argument('--fleet-rule', default='대수는 실행할 때 지정한 값.')
    ap.add_argument('--out'); ap.add_argument('--buf-km', type=float, default=2.0)
    ap.add_argument('--simsrc', default=str(REPO/'src'))
    for k, v in DEF.items(): ap.add_argument('--' + k.replace('_', '-'), default=str(v) if v else None)
    a = ap.parse_args()
    if a.prepare or not (Path(a.cache)/'vec_test'/'done').exists(): prepare(a)
    if a.prepare and not a.slug: return
    if not re.match(r'^[a-z]+$', a.slug or ''): raise SystemExit('--slug는 영문 소문자만 (망 ID 규칙 <구>-buf2km-0610-v2)')
    build(a)


import re
if __name__ == '__main__':
    main()
