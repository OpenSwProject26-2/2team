"""V1 R5 발생 생성기 독립 재구현. 근거는 명세 §3 V1 행 하나뿐이다.

엔진의 `sim/arrivals.py`를 읽거나 import하지 않는다. 집계 수요 행(`demand_bins.csv`)과
seed만으로 발생을 다시 만들어 `realization.jsonl`과 비교한다. 명세 §2가 말하는 (b) 경로이며
(a)보다 독립성이 높다. `realization.jsonl`이 없으면 이것이 유일한 발생 출처가 된다.

명세 문장의 중의성 (보고 대상, 아래 BASE_NOTE):
  "base = SHA-256(json.dumps([generator, seed, aggregate_id]) ‖ \\0)" 는
  (A) base를 32바이트 digest로 보는 읽기와 (B) base를 해시 입력 앞부분(바이트열)으로 보는
  읽기가 둘 다 가능하다. 합성 complex 1천 명에서 (A)는 3명, (B)는 1000명이 맞았다.
  여기서는 (B)로 구현하고 명세 수정을 요청한다.
"""
import hashlib
import json

GENERATOR = 'uniform_bin_sha256_v1'
WORD = 1 << 64  # 앞 8바이트를 부호 없는 정수로 읽는다

BASE_NOTE = (
    '명세 V1의 "base = SHA-256(json ‖ \\0)"는 중의적이다. base를 digest로 읽으면(A) '
    '합성 complex 1천 명 중 3명만 맞고, 해시 입력 앞부분으로 읽으면(B) 1000명이 맞는다. '
    '(B)로 구현했다. 명세에 "SHA-256(base ‖ index ‖ c)에서 base는 digest가 아니라 '
    '앞 바이트열"임을 명시해야 한다.')


def passenger_id(aggregate_id, index):
    """명세 V1: json.dumps([aggregate_id, index], separators=(',', ':'))."""
    return json.dumps([aggregate_id, index], separators=(',', ':'))


def _base(seed, aggregate_id):
    """해시 스트림의 고정 앞부분. json.dumps는 기본 구분자(쉼표 뒤 공백)를 쓴다."""
    return json.dumps([GENERATOR, seed, aggregate_id]).encode() + b'\0'


def offset(seed, aggregate_id, index, bin_width_s):
    """bin 안 오프셋. 균등 분포를 깨지 않도록 rejection sampling한다.

    counter c를 0부터 올리며 SHA-256(base ‖ index(8B, big-endian) ‖ c(4B, big-endian))의
    앞 8바이트 x를 본다. x가 w의 배수 구간을 넘으면 버리고 c를 올린다.
    """
    if bin_width_s < 1:
        raise ValueError(f'bin_width_s는 1 이상이어야 한다: {bin_width_s}')
    base = _base(seed, aggregate_id)
    cutoff = WORD - (WORD % bin_width_s)
    counter = 0
    while True:
        digest = hashlib.sha256(base + index.to_bytes(8, 'big')
                                + counter.to_bytes(4, 'big')).digest()
        x = int.from_bytes(digest[:8], 'big')
        if x < cutoff:
            return x % bin_width_s
        counter += 1


def generate(bin_rows, seed):
    """집계 행에서 발생을 다시 만든다. 순서는 aggregate_id 정렬 후 index 순.

    [(passenger_id, origin, destination, time_s), ...]
    """
    out = []
    for row in sorted(bin_rows, key=lambda r: r.aggregate_id):
        for index in range(row.passenger_count):
            time_s = row.bin_start_s + offset(seed, row.aggregate_id, index, row.bin_width_s)
            out.append((passenger_id(row.aggregate_id, index), row.origin, row.destination, time_s))
    return out


def source_checksum(bin_rows):
    """명세 V1: aggregate_id 정렬 행 dict 목록의 정규 JSON에 대한 SHA-256."""
    payload = [{'aggregate_id': r.aggregate_id, 'bin_start_s': r.bin_start_s,
                'bin_width_s': r.bin_width_s, 'destination': r.destination,
                'origin': r.origin, 'passenger_count': r.passenger_count}
               for r in sorted(bin_rows, key=lambda r: r.aggregate_id)]
    return hashlib.sha256(json.dumps(payload, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def realization_checksum(seed, source, passengers):
    """명세 V1: 머리글 JSON 뒤에 승객마다 개행 + 정규 JSON을 이어 붙인 것의 SHA-256."""
    h = hashlib.sha256()
    h.update(json.dumps([GENERATOR, seed, source], separators=(',', ':')).encode())
    for row in passengers:
        h.update(b'\n' + json.dumps(list(row), separators=(',', ':')).encode())
    return h.hexdigest()


# ---------- V1 검사 ----------

def check_realization(scenario, run):
    """V1 R5 발생 재현.

    집계 수요 행과 seed로 발생을 다시 만들어 (a) `realization.jsonl`과 (b)
    `passenger_outcomes.csv`의 OD·발생 시각과 비교하고, source/realization checksum을
    다시 계산해 대조한다. realization.jsonl이 없어도 (b)로 검사한다.
    """
    from .checks import CheckResult, NOT_VERIFIED

    result = CheckResult('V1', 'R5 발생 재현', unit='승객')
    seed = run.measurement.get('arrival_seed')
    if seed is None:
        result.status = NOT_VERIFIED
        result.note = 'measurement.json에 arrival_seed가 없어 발생을 다시 만들 수 없다.'
        return result

    rows = scenario.bin_rows
    mine = generate(rows, seed)
    result.examined = len(mine)
    mine_by_id = {row[0]: row for row in mine}
    if len(mine_by_id) != len(mine):
        result.add('재계산 passenger_id가 유일하지 않다 (명세 V1 index 0..count−1 유일)')

    expected_total = sum(r.passenger_count for r in rows)
    if len(mine) != expected_total:
        result.add(f'재계산 승객 수 {len(mine)}가 집계 행 인원 합 {expected_total}과 다르다')

    # bin 경계와 OD는 재계산이 정의상 만족하지만, 집계 행 자체의 유효성을 확인한다.
    for row in rows:
        if row.bin_width_s < 1:
            result.add(f'bin_width_s가 1 미만: {row.bin_width_s}', aggregate_id=row.aggregate_id)
        if row.passenger_count < 0:
            result.add(f'passenger_count가 음수: {row.passenger_count}',
                       aggregate_id=row.aggregate_id)
        if row.origin == row.destination:
            result.add('출발지와 목적지가 같다', aggregate_id=row.aggregate_id)

    source = source_checksum(rows)
    checksum = realization_checksum(seed, source, mine)

    # (a) realization.jsonl
    if run.has_realization:
        meta, filed = run.realization()
        if meta.get('generator') != GENERATOR:
            result.add(f'generator가 {GENERATOR!r}가 아니다: {meta.get("generator")!r}')
        if meta.get('seed') != seed:
            result.add(f'realization seed {meta.get("seed")!r}가 measurement '
                       f'arrival_seed {seed!r}와 다르다')
        if meta.get('source_checksum') != source:
            result.add('source_checksum이 집계 수요 행에서 다시 계산한 값과 다르다 '
                       '(demand_bins.csv가 바뀌었거나 다른 수요로 실행했다)',
                       recomputed=source, recorded=meta.get('source_checksum'))
        if meta.get('checksum') != checksum:
            result.add('realization checksum이 재계산과 다르다',
                       recomputed=checksum, recorded=meta.get('checksum'))
        if meta.get('passenger_count') != len(mine):
            result.add(f'메타 passenger_count {meta.get("passenger_count")}가 '
                       f'재계산 {len(mine)}와 다르다')

        meta_rows = meta.get('rows')
        if isinstance(meta_rows, list):
            recorded = sorted((r['aggregate_id'], r['origin'], r['destination'],
                               r['bin_start_s'], r['bin_width_s'], r['passenger_count'])
                              for r in meta_rows)
            scenario_rows = sorted((r.aggregate_id, r.origin, r.destination, r.bin_start_s,
                                    r.bin_width_s, r.passenger_count) for r in rows)
            if recorded != scenario_rows:
                only_meta = [r for r in recorded if r not in set(scenario_rows)]
                only_csv = [r for r in scenario_rows if r not in set(recorded)]
                result.add('realization 메타의 집계 행이 demand_bins.csv와 다르다',
                           only_in_realization=only_meta[:5], only_in_demand_bins=only_csv[:5])

        if len(filed) != len(mine):
            result.add(f'realization 승객 수 {len(filed)}가 재계산 {len(mine)}와 다르다')
        for position, (theirs, ours) in enumerate(zip(filed, mine)):
            if theirs != ours:
                result.add('realization 승객이 재계산과 다르다 '
                           '(순서는 aggregate_id 정렬 후 index 순)',
                           position=position, recomputed=list(ours), recorded=list(theirs))
                if len(result.violations) > 200:
                    break
        recorded_checksum = run.measurement.get('realization_checksum')
        if recorded_checksum is not None and recorded_checksum != checksum:
            result.add('measurement.json realization_checksum이 재계산과 다르다',
                       recomputed=checksum, recorded=recorded_checksum)
    else:
        result.note = ('realization.jsonl이 없어 발생을 집계 행과 seed로 다시 만들어 '
                       'passenger_outcomes.csv와만 비교했다. checksum 대조는 하지 못했다.')

    # (b) passenger_outcomes.csv (realization이 없어도 가능한 교차 검사)
    declared = run.outcomes
    for pid in sorted(set(mine_by_id) | set(declared)):
        ours, theirs = mine_by_id.get(pid), declared.get(pid)
        if ours is None:
            result.add('CSV에 있는 승객이 재계산 발생에 없다', passenger_id=pid)
            continue
        if theirs is None:
            result.add('재계산 발생에 있는 승객이 CSV에 없다', passenger_id=pid)
            continue
        if (theirs.origin, theirs.destination, theirs.time_s) != (ours[1], ours[2], ours[3]):
            result.add('CSV의 OD·발생 시각이 재계산과 다르다', passenger_id=pid,
                       recomputed=[ours[1], ours[2], ours[3]],
                       csv=[theirs.origin, theirs.destination, theirs.time_s])
    return result.resolve()
