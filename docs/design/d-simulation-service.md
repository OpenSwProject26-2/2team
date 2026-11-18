# D 운영자·시민 시뮬레이션 연동

## 현재 연결 범위

기존 운영자 MapLibre 화면의 `시뮬레이션 결과` 모드는 PR #41의 `routegen_v4.md`를 읽어 4개 구의 실험·테스트 5일 평균을 보여 준다. ours와 sp는 C팀의 실제 비교 방법 이름이며, 지자체안·균등 배분안·일반 AI안의 결과로 재분류하지 않는다. 비교표의 충족률은 B M05의 `M1.served / M1.total`이다. 다른 구·차량·기간은 잔여 수요 분모가 달라 그래프에 조건 차이를 명시한다.

현재 저장소와 최초 제공된 외부 데이터 경로에는 v4의 `manifest.json`, 후보 노선, 계획, 실행별 M05·M07 원본이 없다. 따라서 보고서 수치로 개별 노선·차량 배분이나 셔틀 투입 후 정류장별 미충족 수요를 만들지 않는다. 대신 제공된 `bundles_v4/region_bundles_0610_v4.zip` 안의 `demand_bins.csv`와 C 지역망 `network/stops.csv`를 연결해 **셔틀 투입 전 잔여 수요**를 정류장 원으로 표시한다. 입력 변형·날짜·시간에 따라 바뀌며 5명 미만 시간칸은 감춘다. `network_snapshot_id`가 맞는 원본 운행안이 연결되면 노선별 정차점·차량 수를 표시한다. 검증된 도로 선형이 없으면 노선 선을 그리지 않는다. A GTFS 정류장 ID와 C 지역 교통망 ID는 자동으로 합치지 않는다.

2026-11-18에 제공된 입력 번들과 저장소의 C/B/M07 코드로 구로 `affected` 2026-10-26의 11·13대 × ours/sp 네 건을 OneDrive 밖 임시 폴더에 **새로 계산**했다. 이는 PR #41의 원본 실행 파일이 아니다. 네 건 모두 M05 성공, UTF-8 모드로 재실행한 M07 `pass=true`, `fully_verified=false`, V12/V15/V16 미검증이다. `--simulation-results`와 `--simulation-inputs`로 이 외부 결과 폴더를 지정하면 날짜별 정류장 미충족 원과 실제 C 계획·차량 배분이 표시된다. 다른 날짜·지역·변형은 원본 파일이 없으므로 입력 잔여 수요 원과 보고서 5일 평균만 표시한다.

## 선택과 시민 경로

`POST /api/d/simulation/selection`은 개별 날짜 M05 실행 성공, 개별 M07 `pass=true`, 실패·미실행 검사 없음, 요청한 `plan_id/revision/network_snapshot_id`와 원본 일치를 확인한다. V12/V15/V16 같은 미검증 항목은 그대로 남긴다. `data/d_selection.json`에 선택 시각, 시나리오, 입력 묶음, 검증 상태와 이력을 저장하며 Git 대상에서 제외한다. `selected`는 운행 승인 상태가 아니다.

시민 개발 화면은 합성 GTFS에서 평상시·파업·파업+합성 셔틀 상태를 계산한다. 운영자 선택 셔틀 모드는 같은 선택 포인터의 `plan_id/revision`을 검사한다. 현재 실제 시간표와 A/C 정류장 매핑이 없으므로 이 모드는 `DATA_INCOMPLETE`를 반환하며, 합성 셔틀을 실제 선택안 대신 표시하지 않는다. 파업으로 경로가 없을 때는 `NO_PATH`다. 경로 연결 가능성과 M05의 시간 내 수송 성공·좌석 가능성은 서로 다른 결과다.

## 원본 인수 조건

구로·동작·강서·강동 각각의 v4 출력 루트(`manifest.json`, `candidates/<variant>.json`, `plans/*.json`, `runs/<variant>/runs.json`, `runs_test/<variant>/runs.json`, 실행별 `run/metrics.json`, `run/passenger_outcomes.csv`, `verifier_report.json`, `plan_m05.json`)와 지역 입력 묶음의 `network/stops.csv`, `network/travel_times.json`을 받는다. 공간 수요에는 개인 식별자를 제거한 정류장·OD·시간대 집계와 A/C 정류장 ID 매핑이 추가로 필요하다. 시민 실제 경로에는 선택 계획 버전에 대응하는 검증된 운행 시간표와 파업 제외 노선 목록이 필요하다. 도로 선형이 필요하면 정차 순서·방향·버전에 맞는 검증된 geometry를 받는다.
