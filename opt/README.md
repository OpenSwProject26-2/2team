# C: 직행 셔틀 운행안 탐색

관련 Issue #3 · M04 planning · M00/M05/M07 연결. Python 3.11 이상, 외부 의존성 없음.
이 구현은 합성 데이터에서 실행 가능한 C 프로토타입이다. **실제 A/B/D 통합 및 공용 계약 승인은 아직 완료되지 않았다.**

## 실행

저장소 루트에서 실행한다.

```bash
python3 -m unittest discover -s tests -t . -v
python3 -m opt --input opt/examples/synthetic.json
python3 -m opt --input opt/examples/synthetic.json --max-evaluations 100
```

`-t .`는 tests/opt를 실제 opt 패키지와 구분하기 위해 필요하다.
CLI는 JSON을 stdout에 출력하고 오류는 stderr에 오류 코드와 함께 출력한다(exit 2).
전체 전수 조합이 10,000개를 넘는 입력은 이 소규모 비교 CLI에서 거절한다.
라이브러리의 search()는 전수 비교 없이 독립 사용 가능하다.

## 제공 기능

- 기존 노선 균등 배분 및 원래 노선 수요 비례 정수 배분. 잔여는 최대 나머지, 동률은 노선 ID 순서.
- 후보의 방향·구간 전이·종점 회차·폐회로 복귀 검사와 제외 사유.
- 정적인 예상 운행편의 구간별 잔여 정원으로 중복 수요 없는 근사 평가.
- 평가 횟수 상한(기본 1,000), 결과 캐시, 탐욕적 추가 배분 및 1대 이동 탐색.
- 방문한 안 중 상위 3개 보존. 차량을 다 쓰지 않은 중간 안도 유효 비교 대상이다.
- 차량 미사용을 포함하는 소규모 전수 배분 비교. 최고값은 **고정 후보·동일 근사 평가 함수 안에서만** 정확하다.
- B 결과로 개발용 상위 3개 재평가 및 고정한 안의 별도 holdout 평가.

## 입력과 시간 의미

`models.Inputs`는 C 소유의 초안이다. `load_inputs`가 JSON의 필수 필드·참조·단위를 검사한다.
공용 contracts 또는 M00 문서는 수정하지 않았다. A/B가 실제 데이터를 연결할 때 이 형식을 먼저 리뷰해야 한다.

- 각 버전·입력 묶음 ID와 SHA-256 정규 JSON checksum으로 입력을 추적한다.
- `scope_mode=single_bus_leg`. 승객 도착 시각은 사전 생성된 값이며 카드 태그 원시값이 아니다.
- 시간은 Asia/Seoul service_date 자정 기준 정수 초. 86,400초 초과 허용.
- 데이터 split은 development / evaluation. 탐색·재선택·전수 비교는 development만 허용.
- `segments`: 방향별 travel_s와 distance_m. 누락을 0으로 채우지 않는다.
- `allowed_turns`: 연속 구간 쌍. 종점 회전과 반복 운행의 마지막→첫 구간 전이도 필요하다.
- `outbound`: 승객 수송 구간. `return_segments`: 공차 복귀 구간. 출발 거점과 종점의 회차 정보가 필요하다.
- 수송 구간 내 같은 정류장 재방문은 첫 버전에서 제외한다. M00 occurrence 처리는 후속 확장이다.
- `original_route_id`: 기준안의 노선별 수요 가중치를 위한 값. 원시 카드 식별자는 받지 않는다.
- 차량의 initial_stop과 return_stop은 같은 거점이어야 한다. 동일 정원·가용시간·거점 차량만 지원한다.

합성 예제는 정류장 A/B/C/D, 차량 2대(정원 2명), 구간당 60초·300m이다.
수요 도착은 0~120초, 운영은 0~480초이며 **실제 07~09시 서울 관측값이 아니다**.
정차·회차 0초는 손계산용이며 실제 실험 기본값으로 사용하지 않는다.

## 근사 평가의 명시적 한계

순환시간 = 수송 구간 주행 + 각 수송 출발 정류장의 정차 + 종점 회차 + 공차 복귀.
각 차량은 `start + floor(차량순번 × 순환시간 / 배정대수)`에 시작하고 순환시간마다 반복한다.
도착한 시점에 탑승 처리하고 정차 후 출발한다. 종점 회차에 하차 처리를 포함한다.
복귀 완료가 차량·운영 종료 시각을 넘는 운행은 제외한다.

수요를 도착 시각·수요 ID 순서로 배정하며 예상 도착이 빠른 운행편을 선택한다.
수요 묶음은 남은 정원만큼 분할된다. 예상 노선 선택과 선배정은 B의 실제 대기열/FIFO 처리와 다를 수 있다.
운행거리에는 빈 차량 운행과 복귀도 포함한다. 대기는 완료 인원 가중 평균이다.
0수요의 충족률과 완료 인원 0명의 평균 대기는 null이다. 실패 승객을 0분 대기로 넣지 않는다.
`estimated=true` 결과를 검증된 수송 효과나 실제 운행 가능성으로 표현하지 않는다.

## B 연결 포트

`integration.EvaluationPort = Callable[[Plan, Inputs], Assessment]`.
C → B는 불변 운행안과 동일 사전 생성 입력을 전달한다. B는 일정 생성, 시뮬레이션, 독립 검증 후 Assessment를 반환한다.

반드시 반환할 근거:
- plan_id, plan_revision, input_bundle_id, input_checksum
- schedule_feasible, independently_verified, violations
- served, total, p90_wait_s, distance_m

`rerank(inputs, search_result.top, b_port)`는 개발용 후보 최대 3개만 재평가한다.
일정 생성 실패·미검증·위반이 있는 안은 선택하지 않는다. 모두 실패하면 recommended=None이며 평가 결과를 보존한다.
불일치 ID·checksum·분모·비정상 지표는 오류로 처리한다. B 호출 예외는 숨기지 않고 호출자에게 전파한다.
결과는 운영자용 추천일 뿐 상태를 selected로 전환하지 않는다.

`evaluate_holdout(development, evaluation, chosen_plan, b_port)`는 배분을 고정해 한 번 평가한다.
평가 수요만 바꿀 수 있으며 교통망·차량·정책이 달라지면 거절한다. 원래 plan_id를 새 안의 source에 연결한다.
평가일의 다른 교통 조건은 별도 강건성 실험으로 설계한다.

`Plan.to_service_plan(inputs)`는 M00 최소 키를 가진 JSON 직렬화 가능한 dict를 반환한다.
status=draft, revision=1, plan_mode=simulation, schedule_status=pending_b.
module-owned routes에 vehicle_count를 추가하며 vehicle_duties/trips는 빈 목록이다.
이를 실행 가능한 완성 시간표로 오인하지 말고 B의 일정 생성 입력으로만 사용한다.

## 합성 예제 확인 결과

| 안 | 추정 충족 인원 / 평가 수요 |
| --- | --- |
| 균등 배분 | 7 / 9 |
| 수요 비례 | 6 / 9 |
| 탐색 | 7 / 9 |

전수 배분은 10개(차량 미사용 포함), 최고 충족 인원 7명, 탐색 충족 인원 격차 0명.
이는 탐색이 모든 실제 입력에서 최선이거나 기준안보다 우수하다는 증거가 아니다.

## 다음 통합 작업

1. A/B가 이 내부 입력 및 draft 출력 계약을 리뷰한다.
2. B 담당자가 실제 EvaluationPort를 구현하고 독립 검증 보고서와 연결한다.
3. 동일 RunInputs로 기준안·상위 후보의 실제 결과와 근사 순위를 비교한다.
4. 선택안을 고정한 뒤 미사용 날짜·시나리오로 최종 평가한다.

실제 데이터, 지자체·범용 AI 비교안, 시민 경로, 자동 후보 생성, MIP·강건 최적화는 이 PR 범위 밖이다.
