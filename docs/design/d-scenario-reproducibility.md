# D 합성 시나리오 재현 및 외부 데이터 경계

2026-11-23 기준. 이 문서는 **D 합성 DEMO의 계산·화면**을 재현하는 절차다. D 결과는 실제 파업일 관측치, C M05 실행 결과, B M07 독립 검증 결과가 아니다.

## 현재 입력과 출처

| 입력 | 버전·범위 | 배치·획득 | 이용 조건·상태 |
|---|---|---|---|
| `tests/fixtures/d_dashboard/demo.json` | `d-map-demo-v1`, 기준일 2026-11-13, 10개 가상 거점, 파업 700·701, 시내·경기·마을·지하철·셔틀 가상 노선. 날짜 11/13·11/14·11/16의 거점별 값과 시간 계수 | Git에 포함. `load_demo`가 거점별 OD 셀을 결정적으로 생성 | `synthetic`. 실제 노선·OD·수송 정원이 아니다. SHA-256 `fe07cd38f62e055de2d8e95b43393edf63059af74a28ee5ec839421554e5a933` |
| `tests/fixtures/d_dashboard/road_shapes.json` | DEMO 도로 선형 추정 | Git에 포함 | 실제 운행·정차 검증 자료가 아니다. SHA-256 `b8b92516c99e16c6451f52ae714f3924d042d8dd40b9c236509267166c99d326` |
| A 제공 `busstrike_data_release_20261111.zip` | 2026-11-02~06 GTFS 포함. 로컬 확인본에서 경기버스 분류 0개 | 팀 제공 ZIP을 각자 로컬에 놓고 `--a-gtfs`로 전달 | Git 미포함. A 지도 미리보기에만 사용. 수요 재계산 입력이 아니다. 원본 배포 조건은 팀 확인 필요. 이 PC의 ZIP SHA-256 `bd6ac1eccb59f8a854ef1c01fa37f64eced1f9a3b19cdc2a5223aa0cd390f2d4` |
| 로컬 `seoul_transit_data.Zip` | 2026-10-26~30, 11-02 집계·시간표·혼잡도 포함. 내부 README는 생성일 2026-11-17, 원본의 개인 PC 사용 제한과 k5 공유본을 구분 | 팀 제공 ZIP을 `--transit-data`로 전달 | Git 미포함. `seoul/<date>/subway_od_hourly.csv`는 개인 PC용 원 해상도 집계이며 **공유/커밋 금지**. `share_k5`는 5명 미만 셀 제거로 OD 보존율이 제한됨. 지하철 여유 수송력으로 자동 환산하지 않음. 이 PC의 ZIP SHA-256 `f048272be4b650d5787140bf7a45603908c7a3607344a82ebd61bb01c2b4de83` |
| C `region_bundles_0610_v4.zip` 및 M05 원본 | `docs/design/routegen-data.md`, `routegen-design.md`에 입력 계약 설명 | 이 작업 환경에는 ZIP 미존재 | D의 합성 OD나 B 결과로 취급하지 않음. C 코드/자료 재현은 C 제공 번들·동일 실행 설정이 있어야 함 |
| B M07 원본 실행 디렉터리 | `docs/design/m07-output-contract.md` | 이 작업 환경에는 원본 미존재 | 현재 조회 화면의 보고서 5일 평균과 구분. M07 통과로 표시하지 않음 |

원본 ZIP의 해시·배포판 버전은 팀원별로 `scripts/verify_d_scenario.py --manifest`를 실행해 로컬에서 비교한다. 각자 절대 경로를 코드에 넣지 않는다. 민감 집계의 원본 내용이나 파일 목록을 Git에 복사하지 않는다.

## 조사한 공식 외부 자료

| 자료·제공기관 | 공식 주소·기준 | 이용 조건·인증 | 현재 활용 가능 범위 |
|---|---|---|---|
| 경기도 `경기도_버스노선 조회` (GBIS) | [공공데이터포털 15080662](https://www.data.go.kr/data/15080662/openapi.do), `https://apis.data.go.kr/6410000/busrouteservice/v2/`, 2025-05-12 메타데이터 수정, 실시간 갱신 | 무료·이용허락범위 제한 없음. 공공데이터포털 활용신청 후 `serviceKey` 필요. 개발 트래픽 1,000회 | 노선 정보·경유 정류장·형상 조회 가능. 강동구 지도에는 별도 로컬 파일의 공식 선형 23개를 연결했다. 키나 원본 응답은 저장소에 포함하지 않는다. 2026-10/11 과거 운행 여부·실제 승객·여유 정원은 이 API만으로 복원 불가 |
| 서울시 `CardSubwayTime` | [서울 열린데이터광장 OA-12252](https://data.seoul.go.kr/dataList/OA-12252/S/1/datasetView.do), `https://openapi.seoul.go.kr:8088/{KEY}/json/CardSubwayTime/{START}/{END}/{YYYYMM}`, 매월 전월 자료 갱신, 페이지 확인일 기준 데이터 갱신 2026-11-06 | 이용허락범위 제한 없음. 서울시 Open API 키 필요 | 월·호선·역·시간대별 승하차. **현재 키 없음/다운로드 안 됨**. 승객 OD나 열차 잔여 정원은 제공하지 않음 |
| 서울교통공사 지하철 혼잡도 | [서울 열린데이터광장 OA-12928](https://data.seoul.go.kr/dataList/OA-12928/F/1/datasetView.do), 1~8호선 역·방향·30분 단위 평균, 분기 파일 | 이용허락범위 제한 없음으로 안내. 파일별 배포 조건 재확인 필요 | 과거 평균 혼잡의 참고 자료. 특정 파업일 차량별 여유 정원·OD로 볼 수 없음. D 계산에 미연결 |

자료의 이용허락범위와 실제 배포 가능 범위는 다를 수 있다. 공공 API 결과도 수집일·원본 응답 해시를 남기고, 팀 정책 확인 전에는 Git에 넣지 않는다. API 신청/발급은 각 팀원의 계정에서 수행한다.

## 공식 API 응답 수집

`scripts/fetch_d_public_transit.py`는 API **원본 응답만** 로컬에 저장하고 SHA-256·수집 시각을 매니페스트로 남긴다. A/C 정류장 ID와 자동으로 합치지 않는다. 키를 명령행·로그·매니페스트에 남기지 않는다. 출력 위치는 Git 밖으로 지정한다.

```powershell
# 공공데이터포털에서 데이터셋 15080662 활용신청 후 발급받은 키를 현재 셸에만 지정
$env:DATA_GO_KR_SERVICE_KEY = '<발급받은 키>'
py -3 scripts/fetch_d_public_transit.py gyeonggi --route-id <공식_GBIS_routeId> --out "$env:TEMP\d-public-transit"

# 서울 열린데이터광장에서 Open API 키 발급 후
$env:SEOUL_OPEN_DATA_KEY = '<발급받은 키>'
py -3 scripts/fetch_d_public_transit.py metro_monthly --month 202610 --start 1 --end 1000 --out "$env:TEMP\d-public-transit"
```

월간 지하철 API는 페이지 단위이므로 전체 자료가 필요하면 범위를 바꿔 반복 수집하고 행 수와 중복을 확인한다. GBIS 노선번호 검색 결과와 A GTFS `route_id`는 같다고 가정하지 않는다. 공식 `routeId`·정류장 순서·좌표·방향·시행 날짜를 대조한 뒤 매핑률을 별도 기록한다. 매핑이 확인되지 않은 노선은 실제 레이어나 흡수량에 투입하지 않는다.

## 합성 DEMO 계산 계약

`POST /api/d/demo/scenario/evaluate` 입력은 `date`(지원 날짜 중 하나, 생략 시 기준일), `hour`, `strike_route_ids`, `active_normal_route_ids`, 셔틀 `routes[{route_id, stop_ids, vehicles}]`다. 파업 체크 해제 노선은 **복구 운행**으로 처리한다. 700·701은 fixture의 `stop_ids` 순서와 `demo_capacity_per_hour`를 사용하며 자기 노선에 연결된 OD만 먼저 수송한다. 시내·경기·마을·지하철, 마지막으로 셔틀은 방향이 맞는 OD를 각 시간당 가정 정원까지 순서대로 수송한다. 한 OD 셀의 `remaining`을 차감하므로 중복 수송하지 않는다. 정원은 노선 전체에 한 번 적용하며 회차·혼잡·운행시각·환승·실제 차량별 잔여 정원은 모델링하지 않는다.

결과는 `total`, `served`, `unmet`, `route_effects`, 셀별 `features`와 `scenario_fingerprint`를 반환한다. `total = served + unmet`, `served = Σ route_effects.served`, `unmet = Σ features.unmet`을 검사한다. 같은 날짜·시간에서 노선이나 셔틀 차량 수를 바꾸어도 전체 OD는 그대로다. 날짜·시간을 바꾸면 fixture의 **명시된 가상 입력**에 따라 OD 크기와 분포가 바뀐다. 이 날짜는 관측 날짜가 아니다.

지도에서 DEMO 파업 체크·수단 운행 체크·레이어 수단 체크·셔틀 경로/차량/활성 상태·날짜/시간 변경은 평가 API를 다시 호출한다. 지도 수요 원과 요약은 응답 `features`/집계에서 갱신하고 같은 날짜·시간의 직전 미충족 값을 약 220ms 보간한다. 지도 표시 체크와 운행 체크가 같은 수단에 대해 동기화되어 있어 토글은 수송 조건도 변경한다. A GTFS 모드의 레이어/파업 강조는 표시 가정이며 실제 OD 계산이 아니다. B 결과 조회 화면은 보고서·원본 실행 조회를 별도로 유지한다.

## 새 PC에서 재현

Python 3.10 이상이 필요하다. 합성 DEMO와 아래 검증에는 제3자 Python 패키지가 없다. 개발 설치를 원하면 `py -3 -m pip install -e .`를 실행한다. 네트워크 지도 바탕은 OpenFreeMap 접속이 필요하다.

```powershell
git clone https://github.com/OpenSwProject26-2/2team.git
Set-Location 2team
# 이 작업을 공유받은 브랜치/작업 파일에 맞춘 뒤
py -3 scripts/verify_d_scenario.py
py -3 -m unittest tests.api.test_d_dashboard tests.api.test_d_dashboard_http tests.api.test_d_transport_demand tests.api.test_d_simulation
py -3 web/operator/serve.py --port 8767
# 브라우저에서 http://127.0.0.1:8767/ 열기
```

Linux/macOS에서는 `python3`로 바꾸면 된다. DEMO 검증의 입력 해시와 시나리오별 결과를 다른 PC의 출력과 비교한다. Python 마이너 버전, Git 리비전, 외부 ZIP 해시·관측일·API 수집 시각을 함께 기록한다. 검증 스크립트의 결과 지문은 입력의 정렬된 JSON SHA-256 앞 16자이며 난수 시드는 사용하지 않는다.

로컬 A 미리보기를 함께 볼 경우 팀 제공 ZIP의 사용 권한과 해시를 확인한 뒤 `--a-gtfs <ZIP 경로> --transit-data <ZIP 경로>`를 서버 명령에 추가한다. ZIP은 저장소에 복사하지 않는다. B/C 파일 해시만 대조하려면 개인 PC에서 `['C:\\path\\input.zip', 'C:\\path\\run.json']` 형태의 JSON 목록을 로컬 매니페스트로 만든 뒤 `--manifest <파일>`을 사용한다. 이는 **해시 대조일 뿐 B/C 실행 검증이 아니다**. B/C 재현은 해당 팀이 제공한 원본 입력, 설정, 실행 명령, M05/M07 출력·검증 보고서를 각각 요구한다. D가 그 결과를 합성 DEMO와 동등한 것으로 표시하지 않는다.

## 계약 연결과 남은 차이

- C `routegen-design.md`는 파업 수요를 정상 시내버스로 우선 흡수한 뒤 마을·경기버스를 별도 정원 모델로 흡수한다. C의 버스 단일 구간 모델은 지하철 환승·열차 여유 정원을 평가하지 않는다. D는 10개 가상 거점의 단순 OD를 정해진 순서와 정원으로 배분하므로 C의 M05 수치와 직접 비교할 수 없다.
- B `simulator.md`의 M07 `strike_route_ids`는 기록용이고 실제 차량 시간표·승객 여정·대기·환승을 별도 입력으로 계산한다. D의 시간당 정원 배분은 M07 검증 대상이 아니다.
- 경기버스 지도 레이어에는 GBIS 공식 선형 23개를 별도 파일로 연결했다. A GTFS에는 경기버스가 없어 A/C 정류장 ID 교차표, 관측일 운행 여부와 직접 관측 승차량까지 검증된 상태는 아니다. GBIS 선형의 확보 현황과 재현 방법은 [GBIS 선형 문서](d-gbis-official-geometry.md)에 적었다.
- 실제 지하철 잔여 수송력에는 같은 관측일·시간대의 OD, 운행 편수/편성 정원, 이미 탑승한 승객과 환승 가능한 연결이 필요하다. 로컬 지하철 OD와 공공 월별 승하차·평균 혼잡만으로는 여유 정원을 검증할 수 없다.
- 공용 계약 변경 제안: A/C 공통 `route_id`·`stop_id` 교차표와 버전, C의 파업/정상 수송 후 **OD별 잔여 수요** 및 수단별 배정 출처, B의 M07 실행 디렉터리·검증 보고서를 D에 제공한다. 이는 D 제안이며 B/C의 확정 계약이 아니다.

## 차량 수에 따른 원과 현재 검증 상태

- **합성 DEMO:** 셔틀 차량 수는 `vehicles × capacity_per_vehicle`로 시간당 가정 정원에 반영된다. 같은 날짜·시간 5대→1대에서 전체 OD는 984명으로 같고 미충족은 469→493명으로 증가했다. 지도 원은 평가 API가 반환한 셀별 `unmet`으로 다시 그린다.
- **저장된 B/C 결과 화면:** 상단 차량 선택은 해당 보고서나 원본 실행 조건을 조회한다. 구로 `affected_maeul_gg` 실험 5일 평균의 11대·13대는 보고서 집계 충족 인원이 각각 1,134.2명·1,167.4명이다. 두 조건 모두 이 로컬 환경의 `spatial_outcomes.status=unavailable`이므로, 지도에는 **셔틀 투입 전** 입력 잔여 수요 원만 남는다. 보고서 합계 차이를 정류장에 임의 배분하지 않는다. `simulation.js`의 지도 상태 문구가 차량 효과 미반영을 명시한다. 같은 날짜·차량·운행안의 B `run/passenger_outcomes.csv`와 일치하는 정류장 좌표가 있어야 셔틀 투입 후 미충족 원을 보여 준다.
- 2026-11-23 로컬 검증: D 관련 `unittest` 39개 통과, `scripts/verify_d_scenario.py` 통과, `app.js`·`simulation.js` 모듈 문법 검사와 `git diff --check` 통과. 브라우저에서 로컬 지도와 A GTFS 화면이 열리는 것까지 확인했지만, 브라우저 제어 연결과 Windows 좌표 입력 오류로 교통수단·차량 토글 후 원과 파업 선의 **육안 비교는 완료하지 못했다**. 업로드 후 수동 화면 검증이 필요하다.

## Git 업로드 참고

이 저장소는 원시 카드 자료·개인 식별자·인증키와 `raw/`, `data/processed/`, ZIP의 커밋을 금지한다. `.browser-smoke*/`는 테스트용 Chrome 프로필로 쿠키·방문 기록이 들어갈 수 있어 `.gitignore`에서 제외한다. 사용하지 않는 미추적 `web/operator/data/districts.geojson`도 제외하고, 런타임은 추적 중인 `districts_all.geojson`을 사용한다. **`git add -A` 전에는 `git status --short`와 `git diff --cached --name-only`로 실제 포함 파일을 확인한다.** 이 문서의 해시와 API 수집 절차는 외부 ZIP의 업로드 허가를 뜻하지 않는다. 이 작업에서는 커밋·Push·PR을 만들지 않았다.
