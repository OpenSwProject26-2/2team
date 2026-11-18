# 이미 만든 구 묶음의 대수만 바꾼다(수요·망은 그대로).
#   PYTHONPATH=<repo>/src python3 set_fleets.py --root <묶음 루트> --fleets a,b [--basis "<대수 근거 한 줄>"]
# 바꾸는 것: bundles/*/scenario_f{F}.json, run_inputs_f{F}.json(예전 대수 파일은 지움), README의 대수 줄·실행 예시·"## 대수" 절,
#           build_stats.json의 fleets·fleet_rule. demand_bins.csv·travel_times.json은 건드리지 않는다(대수와 무관).
# zip_v4/region의 기준 묶음(guro_build.py 출력)에도 쓸 수 있다 — 반드시 복사본에 돌린다.
import argparse, json, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for p in (HERE.parents[1]/'src', HERE.parent/'src'):
    if (p/'busstrike').exists(): sys.path.insert(0, str(p))
from busstrike.sim import io as sio                       # noqa: E402
from busstrike.sim.arrivals import build_run_inputs       # noqa: E402


def set_fleets(root: Path, fleets: list[int], basis: str | None = None) -> None:
    root = Path(root)
    bdirs = sorted(p for p in (root/'bundles').iterdir() if p.is_dir())
    for b in bdirs:
        olds = sorted(b.glob('scenario_f*.json'))
        if not olds: raise SystemExit(f'{b}: scenario_f*.json 없음')
        tmpl = json.loads(olds[0].read_text(encoding='utf-8'))
        ri = json.loads((b/olds[0].name.replace('scenario_', 'run_inputs_')).read_text(encoding='utf-8'))
        seed = ri['seed']; bid0 = ri['input_bundle_id']
        v0 = tmpl['fleet']['vehicles'][0]
        for p in list(b.glob('scenario_f*.json')) + list(b.glob('run_inputs_f*.json')): p.unlink()
        for F in fleets:
            sc = dict(tmpl)
            sc['scenario_id'] = re.sub(r'-f\d+$', f'-f{F}', tmpl['scenario_id'])
            sc['fleet'] = {'authorized_count': F, 'vehicles': [{**v0, 'vehicle_id': f'V{i:03d}'} for i in range(1, F+1)]}
            sio.write_bytes(b/f'scenario_f{F}.json', sio.json_bytes(sc))
            build_run_inputs(re.sub(r'-f\d+$', f'-f{F}', bid0), b/f'scenario_f{F}.json', b/'demand_bins.csv', b/'travel_times.json', seed, b/f'run_inputs_f{F}.json')
    fs = '·'.join(map(str, fleets))
    rd = root/'README.md'
    if rd.exists():
        t = rd.read_text(encoding='utf-8')
        t = re.sub(r'대수 \d+(?:·\d+)*대', f'대수 {fs}대', t, count=1)
        t = re.sub(r'(scenario|run_inputs)_f\d+\.json', lambda m: f'{m.group(1)}_f{fleets[0]}.json', t)
        t = re.sub(r'스모크 계획\((\d+)노선·\d+대\)', lambda m: f'스모크 계획({m.group(1)}노선·{fleets[0]}대)', t)
        t = re.sub(r'묶음 (\d+)개\(변형 4 × 날짜 5 × 대수 \d+\)', lambda m: f'묶음 {len(bdirs)*len(fleets)}개(변형 4 × 날짜 5 × 대수 {len(fleets)})', t)
        sec = ['## 대수', f'- {basis}' if basis else f'- 대수 {fs}대 (set_fleets.py로 바꿈).',
               '- 대수만 바꿀 때: `python3 scripts/set_fleets.py --root . --fleets a,b` (수요·망은 그대로, scenario_f*·run_inputs_f*만 다시 만든다).', '']
        if '## 대수' in t:
            t = re.sub(r'## 대수\n(?:- .*\n)*\n?', '\n'.join(sec) + '\n', t, count=1)
        else:
            t = t.replace('\n## 실행 확인', '\n' + '\n'.join(sec) + '\n## 실행 확인', 1) if '\n## 실행 확인' in t else t + '\n' + '\n'.join(sec)
        rd.write_text(t, encoding='utf-8')
    bs = root/'build_stats.json'
    if bs.exists():
        s = json.loads(bs.read_text(encoding='utf-8'))
        s['fleets'] = fleets
        if basis: s['fleet_rule'] = basis
        bs.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding='utf-8')
    sd = root/'scripts'
    if sd.is_dir() and Path(__file__).resolve() != (sd/'set_fleets.py').resolve():
        (sd/'set_fleets.py').write_bytes(Path(__file__).read_bytes())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', required=True); ap.add_argument('--fleets', required=True); ap.add_argument('--basis')
    a = ap.parse_args()
    fleets = sorted({int(x) for x in a.fleets.split(',') if x.strip()})
    set_fleets(Path(a.root), fleets, a.basis)
    print('OK', a.root, fleets)


if __name__ == '__main__':
    main()
