"""V15–V17 운행 제약·체크섬. 명세 §3을 따른다.

V16은 자료가 없어 끝까지 검증할 수 없다(R10 가용 fleet, R13 방향별 구간). 명세 자신이
R13을 "미검증(통과 아님)"으로 정했다. 그래서 V16은 검사 가능한 규칙에 위반이 없어도
`pass`가 아니라 `not_verified`로 남는다. 통과하지 않은 것을 통과로 적지 않기 위한 것이다.
"""
from .checks import CheckResult, FAIL, NOT_VERIFIED
from .inputs import sha256_file

# measurement.json의 기록 ↔ 산출물 파일
DIGEST_FIELDS = (('events.jsonl', 'log_sha256', 'log_bytes'),
                 ('passenger_outcomes.csv', 'outcome_sha256', 'outcome_bytes'),
                 ('metrics.json', 'metrics_sha256', None),
                 ('realization.jsonl', 'realization_sha256', None))


# ---------- V15 R12 배차간격 ----------

SINGLE_TRIP_NOTE = (
    '연속 출발이 없는 노선(운행편 1개)의 "최대 간격"을 명세가 정의하지 않았다. 엔진은 그 '
    '노선을 max_gap_by_route_s에서 생략한다. 검증기는 간격이 정의되는 노선만 비교하고 '
    '생략된 노선 목록을 여기에 남긴다. 명세가 정의(생략 또는 0)를 정해야 한다.')


def headway_report(scenario, max_headway_s):
    """노선별·정류장별 연속 출발 간격.

    (최대 간격 dict, 위반 목록, 간격이 정의되지 않는 노선 목록). 연속 출발이 한 쌍도
    없는 노선은 최대 간격을 만들지 않는다(명세 미정, SINGLE_TRIP_NOTE 참고).
    """
    by_route_stop = {}
    routes = set()
    for trip_id, visits in scenario.visits_by_trip.items():
        route = scenario.route_of.get(trip_id)
        if route is None:
            continue
        routes.add(route)
        for visit in visits:
            by_route_stop.setdefault((route, visit.stop), []).append(visit.departure_s)

    max_gap, violations = {}, []
    for (route, stop), departures in sorted(by_route_stop.items()):
        departures.sort()
        for before, after in zip(departures, departures[1:]):
            gap = after - before
            if gap > max_gap.get(route, -1):
                max_gap[route] = gap
            if gap > max_headway_s:
                violations.append({'route': route, 'stop': stop, 'from_s': before,
                                   'to_s': after, 'gap_s': gap})
    violations.sort(key=lambda v: (v['route'], v['stop'], v['from_s']))
    return max_gap, violations, sorted(routes - set(max_gap))


def check_headway(scenario, run):
    result = CheckResult('V15', 'G/R12 배차간격', unit='노선',
                         note='창 경계 간격(운행 시작·종료와 첫·마지막 출발 사이)은 명세가 '
                              '미검사로 정했다(§3 V15 TODO 정책).')
    policy = run.measurement.get('policy') or {}
    max_headway_s = policy.get('max_headway_s')
    if max_headway_s is None:
        result.status = NOT_VERIFIED
        result.note = 'measurement.json policy에 max_headway_s가 없어 R12를 재계산할 수 없다.'
        return result
    if not scenario.route_of:
        result.status = NOT_VERIFIED
        result.note = 'network.json에 route_of가 없어 노선별 간격을 재계산할 수 없다.'
        return result

    max_gap, violations, undefined = headway_report(scenario, max_headway_s)
    result.examined = len(max_gap)
    if undefined:
        result.note += ' ' + SINGLE_TRIP_NOTE + f' 생략한 노선: {undefined[:10]}'
    recorded = (run.metrics.get('G') or {}).get('R12')
    if not isinstance(recorded, dict):
        result.add('metrics.json G.R12가 없다')
        return result.resolve()

    if recorded.get('max_headway_s') != max_headway_s:
        result.add('G.R12.max_headway_s가 measurement policy와 다르다',
                   policy=max_headway_s, metrics=recorded.get('max_headway_s'))
    listed_gaps = recorded.get('max_gap_by_route_s') or {}
    differing = {route: {'recomputed': gap, 'metrics': listed_gaps.get(route)}
                 for route, gap in max_gap.items() if listed_gaps.get(route) != gap}
    if differing:
        result.add('노선별 최대 간격이 재계산과 다르다', differing=dict(list(differing.items())[:10]),
                   differing_routes=len(differing))
    # 간격이 정의되는 노선인데 metrics에 없는 경우도 위반이다.
    unexpected = sorted(set(listed_gaps) - set(max_gap) - set(undefined))
    if unexpected:
        result.add('metrics에만 있는 노선의 최대 간격', routes=unexpected[:10])
    if recorded.get('violation_count') != len(violations):
        result.add('R12 위반 수가 재계산과 다르다', recomputed=len(violations),
                   metrics=recorded.get('violation_count'))
    expected_pass = not violations
    if recorded.get('pass') is not expected_pass:
        result.add(f'G.R12.pass가 {recorded.get("pass")!r}인데 재계산 위반 {len(violations)}건',
                   recomputed_pass=expected_pass, examples=violations[:3])
    listed = recorded.get('violations')
    if isinstance(listed, list) and len(listed) != len(violations):
        result.add('G.R12.violations 목록 길이가 violation_count 재계산과 다르다',
                   recomputed=len(violations), listed=len(listed))
    return result.resolve()


# ---------- V16 G 기타 ----------

def check_fleet_rules(scenario, run):
    """R11 정원, R14 운영창, 차량 duty 비중첩·같은 출발 거점. R10·R13은 검증 불가."""
    result = CheckResult('V16', 'G 기타(R10·R11·R13·R14·duty)', unit='차량')
    spec = {}
    manifest_spec = (scenario.manifest or {}).get('demand_spec')
    network_spec = None
    # network.json의 spec은 합성 망 설정이다. 손으로 만든 fixture에는 해당 필드가 없다.
    try:
        import json
        network_spec = json.loads((scenario.path / 'network.json').read_text()).get('spec')
    except (OSError, ValueError):
        network_spec = None
    if isinstance(network_spec, dict):
        spec = network_spec

    unverified = ['R10 가용 fleet 대수 (Scenario.fleet이 산출물에 없다)',
                  'R13 방향별 구간 (M02 산출물 확보 전, 명세 §3 V16이 미검증으로 정함)']

    # R11 정원
    capacity = spec.get('capacity')
    if capacity is None:
        unverified.append('R11 정원 기준값 (network.json spec에 capacity가 없다)')
    else:
        for trip in scenario.trips.values():
            if trip.capacity != capacity:
                result.add(f'정원 {trip.capacity}가 시나리오 값 {capacity}와 다르다',
                           trip_id=trip.trip_id, vehicle_id=trip.vehicle_id)

    # R14 운영창
    start, end = spec.get('service_start_s'), spec.get('service_end_s')
    if start is None or end is None:
        unverified.append('R14 운영창 (network.json spec에 service_start_s·service_end_s가 없다)')
    else:
        for trip_id, visits in scenario.visits_by_trip.items():
            trip = scenario.trips[trip_id]
            if visits[0].arrival_s < start or trip.return_s > end:
                result.add(f'운행편이 운영창 [{start}, {end}] 밖이다',
                           trip_id=trip_id, first_arrival_s=visits[0].arrival_s,
                           return_s=trip.return_s)

    # 차량 duty: 비중첩 + 같은 출발 거점
    by_vehicle = {}
    for trip_id, visits in scenario.visits_by_trip.items():
        by_vehicle.setdefault(scenario.trips[trip_id].vehicle_id, []).append((visits, trip_id))
    result.examined = len(by_vehicle)
    for vehicle_id, duties in sorted(by_vehicle.items()):
        duties.sort(key=lambda item: item[0][0].arrival_s)
        depots = {visits[0].stop for visits, _ in duties}
        if len(depots) > 1:
            result.add(f'같은 차량의 운행편 출발 거점이 여러 곳이다: {sorted(depots)[:5]}',
                       vehicle_id=vehicle_id)
        for (before, before_id), (after, after_id) in zip(duties, duties[1:]):
            before_return = scenario.trips[before_id].return_s
            if after[0].arrival_s < before_return:
                result.add(f'차량 duty가 겹친다: {before_id} 복귀 {before_return} > '
                           f'{after_id} 시작 {after[0].arrival_s}', vehicle_id=vehicle_id)

    result.note = '검증하지 못한 규칙: ' + ' / '.join(unverified)
    # 검사한 규칙에 위반이 있으면 실패. 없어도 R10·R13 때문에 통과로 적지 않는다.
    result.status = FAIL if result.violations else NOT_VERIFIED
    return result


# ---------- V17 체크섬 ----------

def check_checksums(scenario, run):
    result = CheckResult('V17', '체크섬', unit='파일')
    recorded = (scenario.manifest or {}).get('sha256')
    if not isinstance(recorded, dict):
        result.add('scenario_manifest.json에 sha256 기록이 없다')
    else:
        for name, digest in sorted(recorded.items()):
            path = scenario.path / name
            result.examined += 1
            if not path.is_file():
                result.add('manifest가 가리키는 시나리오 파일이 없다', file=name)
                continue
            actual = sha256_file(path)
            if actual != digest:
                result.add('시나리오 파일 sha256이 manifest 기록과 다르다',
                           file=name, actual=actual, manifest=digest)

    not_recorded = []
    for name, digest_field, bytes_field in DIGEST_FIELDS:
        path = run.path / name
        digest = run.measurement.get(digest_field)
        if digest is None:
            if path.is_file():
                not_recorded.append(f'{name} (measurement.{digest_field} 없음)')
            continue
        result.examined += 1
        if not path.is_file():
            result.add('measurement가 기록한 산출물 파일이 없다', file=name)
            continue
        actual = sha256_file(path)
        if actual != digest:
            result.add('산출물 파일 sha256이 measurement 기록과 다르다',
                       file=name, actual=actual, measurement=digest)
        if bytes_field is not None and run.measurement.get(bytes_field) is not None:
            size = path.stat().st_size
            if size != run.measurement[bytes_field]:
                result.add('산출물 파일 크기가 measurement 기록과 다르다',
                           file=name, actual=size, measurement=run.measurement[bytes_field])

    if run.has_realization:
        meta, _ = run.realization()
        result.examined += 1
        if meta.get('checksum') != run.measurement.get('realization_checksum'):
            result.add('realization.jsonl checksum이 measurement 기록과 다르다',
                       realization=meta.get('checksum'),
                       measurement=run.measurement.get('realization_checksum'))

    manifest_in_measurement = run.measurement.get('scenario_manifest')
    if isinstance(manifest_in_measurement, dict):
        result.examined += 1
        if manifest_in_measurement.get('sha256') != recorded:
            result.add('measurement.json이 품은 scenario_manifest의 sha256이 '
                       'scenario_manifest.json과 다르다')

    if not_recorded:
        result.note = ('measurement.json에 digest가 기록되지 않아 무결성을 확인하지 못한 '
                       '산출물: ' + ', '.join(not_recorded) +
                       '. 엔진이 기록을 추가해야 V17이 이 파일들을 덮을 수 있다.')
    return result.resolve()


def run_guards(scenario, run):
    """V15–V17을 돌려 CheckResult 목록을 돌려준다."""
    return [check_headway(scenario, run), check_fleet_rules(scenario, run),
            check_checksums(scenario, run)]
