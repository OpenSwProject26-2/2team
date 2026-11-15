"""변조 사본을 만드는 도우미. 4단계 T1–T20이 이것을 재사용한다.

정상 산출물을 임시 디렉터리에 복사해 **한 가지만** 바꾼다. 원본은 건드리지 않는다.
"""
import contextlib
import csv
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
