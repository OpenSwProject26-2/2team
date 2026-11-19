# D 운영자 시뮬레이션 결과 계약

## 확인한 출처

- PR #41의 `experiments/reports/routegen_v4.md`: 4개 구, 8개 차량 조건, 4개 시나리오, 2개 운행안, 실험·테스트 5일 평균. 보고서의 M1 충족률은 `served / total`이다. 640회 M07 실행 통과라는 집계는 개별 실행의 검증 파일을 대신하지 않는다. V12/V15/V16은 미검증이다.
- `experiments/guro_exp/run_matrix.py`: `runs/<variant>/runs.json` 및 `runs_test/<variant>/runs.json`의 실행 레코드. `label`, `fleet`, `date`, `conversion_rc`, `sim_rc`, `verify_rc`, `stage_failed`, `reason`, `verify_pass`, `failed_checks`, `not_run_checks`, `not_verified_checks`, `input_bundle_id` 등이 조건별로 기록된다. 현재 생성 코드에는 `run_id`가 없다.
- `docs/contracts/M00_DATA_CONTRACT.md`: `(plan_id, revision)`은 불변 버전이며 `selected`는 실험 대안 선택이다. 실제 운행 승인을 뜻하지 않는다. 실패 응답은 `code`, `message`, `details`, `request_id` 형태다.

## 로컬 API

`GET /api/d/simulation/result`는 조건 `region`, `fleet`, `variant`, `plan`, `period`, `date`를 받는다. `date=mean`은 보고서의 5일 평균이다. 개별 날짜는 연결된 B 원본만 사용한다. 선택한 결과의 `outcome.status`는 다음 중 하나다.

| 상태 | 의미 |
| --- | --- |
| `report_only` | PR #41 보고서의 5일 평균. 개별 M07 통과를 주장하지 않음 |
| `data_incomplete` | 실행 인덱스나 M05 지표가 없음 |
| `calculation_failed` | B 변환 또는 M05 실행 실패. 잔존 지표 파일을 유효한 수치로 쓰지 않음 |
| `verification_unknown` | M05 지표는 있으나 M07 확인 불가 |
| `verification_failed` | M07 실패 |
| `passed` | M05 성공 및 M07 `pass=true`. `fully_verified`와 미검증 항목은 별도 표시 |

계산 실패 시 `outcome.error`에 M00 오류 형식의 `JOB_FAILED`와 실패 단계·반환 코드를 담는다. `execution`에는 B 실행 레코드의 반환 코드, 실패 이유, 입력 번들 및 제공된 경우 재계산 출처(`provenance`) 등을 담는다. `run_id`가 없으면 `null`을 반환한다. `plan_id`, `revision`, `network_snapshot_id` 쿼리를 추가하면 실제 산출물 버전과 비교한다. 버전을 확인할 자료가 없으면 `DATA_INCOMPLETE`, 다르면 `DATA_VERSION_MISMATCH`다.

`GET /api/d/simulation/runs`는 같은 조건의 `runs.json`에 실제 기록된 날짜별 시도만 반환한다. `GET /api/d/simulation/table`은 우리 안과 피더안을 같은 조건에서 비교한다. `GET /api/d/simulation/selection`은 선택 포인터와 이력을 반환한다. `POST /api/d/simulation/selection`은 실제 날짜의 M05 성공, M07 통과, 실패·미실행 검사 없음, 요청한 운행안 버전 일치를 요구한다. 저장된 `approved_for_service=false`는 실제 운행 확정이 아님을 명시한다.

`GET /api/d/simulation/residual`은 `--transit-data`의 `bundles_v4/region_bundles_0610_v4.zip`에서 `region`, `variant`, `period`, `date`에 맞는 `demand_bins.csv`를 읽어 C 지역망 출발 정류장 좌표에 합산한다. 이것은 **셔틀 투입 전 잔여 수요**이며 M05 미충족 수요가 아니다. 입력은 06:00~10:00의 네 시간칸만 포함한다. 5명 미만 정류장·시간 칸은 API에서 숨긴다. `result.spatial_outcomes`는 해당 날짜의 B `passenger_outcomes.csv`가 있을 때만 출발 정류장별 셔틀 투입 후 결과를 제공한다. 지도는 해당 실행이 있으면 미충족 원을, 없으면 잔여 수요 원을 표시한다.

## `routegen-results/1` 결과 manifest (PR #47)

`--simulation-results <root>` 아래 `<region>/manifest.json`의 `schema`가 `routegen-results/1`이면 결과 카탈로그를 manifest에서 읽는다. 주요 필드는 `region`, `gu`, `center`, `network_snapshot_id`, `root`, `network_file`, `fleets`, `variants`, `plans`다. `plans[]`는 UI 선택 ID(`id`), 표시명(`name`), 방법(`method`), 기준안 여부(`baseline`), 해당 차량·변형, 실제 `plan_id`·`plan_file`, `run_label`, 실험·테스트 `runs` 상대 경로를 제공한다. 지역마다 가능한 차량 수·변형·운행안이 다를 수 있으므로 `/api/d/simulation/options`의 해당 지역 목록을 기준으로 화면 선택지를 만든다.

manifest 결과의 5일 평균은 보고서에서 가져오지 않는다. 해당 기간 `runs.json`에서 실행이 성공한 날짜의 M05 `metrics.json`을 읽어 평균을 계산하고, M07 통과일 수와 계획의 노선 수도 별도로 반환한다. 일부 날짜만 있으면 응답의 `days`는 실제 집계 날짜 수다. 실행 원본이 없거나 모두 실패하면 값이 미연결로 남는다. `routegen-results/1` manifest가 없으면 기존 구·차량·변형·ours/sp 상수와 PR #41 `routegen_v4.md` 평균 fallback을 유지한다. API는 시뮬레이터를 재실행하지 않는다.

`/api/d/simulation/result`, `/table`, `/comparison`, `/map` 등 API의 질의 조건은 기존과 같지만, manifest 방식에서는 `plan`에 화면 표시명이 아니라 `plans[].id`를 전달한다. 결과 응답은 `plan_name`, `method`, 계획의 `plan_id`, `revision`, `network_snapshot_id`를 가능한 범위에서 유지한다. 비교표의 운행안 이름과 `baseline` 표시도 manifest를 따른다. 지도 geometry가 없으면 후보 정차점만 반환하고 도로 선형을 임의로 만들지 않는다.

## 원본 연결에 필요한 파일과 결정

PR #41에는 원본 데이터와 실행 결과가 포함되지 않았다. 지역별로 아래 구조를 `--simulation-results` 및 `--simulation-inputs` 루트 아래 제공해야 실제 노선, 차량 배정, 실행·검증 상태가 표시된다.

- 결과: `<region>/manifest.json`, `<region>/candidates/<variant>.json`, `<region>/plans/*.json`, `<region>/runs/<variant>/runs.json`, `<region>/runs_test/<variant>/runs.json`, 각 실행의 `run/metrics.json`, `run/passenger_outcomes.csv`, `verifier_report.json`, `plan_m05.json`.
- 지도: 지역 입력의 `<region>_0610_v4/network/stops.csv`, `network/travel_times.json` (`network_snapshot_id` 일치). 실제 도로 선형 또는 검증된 도로 추정 자료가 없으면 정류장 점만 표시한다.
- 선택 기능을 실제 서비스에 연결하려면 A GTFS 정류장과 C 지역망 정류장의 검증된 ID 매핑, 운행 방향·정차 순서·시간표, 실제 운행 승인 주체와 승인 상태의 정의가 필요하다.
- B가 M00 공통 계약의 `run_id`, `plan_revision`, `config_hash`, 실행 시각을 생성·전달하는지 결정해야 한다. 현재 `run_matrix.py`의 `runs.json`에는 이 필드가 없으므로 D는 임의로 채우지 않는다.
- 정류장별 셔틀 투입 후 충족·미충족 지도에는 해당 실행의 `passenger_outcomes.csv` 또는 같은 의미의 정류장·시간대 비식별 집계가 필요하다. 구 단위 5일 평균을 공간에 임의 배분하지 않는다. 최초 제공 자료에는 이 실행 원본이 없으며, 별도 로컬 재계산한 구로 한 날짜 네 건은 PR에 포함하지 않는다.

2026-11-19 현재 `origin/main`, 제공된 로컬 ZIP과 프로젝트 폴더에서 위 지역별 실행 원본은 확인되지 않았다. GitHub의 평가 테스트 fixture에 있는 `metrics.json`·`passenger_outcomes.csv`는 테스트용이며 구별 C/B 결과로 연결하지 않는다. 로컬에 A GTFS ZIP과 `seoul_transit_data.Zip`은 존재하지만 시뮬레이션 결과를 대신하지 않는다.
