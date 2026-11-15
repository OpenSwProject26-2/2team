"""변조 사본을 만드는 도우미. 4단계 T1–T20이 이것을 재사용한다.

정상 산출물을 임시 디렉터리에 복사해 **한 가지만** 바꾼다. 원본은 건드리지 않는다.
"""
import contextlib
import csv
import hashlib
import io
import json
from pathlib import Path
import shutil
import tempfile

FIXTURES = Path(__file__).resolve().parent / 'fixtures'


@contextlib.contextmanager
def tampered(fixture, *mutations):
    """fixture 사본을 만들어 mutations를 차례로 적용하고 경로를 넘긴다."""
    with tempfile.TemporaryDirectory(prefix='busstrike-tamper-') as tmp:
        base = Path(tmp) / fixture
        shutil.copytree(FIXTURES / fixture, base)
        for mutate in mutations:
            mutate(base)
        yield base


# ---------- 사건 로그 ----------

def read_events(base):
    return [json.loads(line) for line in
            (base / 'run/events.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]


def write_events(base, rows):
    (base / 'run/events.jsonl').write_text(
        ''.join(json.dumps(r, sort_keys=True) + '\n' for r in rows), encoding='utf-8')


def edit_events(fn):
    """사건 목록 전체를 받아 새 목록을 돌려주는 함수로 로그를 바꾼다."""
    def mutate(base):
        write_events(base, fn(read_events(base)))
    return mutate


def first_index(rows, **match):
    """match 조건을 모두 만족하는 첫 사건의 위치. 없으면 AssertionError."""
    for i, row in enumerate(rows):
        if all(row.get(k) == v for k, v in match.items()):
            return i
    raise AssertionError(f'해당 사건이 없다: {match}')


def drop_event(**match):
    def fn(rows):
        i = first_index(rows, **match)
        return rows[:i] + rows[i + 1:]
    return edit_events(fn)


def duplicate_event(**match):
    def fn(rows):
        i = first_index(rows, **match)
        return rows[:i + 1] + [dict(rows[i])] + rows[i + 1:]
    return edit_events(fn)


def change_event(field, value, **match):
    def fn(rows):
        i = first_index(rows, **match)
        rows = [dict(r) for r in rows]
        rows[i][field] = value
        return rows
    return edit_events(fn)


def shift_event_time(delta, **match):
    def fn(rows):
        i = first_index(rows, **match)
        rows = [dict(r) for r in rows]
        rows[i]['time_s'] += delta
        return rows
    return edit_events(fn)


def swap_events(index):
    """index와 index+1 사건의 줄 순서를 바꾼다."""
    def fn(rows):
        return rows[:index] + [rows[index + 1], rows[index]] + rows[index + 2:]
    return edit_events(fn)


def append_event(**fields):
    def fn(rows):
        return rows + [{**rows[-1], **fields}]
    return edit_events(fn)


# ---------- 승객 결과 CSV ----------

def read_outcomes(base):
    text = (base / 'run/passenger_outcomes.csv').read_text(encoding='utf-8')
    reader = csv.DictReader(io.StringIO(text))
    return list(reader.fieldnames), list(reader)


def write_outcomes(base, header, rows):
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, header, lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    (base / 'run/passenger_outcomes.csv').write_text(buffer.getvalue(), encoding='utf-8')


def edit_outcomes(fn):
    def mutate(base):
        header, rows = read_outcomes(base)
        write_outcomes(base, header, fn(rows))
    return mutate


def change_outcome(passenger_id, **fields):
    def fn(rows):
        for row in rows:
            if row['passenger_id'] == passenger_id:
                row.update({k: str(v) for k, v in fields.items()})
                return rows
        raise AssertionError(f'승객이 없다: {passenger_id}')
    return edit_outcomes(fn)


def drop_outcome(passenger_id):
    def fn(rows):
        kept = [r for r in rows if r['passenger_id'] != passenger_id]
        assert len(kept) == len(rows) - 1, f'승객이 없다: {passenger_id}'
        return kept
    return edit_outcomes(fn)


# ---------- JSON 파일 ----------

def edit_json(relative, fn):
    def mutate(base):
        path = base / relative
        payload = json.loads(path.read_text(encoding='utf-8'))
        path.write_text(json.dumps(fn(payload), indent=2, sort_keys=True, ensure_ascii=False),
                        encoding='utf-8')
    return mutate


def set_metric(path, value):
    """'M1.served'처럼 점으로 구분한 경로의 값을 바꾼다."""
    keys = path.split('.')

    def fn(payload):
        node = payload
        for key in keys[:-1]:
            node = node[key]
        node[keys[-1]] = value
        return payload
    return edit_json('run/metrics.json', fn)


def bump_metric(path, delta):
    keys = path.split('.')

    def fn(payload):
        node = payload
        for key in keys[:-1]:
            node = node[key]
        node[keys[-1]] = node[keys[-1]] + delta
        return payload
    return edit_json('run/metrics.json', fn)


# ---------- 일관된 위조 (T9) ----------

def recompute_occupancy(rows):
    """남은 사건으로 occupancy를 다시 채워 로그를 자기 정합적으로 만든다.

    T9("자격 승객 승차를 삭제하고 이후 사건을 일관되게 수정")처럼 V5·V8을 통과하는
    위조를 만들 때 쓴다. 검증기가 그래도 V6로 잡아야 한다.
    """
    onboard = {}
    out = []
    for row in rows:
        row = dict(row)
        vehicle = row.get('vehicle_id')
        if row['kind'] == 'boarded':
            onboard[vehicle] = onboard.get(vehicle, 0) + 1
        elif row['kind'] == 'alighted':
            onboard[vehicle] = onboard.get(vehicle, 0) - 1
        if row['kind'] == 'generated' or (row['kind'] == 'expired' and vehicle is None):
            row['occupancy'] = None
        else:
            row['occupancy'] = onboard.get(vehicle, 0)
        out.append(row)
    return out


def remove_ride(passenger_id, **outcome_fields):
    """승객의 승차·하차 사건을 지우고 로그·CSV를 자기 정합적으로 되돌린다.

    남는 모순은 "빈 좌석이 있는데 자격 승객이 타지 않았다"뿐이다(V6).
    """
    def events(rows):
        kept = []
        for row in rows:
            if row.get('passenger_id') == passenger_id and row['kind'] in ('boarded', 'alighted'):
                continue
            if row.get('passenger_id') == passenger_id and row['kind'] == 'expired':
                row = {**row, 'vehicle_id': None}
            kept.append(row)
        return recompute_occupancy(kept)

    def mutate(base):
        edit_events(events)(base)
        change_outcome(passenger_id, **outcome_fields)(base)
    return mutate


def change_visit(stable_id, **match):
    """사건의 방문(stable_id)을 다른 방문으로 바꾼다 (T7·T8)."""
    return change_event('stable_id', stable_id, **match)


# ---------- 시나리오 파일 ----------

def edit_demand_bins(fn):
    """demand_bins.csv의 행 dict 목록을 바꾼다 (T14)."""
    def mutate(base):
        path = base / 'scenario/demand_bins.csv'
        reader = csv.DictReader(io.StringIO(path.read_text(encoding='utf-8')))
        header, rows = list(reader.fieldnames), fn(list(reader))
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, header, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)
        path.write_text(buffer.getvalue(), encoding='utf-8')
    return mutate


def bump_bin_count(aggregate_id, delta):
    def fn(rows):
        for row in rows:
            if row['aggregate_id'] == aggregate_id:
                row['passenger_count'] = str(int(row['passenger_count']) + delta)
                return rows
        raise AssertionError(f'집계 행이 없다: {aggregate_id}')
    return edit_demand_bins(fn)


# ---------- digest까지 다시 맞춘 위조 ----------

def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def repair_digests(base):
    """변조 후 manifest·measurement의 파일 digest를 다시 계산해 V17을 통과시킨다.

    V17은 파일을 조금이라도 건드리면 걸린다. 그래서 V17만으로 "검출됐다"고 하면 개별
    검사가 고장나도 모른다. 이 보정을 적용한 사본에서도 해당 검사가 잡는지를 확인해야
    T1–T20 검출이 실제로 의미를 갖는다. 다른 모든 변조 뒤에 **마지막으로** 적용한다.
    """
    scenario, run = base / 'scenario', base / 'run'
    manifest_path = scenario / 'scenario_manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest['sha256'] = {name: _sha256(scenario / name)
                          for name in sorted(manifest.get('sha256') or {})
                          if (scenario / name).is_file()}
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding='utf-8')

    measurement_path = run / 'measurement.json'
    measurement = json.loads(measurement_path.read_text(encoding='utf-8'))
    for name, digest_field, bytes_field in (
            ('events.jsonl', 'log_sha256', 'log_bytes'),
            ('passenger_outcomes.csv', 'outcome_sha256', 'outcome_bytes'),
            ('metrics.json', 'metrics_sha256', None),
            ('realization.jsonl', 'realization_sha256', None)):
        path = run / name
        if digest_field in measurement and path.is_file():
            measurement[digest_field] = _sha256(path)
            if bytes_field is not None and bytes_field in measurement:
                measurement[bytes_field] = path.stat().st_size
    if isinstance(measurement.get('scenario_manifest'), dict):
        measurement['scenario_manifest']['sha256'] = manifest['sha256']
    if (run / 'realization.jsonl').is_file():
        with open(run / 'realization.jsonl', encoding='utf-8') as f:
            meta = json.loads(f.readline())
        if 'realization_checksum' in measurement and 'checksum' in meta:
            measurement['realization_checksum'] = meta['checksum']
    measurement_path.write_text(json.dumps(measurement, indent=2, sort_keys=True),
                                encoding='utf-8')
