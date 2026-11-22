"""도로명주소 → 경위도. 망에 없는 임시 정류장의 좌표를 구한다.

카카오 로컬 API의 "주소로 좌표 변환"을 쓴다. 표준 라이브러리만 쓰고 저장소에 키를 넣지
않는다. 키는 환경 변수 `KAKAO_REST_KEY`로만 받는다.

    export KAKAO_REST_KEY='...'                 # 저장소·커밋·프롬프트에 넣지 않는다
    python3 experiments/plans/geocode_stops.py plan-01

입력·출력은 `experiments/plans/<plan>/source/temporary-stops.json`이다. 받은 좌표를 그
파일에 **검토 대기 상태로** 적고, 사람이 확인한 뒤 `plan.json`에 반영한다.

이 도구가 하지 않는 것
- 후보가 둘 이상이면 **고르지 않는다**. `needs_review`로 표시하고 전부 적는다.
- API가 돌려준 주소가 입력과 다르면 **그대로 쓰지 않는다**. 둘 다 적고 표시한다.
- 노선 순서로 정해진 범위(`_location_bounds`)를 벗어나면 **표시**한다. 값을 바꾸지 않는다.
  다만 범위 검증은 **큰 오류만** 잡는다. 같은 동네의 다른 길은 수십 미터 차이라 통과한다
  (`디지털로27길` ↔ `디지털로27라길`이 +26 m). 그래서 주소 문자열도 함께 대조한다.
- `plan.json`을 직접 고치지 않는다.

좌표의 성격: API가 주는 것은 **건물 위치**다. 임시 정류장은 그 앞 도로변이라 수십 미터
차이가 날 수 있다. 그래서 상태를 `주소 기반 추정`으로 적는다.
"""
import argparse
import json
import math
import os
import re
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request

API = 'https://dapi.kakao.com/v2/local/search/address.json'
SOURCE_LABEL = '카카오 로컬 API 주소 검색 (주소로 좌표 변환)'
PLANS = Path(__file__).resolve().parent


def geocode(address, key, timeout=10):
    """도로명주소 → 응답 dict. 결과가 없으면 None. 키는 로그에 남기지 않는다."""
    url = API + '?' + urllib.parse.urlencode({'query': address, 'analyze_type': 'exact'})
    request = urllib.request.Request(url, headers={'Authorization': f'KakaoAK {key}'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        # 본문에 키가 섞일 수 있으므로 상태 코드만 올린다
        raise RuntimeError(f'조회 실패 HTTP {exc.code} — 앱의 카카오맵 사용 설정과 '
                           '일일 한도를 확인하라') from None
    docs = payload.get('documents') or []
    if not docs:
        return None
    first = docs[0]
    return {'matched_address': first.get('address_name'),
            'lat': float(first['y']), 'lon': float(first['x']),
            'candidates': len(docs),
            'all_candidates': [{'address': d.get('address_name'),
                                'lat': float(d['y']), 'lon': float(d['x'])} for d in docs]}


def distance_m(a, b):
    """두 (위도, 경도) 사이 거리(m). 서울 규모에서 충분한 근사."""
    mid = math.radians((a[0] + b[0]) / 2)
    return math.hypot((a[0] - b[0]) * 111_320, (a[1] - b[1]) * 111_320 * math.cos(mid))


def address_matches(query, matched):
    """반환 주소가 입력 주소와 같은 길·같은 번호인지 본다.

    범위 검증(`check_bounds`)은 큰 오류만 잡는다. 같은 동네의 **다른 길**은 수십 미터
    차이라 범위로 걸러지지 않는다(예: `디지털로27길` vs `디지털로27라길`). 그래서 주소
    문자열을 직접 대조한다.
    """
    if not matched:
        return False, '반환 주소가 비어 있다'
    q = query.replace(' ', '')
    # 괄호 안 법정동 표기 등은 떼고 본다: "디지털로27길 33 (구로동)" → "디지털로27길33"
    m = re.sub(r'\([^)]*\)', '', matched).replace(' ', '').strip()
    # 도로명 + 건물번호를 통째로 비교한다. 띄어쓰기만 다른 것은 같은 것으로 본다.
    tail = q.split('구')[-1] if '구' in q else q
    # endswith로 본다. `in`을 쓰면 "…27길33"이 "…27길331"에 걸려 다른 번지를 통과시킨다.
    if tail and m.endswith(tail):
        return True, None
    return False, f'반환 주소가 입력과 다르다: 입력 {query!r} ≠ 반환 {matched!r}'


def check_bounds(lat, lon, bounds):
    """노선 순서로 정해진 앞뒤 정류장 사이에 있는지 본다. 판단만 하고 값을 바꾸지 않는다."""
    if not bounds:
        return {'checked': False, 'reason': '_location_bounds가 없다'}
    before = (bounds['before']['lat'], bounds['before']['lon'])
    after = (bounds['after']['lat'], bounds['after']['lon'])
    span = distance_m(before, after)
    detour = distance_m(before, (lat, lon)) + distance_m((lat, lon), after)
    return {'checked': True,
            'segment_span_m': round(span),
            'via_point_total_m': round(detour),
            'extra_m': round(detour - span),
            # 앞뒤 정류장을 잇는 경로에서 크게 벗어나면 표시한다
            'inside_corridor': detour <= span * 1.5 + 300}


def run(plan, key, dry_run=False):
    path = PLANS / plan / 'source' / 'temporary-stops.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    bounds = data.get('_location_bounds')
    today = os.environ.get('GEOCODE_DATE') or '미기록'
    changed = 0

    for stop in data['stops']:
        address = stop.get('address_road')
        if not address:
            print(f"  건너뜀 — 주소 없음: {stop.get('name_on_map')}")
            continue
        if stop.get('lat') is not None:
            print(f"  건너뜀 — 이미 좌표 있음: {stop.get('name_on_map')}")
            continue
        print(f"  조회: {address}")
        if dry_run:
            continue
        got = geocode(address, key)
        if got is None:
            stop['coordinate_status'] = '조회 결과 없음'
            stop['_lookup'] = {'source': SOURCE_LABEL, 'queried_at': today,
                               'query': address, 'result': 'no_match'}
            print("    결과 없음")
            continue

        notes = []
        if got['candidates'] > 1:
            notes.append(f"후보 {got['candidates']}개 — 사람이 골라야 한다")
        ok, why = address_matches(address, got['matched_address'])
        if not ok:
            notes.append(why)
        bound = check_bounds(got['lat'], got['lon'], bounds)
        if bound.get('checked') and not bound['inside_corridor']:
            notes.append(f"노선 구간에서 벗어난다 (우회 +{bound['extra_m']} m)")

        stop['lat'] = got['lat']
        stop['lon'] = got['lon']
        stop['coordinate_status'] = '주소 기반 추정 — 검토 대기'
        stop['_coordinate_note'] = ('API가 주는 것은 건물 위치다. 임시 정류장은 그 앞 '
                                    '도로변이라 수십 미터 차이가 날 수 있다.')
        stop['_lookup'] = {'source': SOURCE_LABEL, 'queried_at': today,
                           'query': address, 'matched_address': got['matched_address'],
                           'candidates': got['candidates'],
                           'all_candidates': got['all_candidates'] if got['candidates'] > 1 else None,
                           'bounds_check': bound}
        stop['needs_review'] = bool(notes)
        stop['_review_reasons'] = notes or None
        changed += 1
        print(f"    위도 {got['lat']:.6f} 경도 {got['lon']:.6f} "
              f"({got['matched_address']})")
        for n in notes:
            print(f"    ⚠ {n}")

    if changed and not dry_run:
        data['updated_at'] = today
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(f"\n{path} 갱신 ({changed}건). 사람이 확인한 뒤 plan.json에 반영한다.")
    elif not dry_run:
        print("\n바뀐 것 없음")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('plan', help='운행안 폴더 이름 (예: plan-01)')
    parser.add_argument('--dry-run', action='store_true', help='조회할 주소만 찍고 끝낸다')
    args = parser.parse_args(argv)

    key = os.environ.get('KAKAO_REST_KEY')
    if not key and not args.dry_run:
        print('KAKAO_REST_KEY 환경 변수가 없다. 키를 저장소나 명령줄 인자로 넣지 말 것.',
              file=sys.stderr)
        return 2
    run(args.plan, key, args.dry_run)
    return 0


if __name__ == '__main__':
    sys.exit(main())
