# 서울 25개 구 묶음(exp·test)을 병렬로 만들고, 끝날 때마다 스모크(변환기 → M05 → M07)를 돌려 READY.txt에 한 줄씩 적는다.
#   ~/.venvs/region_build/bin/python run_all.py [--out <region_all>] [--jobs 6] [--only guro,gangdong] [--no-ref]
# 순서: 기준 묶음이 있는 4개 구(구로·강동·강서·동작)를 먼저 만들고 기준과 비교(validation/), 기준 묶음 복사본(ref/)에 대수 적용, 나머지 21개 구.
# 대수: district_fleet_research.csv에서 {jan_am(없으면 am_fleet_best), am_fleet_best}의 정렬된 고유값.
import argparse, csv, json, os, shutil, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PROJ = Path('/mnt/c/Users/ddonggojin/orca/projects')
SEO = PROJ/'guro_exp_work/zip_v4/seoul/seoul_routegen_inputs_0610_v4'
REF = PROJ/'guro_exp_work/zip_v4/region'
RESEARCH = PROJ/'guro_exp_work/district_fleet_research.csv'
SLUG = {'종로구': 'jongno', '중구': 'jung', '용산구': 'yongsan', '성동구': 'seongdong', '광진구': 'gwangjin', '동대문구': 'dongdaemun',
        '중랑구': 'jungnang', '성북구': 'seongbuk', '강북구': 'gangbuk', '도봉구': 'dobong', '노원구': 'nowon', '은평구': 'eunpyeong',
        '서대문구': 'seodaemun', '마포구': 'mapo', '양천구': 'yangcheon', '강서구': 'gangseo', '구로구': 'guro', '금천구': 'geumcheon',
        '영등포구': 'yeongdeungpo', '동작구': 'dongjak', '관악구': 'gwanak', '서초구': 'seocho', '강남구': 'gangnam', '송파구': 'songpa',
        '강동구': 'gangdong'}
REF_GUS = ['guro', 'gangdong', 'gangseo', 'dongjak']
LOCK = Lock()


def fleets_for(row):
    best = int(float(row['am_fleet_best']))
    jan = row.get('jan_am', '').strip()
    A = int(float(jan)) if jan else best
    fl = sorted({A, best})
    basis = (f'대수 {"·".join(map(str, fl))}대 = {{1월 실운행 오전 jan_am={jan or "없음→am_fleet_best"}, 9월 계획 오전 am_fleet_best={best}}}. '
             f'jan_am = 2026-01 파업 때 실제 운행한 오전 대수(서울시 1월 자치구별 운행계획), am_fleet_best = 9월 계획(9.16 파업은 당일 철회되어 미운행). '
             f'근거 district_fleet_research.csv (basis={row.get("basis", "")}).')
    return fl, basis


def ready(out, line):
    with LOCK:
        with open(out/'READY.txt', 'a', encoding='utf-8') as f: f.write(line + '\n')
    print('READY', line, flush=True)


def sh(cmd, log):
    with open(log, 'a', encoding='utf-8') as f:
        f.write(f'\n$ {" ".join(map(str, cmd))}\n'); f.flush()
        p = subprocess.run(list(map(str, cmd)), stdout=f, stderr=subprocess.STDOUT, env={**os.environ, 'PYTHONPATH': str(REPO/'src')})
    return p.returncode


def last_line(log, key):
    lines = [l for l in Path(log).read_text(encoding='utf-8', errors='replace').splitlines() if l.startswith(key)]
    return lines[-1] if lines else ''


def guarded(fn, tag, out, *args):
    try: fn(out, *args)
    except Exception as e: ready(out, f'{tag} FAIL exception {type(e).__name__}: {str(e)[:200]}')


def job(out, gu, slug, prefix, split, fleets, basis, py):
    suf = '' if split == 'exp' else '_test'
    root = out/f'{slug}_0610_v4{suf}'; logs = out/'logs'; logs.mkdir(exist_ok=True)
    log = logs/f'{slug}_{split}.log'; log.write_text('', encoding='utf-8')
    t0 = time.time()
    rc = sh([py, HERE/'build_region.py', '--slug', slug, '--gu', gu, '--prefix', prefix, '--split', split,
             '--fleets', ','.join(map(str, fleets)), '--fleet-rule', basis, '--out', root], log)
    if rc:
        tail = [l for l in log.read_text(encoding='utf-8').splitlines() if l.strip()][-1:]
        ready(out, f'{slug} {split} FAIL build rc={rc} {tail[0][:200] if tail else ""}'); return
    rc = sh([sys.executable, HERE/'smoke.py', root], log)
    if rc:
        ready(out, f'{slug} {split} FAIL {last_line(log, "SMOKE FAIL")[11:][:200]}'); return
    if slug in REF_GUS:
        refroot = REF/f'{slug}_0610_v4{suf}'
        vd = out/'validation'; vd.mkdir(exist_ok=True)
        sh([py, HERE/'validate.py', '--mine', root, '--ref', refroot, '--json', vd/f'{slug}_{split}.json'], vd/f'{slug}_{split}.txt')
    ready(out, f'{slug} {split} OK')
    print(slug, split, f'{time.time()-t0:.0f}s', flush=True)


def ref_job(out, slug, split, fleets, basis):
    suf = '' if split == 'exp' else '_test'
    src = REF/f'{slug}_0610_v4{suf}'; dst = out/'ref'/f'{slug}_0610_v4{suf}'
    log = out/'logs'/f'ref_{slug}_{split}.log'; log.write_text('', encoding='utf-8')
    if dst.exists(): shutil.rmtree(dst)
    shutil.copytree(src, dst)
    rc = sh([sys.executable, HERE/'set_fleets.py', '--root', dst, '--fleets', ','.join(map(str, fleets)), '--basis', basis], log)
    if rc: ready(out, f'ref:{slug} {split} FAIL set_fleets rc={rc}'); return
    rc = sh([sys.executable, HERE/'smoke.py', dst], log)
    ready(out, f'ref:{slug} {split} OK' if rc == 0 else f'ref:{slug} {split} FAIL {last_line(log, "SMOKE FAIL")[11:][:200]}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=str(PROJ/'guro_exp_work/region_all'))
    ap.add_argument('--jobs', type=int, default=6); ap.add_argument('--only'); ap.add_argument('--no-ref', action='store_true')
    ap.add_argument('--splits', default='exp,test')
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True); (out/'logs').mkdir(exist_ok=True)
    py = sys.executable
    pfx = {r['gu']: r['stop_prefix'] for r in csv.DictReader(open(SEO/'district_fleet.csv', encoding='utf-8'))}
    res = {r['gu']: r for r in csv.DictReader(open(RESEARCH, encoding='utf-8-sig'))}
    plan = []
    for gu, slug in SLUG.items():
        fl, basis = fleets_for(res[gu]); plan.append((gu, slug, pfx[gu], fl, basis))
    plan.sort(key=lambda x: (x[1] not in REF_GUS, REF_GUS.index(x[1]) if x[1] in REF_GUS else 0, x[1]))
    if a.only: plan = [p for p in plan if p[1] in a.only.split(',')]
    json.dump([{'gu': g, 'slug': s, 'prefix': p, 'fleets': f, 'basis': b} for g, s, p, f, b in plan], open(out/'plan.json', 'w'), ensure_ascii=False, indent=1)
    subprocess.run([py, HERE/'build_region.py', '--prepare'], check=True)
    splits = a.splits.split(',')
    with ThreadPoolExecutor(a.jobs) as ex:
        futs = []
        for gu, slug, prefix, fl, basis in plan:
            for sp in splits:
                futs.append(ex.submit(guarded, job, f'{slug} {sp}', out, gu, slug, prefix, sp, fl, basis, py))
            if slug in REF_GUS and not a.no_ref:
                for sp in splits: futs.append(ex.submit(guarded, ref_job, f'ref:{slug} {sp}', out, slug, sp, fl, basis))
        for f in as_completed(futs):
            if f.exception(): print('ERROR', f.exception(), flush=True)
    print('ALL DONE')


if __name__ == '__main__':
    main()
