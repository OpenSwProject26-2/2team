# 구로 추출본의 오전 OD 전체와 정제 방문열 연결·검증

Issue #10 분석·정제 결과에 이어 M02·M03 연결 근거를 보존하는 A 내부 개발용 도구다. 팀 승인 Network/Demand·엔진 실행 입력·발생 정책을 확정하지 않는다. 기존 추출·정제 코드와 팀원의 원본 집계 기여를 재사용한다. 원본 ZIP이나 전체 공유 OD를 다시 집계하지 않는다.

**이번 실제 실행 범위는 구로 5개 노선 추출본의 오전 OD 전체**다. `demand_analysis.py`의 공유 정리본 범위 기초 수요 분석 코드와 구분하며, 서울 전체 OD를 연결하거나 분석한 실행이 아니다. 일반 처리 코드에는 실제 노선 ID·인원을 넣지 않는다.

## 실행과 출력 위치

저장소 루트에서 표준 라이브러리만 사용한다. 실제 출력과 실행 설정·계약 검토 메모는 Git 제외 경로에 둔다. 기존 출력은 덮어쓰지 않으며 재실행에는 새 경로가 필요하다.

```powershell
git check-ignore -v -- data/processed/guro_demand_linking_verified_run/links/report.json
python -B A.Part/run.py link_demand --draft-dir data/processed/guro_real_input_draft --network-dir data/processed/guro_network_cleaning_run/network --output-dir data/processed/guro_demand_linking_verified_run/links
python -B -m unittest discover -s A.Part/tests -t A.Part -v
python -B -m unittest discover -s tests -t . -v
```

A.Part 안에서는 `python -B -m data.link_demand`로 실행하며 같은 입력·출력 인자에 `../data/processed/...`를 사용한다. 모든 상대 인자는 현재 작업 디렉터리 기준이다. 입력 manifest에는 절대 경로와 체크섬을 기록한다. `--contract-evidence <JSON 경로>`를 주면 사람이 확인한 B/C 고정 SHA·검토 파일·제약 메모를 보고서에 기록하고 파일 체크섬도 남긴다. 도구가 이 메모를 원격 코드 검증이나 승인으로 간주하지는 않는다. 실제 실행 명령은 로컬 출력 상위 폴더의 `execution.json`에 보관했다. 기존 출력은 덮어쓰지 않는다.

## 추출 보고서와 출처 대사

정제 보고서 `quality_report.json.inputs`에 기록된 `validation_report.json` SHA-256을 현재 추출 보고서와 먼저 비교한다. 기존 `route_visits.csv`, `stops.json` 체크섬 검사도 유지한다. 필수 manifest 항목·SHA가 없거나 다르면 파일명과 문제 필드를 표시하고 중단한다. 일치한 SHA와 스키마·대상 노선·그룹 검사 결과는 `network_saved_source_validation.extraction_provenance`에 남긴다.

필요한 추출 보고서 항목은 기존 `guro-real-input-extraction-draft-v1` 스키마에 근거한다.

| 필수 필드 | 확인 내용 |
| --- | --- |
| `schema_version`, `status`, `all_checks_passed` | 위 스키마, `development_only_not_operational_input`, 기존 추출 검사 성공 표시 |
| `route_ids` | 고유한 비어 있지 않은 문자열 배열. 정제 설정 `route_ids`와 집합 일치 |
| `selected_source_groups` | `route_id`·`source_route_name` 쌍. 중복 없이 정제 `selected_groups`와 일치하고 대상 노선 모두 참조 |
| `total_morning` | `rows`, `passengers`: bool이 아닌 음수 없는 정수 |
| `route_totals` | 정확히 대상 노선 ID를 키로 하며 각각 `rows`, `passengers` 보유. 합계가 `total_morning`과 일치 |
| `observation_date_from_survey` | 유효한 YYYYMMDD 형식 관측 기준일. 서비스일 귀속의 증명은 아님 |
| `sources` | 파일별 `path`, `bytes`, SHA-256. 기존 추출의 `survey.json`, `report.md`, `route_stops.csv`, `bus_od_0709.csv` 출처 항목 필수 |

현재 OD를 별도 스트리밍해 전체·노선별 행 수와 PSNG_NUM 합계를 구하고, **추출 보고서에 이미 기록된 값**과 비교한다. 기대값을 현재 OD에서 만들거나 실제 구로 인원을 코드에 고정하지 않는다. 보고서 대상 밖 노선, 누락/변경된 행·인원, 전체 합계만 같고 노선별 합계가 다른 경우도 실패한다. 결과는 `extraction_reconciliation`의 기대/현재 값·항목별 검사에 기록하며 한 항목이라도 실패하면 `all_checks_passed=false`, CLI 종료 코드 1이다. 보고서 스키마·출처 연결 오류는 종료 코드 2다.

이는 기존 추출본과 정제본 사이의 **출처 연결·규모 대사**다. 과거 원본 파일의 현재 내용을 다시 검증하거나 원본 OD를 독립 재생성한 결과가 아니며, 실제 운행 방향·유효 노선 버전을 확인한 것도 아니다. `sources`의 상위 파일은 다시 열지 않고 출처 설명으로 계승한다.

## 입력·연결 규칙

- `morning_od.csv`의 모든 행을 스트리밍한다. 필수 열은 `source_csv_line`, `ROUTE_ID`, `RIDE_BSST_ID`, `ALGH_BSST_ID`, `RIDE_DTM_H`, `PSNG_NUM` 및 기존 추출의 `endpoint_class`, `internal_source_order`, `route_stop_reference`다. ID·원천 문자열은 그대로 보존하고 인원은 ASCII 정수 문자열을 합산한다. 시간대는 정확히 `07`·`08`만 허용한다. 누락·잘못된 값·중복 원천 행 번호는 중단하며 오류는 파일명·행 번호·필드명만 표시한다.
- 정제의 `stops.json`, `visits.json`, `segments.json`을 실제로 읽는다. `quality_report.json` 통과 표시만 신뢰하지 않고, 기존 `validate_network`로 추출 방문 행의 체크섬·모든 원천 대응·구간 참조·연속 순번·미확인 조건을 다시 대조한다. 외부 원본 CSV를 다시 읽거나 새 방문열을 추정하지 않는다.
- 같은 노선의 **모든 보존 원천 노선명 그룹**을 탐색한다. 선택 그룹만 우선하는 정책은 없다. 그룹을 합치거나 이름 그룹을 방향/버전으로 해석하지 않는다. 동일 정류장 재방문은 방문 ID로 구별한다.
- 그룹 내부에서 승차 순번 < 하차 순번인 모든 방문 쌍을 확인한다. 중간을 포함한 모든 순번에 정확히 한 방문이 있고, 각 인접 쌍의 정제 구간이 있어야 후보가 된다. 단절·충돌·누락 구간을 뛰어넘지 않는다. 실패한 방문 쌍과 사유도 남긴다.
- 유일 후보도 실제 그룹 배정·운행 방향·관측일 유효 버전은 **미확인**이다. 경로의 원천 기준일 후보는 남기지만 `STDR_DE`가 유효기간이나 서비스일을 증명한다고 해석하지 않는다. 반대 방향을 재구성하거나 현재 C 제약에 맞춰 방문열을 바꾸지 않는다.

| 연결 분류 | 개발용 분리 | 의미 |
| --- | --- | --- |
| `unique_connection` | `included` | 원천 순서상 연속 경로가 하나. 실행 가능·방향 확정·최종 영향 수요가 아님 |
| `multiple_candidates` | `held` | 보존 그룹 또는 재방문에서 여러 경로. 임의 선택하지 않음 |
| `no_connection` | `held` | 순서 후보 부재·참조 부족·단절·충돌·구간 누락 등. 실제 이동 불가로 확정하지 않음 |
| `same_stop` | `excluded` | 현재 B의 다른 출도착 입력에서만 분리. 원천 오류로 단정하거나 원본에서 삭제하지 않음 |

여기서 포함/제외/보류는 **원천 순서 기반 초안 분류**다. 포함분에도 `engine_ready=false`, 실제 방향·버전 미확인을 기록한다. 실제 실행 승인 기준에서 보면 포함분도 필요한 근거가 아직 부족하다.

## 연결 파일과 출처

연결 단계는 원래 `07`·`08`시 문자열을 보존하며 시간 bin 파일을 만들지 않는다. 입력이 제공하는 것은 **승차 태그 시간대**이며 승객 발생 시각이 아니다. `arrival_s`·개별 승객·난수 seed·realization을 생성하지 않는다. 관측 기준일은 계승하며 다음 날짜 기록의 서비스일 귀속은 미확인으로 유지한다. `rule_version=source-order-demand-linking-v2`는 내부 연결 출력 버전이며 공용 계약 변경이 아니다.

| 파일 | 내용 |
| --- | --- |
| `od_links.jsonl` | 원천 OD 1행당 1개 객체. 원문 추출 열·파일 내 행 번호·출처 행 번호·분류·그룹별 탐색 진단·모든 연속 경로 후보·차단 사유 |
| `report.json` | 원천 규모·노선/시간대/분류/분리별 행 수와 가중 인원, 모든 그룹, 입력 체크섬·계승 출처·관측 기준일·저장 결과 검증 |
| `README.md` | 실제 로컬 산출물의 범위·분리 기준·검증 결과와 부족 입력 |

`aggregate_id`는 입력 OD 파일 SHA-256·파일 내 행 번호·계승 원천 행 번호에서 결정론적으로 만든다. 값이나 원본 순서를 바꾸면 버전/ID가 달라질 수 있으며 팀 전역 ID 발급 정책은 아니다. 같은 노선·OD·시간이어도 서로 다른 원천 행을 합치지 않는다.

같은 `aggregate_id`로 JSONL에서 원래 노선·시간대·출처를 찾는다. 후보의 방문/구간 ID는 정제 JSON을 참조하며 `source_row_ids`는 정제 방문의 `source_variants.source_rows`로 연결된다. 회사·원천 노선명·순번·원천 행·반복 방문 근거는 해당 정제 파일과 체크섬을 통해 보존한다. 다중 후보를 여러 bin으로 복제해 인원을 증식시키지 않는다.

## 저장 검증과 실패 처리

CSV 전체를 행 목록으로 보관하지 않는다. 작은 정제 망과 입력 행 번호 집합을 메모리에 두고 OD는 순차 처리한다. 경로마다 원천 대응을 보존하므로 JSONL 크기는 CSV보다 커질 수 있다.

저장 후 원천 CSV·JSONL을 다시 읽는다. 원천 행마다 후보·분류를 재계산해 모든 저장 필드와 순서/출현 횟수를 대조한다. 행 수·인원에 대해 원본 = 포함 + 제외 + 보류, 분류·시간대·노선별 합계를 확인한다. 별도로 이전 추출 보고서와 대사하며, 입력 체크섬도 종료 전 다시 대조한다. 이는 공유 정리본 기반 참조·저장 검증이며 원본 카드부터 OD를 재생성한 독립 검증이 아니다.

잘못된 입력은 종료 코드 2, 저장 검증 실패는 1이다. 스트림은 실패해도 닫으며 부분 출력은 완성 결과로 사용하지 않는다. 출력이 이미 존재하거나 입력 디렉터리 안이면 중단한다. 합성 테스트는 재방문·모든 그룹 탐색·단절·충돌·누락 구간·같은 출도착·잘못된 입력·가중 인원·저장 누락/중복/오포함·참조·결정론성·입력 불변·덮어쓰기 방지를 검사한다.

## 커밋 경계와 독립 실행

1. **OD–방문열 연결·검증**: `link_demand.py`, `test_link_demand.py`, 이 문서와 루트 진입점/README의 연결 안내. B 변환 파일이나 B 패키지를 import하지 않는다. 연결 CLI만으로 출처·범위·인원·저장 행 검증을 완료한다. `python -B -m unittest discover -s A.Part/tests -t A.Part -p test_link_demand.py -v`로 연결 회귀 테스트만 실행할 수 있다.
2. **B 시간대 입력 변환**: 첫 커밋 위에 `demand_bins.py`, `test_demand_bins.py`, 별도 실행 설명과 진입점/README의 변환 안내를 추가한다. 검증된 연결 결과를 읽는 별도 CLI이며 연결 단계에서 변환 코드를 참조하지 않는다. `python -B -m unittest discover -s A.Part/tests -t A.Part -p test_demand_bins.py -v`로 변환 검사를 실행한다. 두 단계의 CLI와 전체 A·기존 루트 테스트는 각각 실행 가능하다.

실제 commit·staging은 아직 하지 않았다. 시간 bin과 B/C 연결 제약의 상세 안내는 두 번째 커밋의 별도 문서에서 제공한다.
