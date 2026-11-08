# 개발용 교통망 정제: 정류장·방문·원천 순서 구간

Draft PR #13 / Issue #10의 추가 작업이다. 팀 승인 전 개발용 정제 형식이며 최종 NetworkSnapshot·B/C 실행 입력이 아니다. 수요 연결·환승 연결·엔진 입력 변환·GTFS는 포함하지 않는다. 실제 순서·방향·출발지를 기존 엔진 제약에 맞춰 바꾸지 않는다.

## 입력과 실행 설정

우선 기존 추출의 `route_visits.csv`, `stops.json`을 사용한다. `validation_report.json`이 있으면 원천 route_stops 경로·체크섬을 계승한다. 이 작업은 OD 파일·원본 ZIP을 읽지 않는다. 기존 추출에 열·대응 근거가 부족한 경우에만 별도 원천 대조가 필요하다. 계승한 원천 행 대응은 현재 원본 CSV와 다시 비교한 결과가 아니다.

설정 구조는 [합성 설정](../examples/network_cleaning.synthetic.json)을 참고한다. 일반 코드에 실제 노선·정류장 ID는 없다.

| 설정 | 의미 |
| --- | --- |
| `schema_version` | `network-cleaning-config-v1` |
| `source_namespace` | string. 원천 ID namespace. 원천 문자열을 바꾸지 않음 |
| `input_dir`, `output_dir` | 기존 추출 입력 / 새 정제 출력 경로. 절대 경로 또는 현재 작업 디렉터리 기준 상대 경로 |
| `route_ids` | 대상 원천 노선 ID string 배열 |
| `selected_groups` | `route_id`, `source_route_name`의 배열. 해당 노선의 다른 원천 이름 그룹도 버리지 않고 별도 역할로 보존 |
| `distance.cumulative_unit` | 원천 누적거리 단위. 미확인일 때 null |
| `distance.unit_evidence` | `source`, `field`, `statement`. 확인한 단위 출처와 설명. 미확인일 때 null |

입력 방문 CSV는 기존 추출의 원천 행·일련번호·회사·순번·누적거리·기준일 열을 필요로 한다. 정류장 JSON은 정류장 ID당 하나의 객체와 후보 목록 형식이다. 필수 열·잘못된 순번·설정 오류는 값을 노출하지 않고 중단한다. 존재하지 않는 선택 그룹은 저장 후 품질 검사 실패로 표시한다.

저장소 루트에서 합성 예제를 실행할 수 있다. 출력 경로는 매번 새 경로여야 한다.

```powershell
python -B A.Part/run.py clean_network --config A.Part/data/examples/network_cleaning.synthetic.json
```

실제 실행 설정과 실제 결과는 **루트 `data/processed/` 아래**에만 둔다. 실행 전에 가상 파일 경로로 Git 제외 여부를 확인한다. 설정 파일 작성 자체가 처리 정책의 팀 승인을 뜻하지 않는다.

```powershell
git check-ignore -v -- data/processed/guro_network_cleaning_run/config.json data/processed/guro_network_cleaning_run/network/quality_report.json
python -B A.Part/run.py clean_network --config data/processed/guro_network_cleaning_run/config.json
```

A.Part에서 실행할 때는 `python -B -m data.clean_network --config ../data/processed/guro_network_cleaning_run/config.json`을 사용한다. **설정 안 상대 경로도 현재 작업 디렉터리 기준**이며 설정 파일 위치 기준이 아니다. 두 위치에서 같은 설정을 쓰려면 설정의 입력·출력을 절대 경로로 지정한다. 공개 합성 설정은 루트 실행 기준이다. 기존 실제 출력·입력 경로를 덮어쓰지 않는다.

## 정제 규칙과 원천 대응

- 정류장은 원천 정류장 ID로 구분한다. 이름·좌표·같은 장소 추정으로 서로 다른 ID를 합치지 않는다. 후보가 여러 개면 대표 이름·위경도는 null이다. 유효하지 않은 좌표 후보 수를 기록하고, 원천 파일 체크섬과 정류장 ID로 대응을 남긴다. 좌표 단위는 degree이며 CRS는 미검증(null)이다.
- 방문 식별자는 namespace + 노선 ID + 원천 노선명 + 정수 순번 + 정류장 ID의 결정론적 SHA-256 ID다. 같은 정류장이라도 다른 순번 방문은 다른 방문 ID다. 원천 노선명 그룹은 검증된 방향 ID가 아니며 `direction_id=null`이다.
- 동일 방문 키의 여러 행은 방문 하나에 연결한다. 그 안에서 `ROUTE_ID`, `ROUTE_NM`, `BSST_ORDER`, `BSST_ID`, `COMBINE_TYPE`, `TRCM_ID`, `TRCM_NM`, `BSST_SUM_DISTANCE`, `STDR_DE`의 **원문 문자열 전체가 같은** 근거만 같은 source variant로 묶는다. 일련번호·원천 CSV 줄 번호·이전 그룹 역할·선언 중복 수는 비교에서 제외하지만 모두 보존한다.
- 회사·거리 등 비교 필드가 다르면 별도 variant로 유지한다. 이를 공동운행으로 단정하지 않는다. 각 variant의 모든 원천 행 ID·입력 CSV 줄 번호·상위 원천 줄 번호·일련번호와 개수를 보존한다. source row ID는 입력 방문 파일 체크섬 + 입력 CSV 줄 번호를 사용한다.
- 같은 그룹·순번에 다른 정류장이 있으면 각각 별도 방문으로 남기고 순번 충돌로 기록한다. 그 순번 양쪽에 구간을 만들지 않는다. 순번 단절도 건너뛰지 않는다.
- 구간은 같은 원천 그룹의 연속 순번 두 슬롯이 각각 한 방문일 때만 만든다. 출발·도착 방문 및 정류장, 양쪽 모든 원천 행 ID를 연결한다. 이는 원천 순서 인접성이지 실제 운행 가능성 확인이 아니다.
- 반대 방향을 별도 방문열·검증된 방향으로 구성하지 않으므로 `reverse_direction_status=not_extracted`로 남긴다. 원천 전체 방문열에 반대 방향 운행 부분이 없다는 뜻은 아니다.

## 거리와 미확인 조건

[공식 교통카드 자료 설명](https://t-data.seoul.go.kr/dataprovide/trafficdataviewfile.do?data_id=20006)은 `BSST_SUM_DISTANCE`를 누적거리(m)로 설명한다. 실제 실행 설정에는 확인한 이 출처·필드·단위를 기록한다. 일반 처리 코드는 웹을 조회하지 않고 설정 근거를 산출물에 보존한다. 합성 예제는 합성 자료 정의를 근거로 한다.

같은 원천 그룹, 인접 순번, 같은 단일 기준일, 양 끝의 유일하고 유효한 음수 아닌 누적값, **양수 차이**, m 단위 및 근거가 모두 있을 때만 `distance_m`를 계산한다. 원래 누적거리 문자열 후보·원단위 차이·단위 근거는 함께 남긴다. 결측·음수·문자열 오류·누적값 후보 모호성·비증가·기준일 차이·단위 미확인은 각각 상태를 기록하고 `distance_m=null`로 둔다. 직선 거리나 0으로 보완하지 않는다.

주행시간은 `travel_time_s=null`, 회전·회차·복귀 조건은 `unknown`이다. 정류장 순서로 이 조건을 추정하지 않는다.

## 출력 4개와 검증

| 파일 | 주요 내용·자료형·단위 |
| --- | --- |
| `stops.json` | 정류장 ID string, 이름·좌표·행정구역 후보, 대표값/모호성, 좌표 degree·CRS 미검증 |
| `visits.json` | 방문/원천 그룹 ID string, 원천 노선·이름·순번 int·정류장 참조, source variants와 모든 원천 행 대응 |
| `segments.json` | 구간 ID와 양끝 방문·정류장·원천 행 참조, 순번, 거리 m 또는 null 및 근거, 주행시간 s 미확인 |
| `quality_report.json` | 입력·설정 SHA-256, 처리 규칙 버전, 설정, 원천 규모·정제 규모·중복·충돌·단절·모호성·미확인 항목, 저장 파일 재검증 |

저장한 세 정제 JSON을 다시 읽어 모든 입력 원천 행의 다중집합이 정확히 복원되는지 검사한다. 방문·정류장·구간 참조, 유일 ID, 모든 허용된 인접 쌍의 누락·추가, 거리 근거와 미확인 조건도 대조한다. 결과가 구조적으로 유효하다는 것과 실제 운행·엔진 입력이 완성됐다는 것은 다르다. 입력 오류 종료 코드는 2, 저장 후 검사 실패는 1이며 실패 디렉터리는 완성 결과로 사용하지 않는다.

```powershell
python -B -m unittest discover -s A.Part/tests -t A.Part -v
python -B -m unittest discover -s tests -t . -v
```

합성 테스트는 중복 variant·회사 차이·순번 충돌·재방문·단절·좌표 모호성·거리 오류·단위 미확인·원천 행 복원·저장 결과 변조·결정론성·입력 불변과 덮어쓰기 방지를 검사한다. 실제 행 수·노선·정류장 ID를 기대값에 사용하지 않는다.

B·C 연결에는 검증된 방향/버전·허용 전이·회차/복귀·방향별 주행시간·운영 조건·차량·운행편과 별도의 수요 발생 입력이 필요하다. 이번에는 수요 연결을 수행하지 않는다.
