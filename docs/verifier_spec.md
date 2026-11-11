# M05 독립 검증기(P5) 명세

- 상태: **명세 초안 · 구현 아님 · 팀 검토 필요**. 작성 2026-11-09, 근거 브랜치 `feat/5-scale-pilot`(`6f8e0bb`).
- 대상: M05 엔진(`sim/`) 산출물. 검증기는 M07 영역이며 **엔진 작성자가 아닌 사람**이 구현한다.
- 이 문서는 무엇을, 어떤 입력으로, 어떤 정답과 비교해 검사하는지만 정한다. 엔진 동작을 바꾸거나
  `independently_verified=True`를 부여하지 않는다. 검증기 통과 전 모든 M05 수치는 "독립 검증 전 잠정"이다.

## 1. 독립성 규칙

| 규칙 | 내용 |
| --- | --- |
| 작성자 분리 | 엔진(`sim/engine.py`, `sim/dispatcher.py`, `sim/metrics.py`, `sim/arrivals.py`) 커밋 작성자가 검증기 코드를 작성하거나 단독 승인하지 않는다. 리뷰 참여는 가능. |
| 코드 분리 | `sim`, `opt` 패키지를 import하지 않는다. 엔진 함수·자료구조·상수를 복사하지 않는다. 공유하는 것은 이 명세와 파일 형식뿐이다. |
| 입력 경로 | 파일만 읽는다(§2). 엔진을 호출해 얻은 값을 정답으로 쓰지 않는다. |
| 정답 출처 | 손계산 fixture(§5) · 검증기 자체 brute-force 재계산(§4) · 불변조건(§3). golden 로그(`tests/sim/golden`)는 엔진 출력이므로 회귀 기준일 뿐 정답이 아니다. |
| 실패 처리 | 하나라도 실패하면 보고서 `pass=false`. 부분 통과를 "검증됨"으로 표기하지 않는다. |

## 2. 입력 파일 계약

| 파일 | 생성 | 필드 |
| --- | --- | --- |
| `scenario/network.json` | `python -m sim.harness generate` | `trips[]`(trip_id, vehicle_id, capacity, stops[], arrivals[], departures[], return_s, distance_m), `route_of{trip_id: route}`, `stop_area{stop: area}`, `routes`, `spec`, `seed` |
| `scenario/demand_bins.csv` | 동일 | aggregate_id, origin, destination, bin_start_s, bin_width_s, passenger_count |
| `scenario/scenario_manifest.json` | 동일 | 파일 sha256, rows, passengers, demand_spec |
| `run/realization.jsonl` | `sim.harness run --write-realization` | 1행 메타(generator, seed, rows, source_checksum, checksum, passenger_count), 이후 `[passenger_id, origin, destination, time_s]` |
| `run/measurement.json` | `sim.harness run` | config(max_wait_s, max_journey_s, drain_s), policy(max_headway_s, wait_threshold_s), realization/run checksum, env |
| `run/events.jsonl` | `--sink jsonl` | time_s, phase, stable_id, kind, passenger_id, vehicle_id, occupancy |
| `run/passenger_outcomes.csv` | harness | passenger_id, origin, destination, time_s, outcome, reason, physical, vehicle_id, boarding_s, arrival_s, refused |
| `run/metrics.json` | `sim.metrics.summarize` | M1, M2, M3, M5, M6, G |

검증기는 realization을 (a) `realization.jsonl`에서 읽거나 (b) `demand_bins.csv`+seed로 V1 규칙을 독립 재구현해
만든다. (b)가 독립성이 더 높으며 두 결과가 같아야 한다.

사건 의미(엔진 정의, 검증기는 이 정의를 독립 구현한다):

- phase: 0 차량 도착·하차, 1 승객 발생, 2 승차, 3 차량 출발, 4 기한 만료. 같은 time_s에서 phase 오름차순,
  같은 phase에서 stable_id 문자열 오름차순.
- stable_id: 정류장 방문 사건 `<trip_id>:<방문 index 8자리 0채움>`, 발생 `generate:<pid>`, 기한 `wait:<pid>`·`journey:<pid>`.
- kind: `alighted`·`vehicle_arrived`(phase 0, 같은 키에서 alighted가 먼저), `generated`(1), `boarded`(2),
  `departed`(3), `expired`(4).
- occupancy: 해당 사건 직후 차량 탑승 인원(alighted는 하차 후, boarded는 승차 후). 승객 사건은 null.
- 기한은 inclusive: 발생 t, t+max_wait_s 시각 phase 2 승차는 성공(만료는 phase 4). t+max_journey_s 시각
  phase 0 하차는 served.
- drain_s 초과 시각 사건은 실행하지 않는다. 끝까지 확정되지 않은 승객은 censored.

## 3. 검사 항목

| ID | 검사 | 판정 |
| --- | --- | --- |
| V1 | R5 발생 재현 | 행별 인원 정확 보존, OD 동일, `bin_start_s ≤ time_s < bin_start_s + bin_width_s`, passenger_id = `json.dumps([aggregate_id, index], separators=(',', ':'))`, index 0..count−1 유일. 생성기 `uniform_bin_sha256_v1`: base = SHA-256(`json.dumps([generator, seed, aggregate_id])`(기본 구분자) ‖ `\0`); 승객 index와 counter c(0부터)에 대해 SHA-256(base ‖ index 8바이트 big-endian ‖ c 4바이트 big-endian)의 앞 8바이트 x; x < 2^64 − (2^64 mod w)이면 offset = x mod w, 아니면 c+1로 반복. source_checksum = SHA-256(aggregate_id 정렬 행 dict 목록의 `json.dumps(sort_keys=True, separators=(',', ':'))`). realization checksum = SHA-256(`json.dumps([generator, seed, source_checksum], separators=(',', ':'))` 뒤에 승객마다 `\n` + `json.dumps([pid, origin, destination, time_s], separators=(',', ':'))`, 승객 순서는 aggregate_id 정렬 후 index 순). |
| V2 | 사건 순서 | 키 (time_s, phase, stable_id) 엄격 증가, phase∈{0..4}, 모든 time_s ≤ drain_s, 정수. |
| V3 | 일정 일치 | 각 trip의 i번째 방문에서 arrivals[i] ≤ drain_s이면 vehicle_arrived 정확히 1회, departures[i] ≤ drain_s이면 departed 정확히 1회. 일정에 없는 방문 사건 0. |
| V4 | 승객 생애 | generated 정확히 1회(time_s = 발생 시각). boarded ≤ 1회, 출발지 방문에서만, 목적지가 그 trip의 이후 정류장. 승차한 차량이 목적지를 방문하면(≤ drain) alighted 정확히 1회. 승차 전 하차·재승차·복수 차량 탑승 없음. |
| V5 | 정원 | 사건으로 재구성한 차량별 탑승 인원 = 각 사건의 occupancy, 0 ≤ occupancy ≤ capacity. |
| V6 | FIFO·비유휴 | 승차 방문의 자격 승객 = 그 정류장에서 대기 중(발생 후, 미승차, 결과 미확정)이고 목적지가 이 trip의 이후 정류장. 승차 집합 = 자격 승객을 (time_s, passenger_id) 정렬한 앞 min(빈 좌석, 자격 수)명, 순서도 같음. 빈 좌석이 남았는데 자격 승객이 남으면 실패. |
| V7 | 기한 | 미승차로 t+max_wait_s에 도달하면 그 시각 phase 4 `expired`. 승차자에게는 wait 만료 없음. 미확정 승객은 t+max_journey_s에 `expired`(탑승 중이면 vehicle_id 포함). 같은 시각 두 기한은 `journey:`가 먼저이며 뒤의 것은 사건을 남기지 않는다. |
| V8 | 결과 | served ⇔ 만료 전 목적지 하차. unserved ⇔ drain 이내 만료 사건. censored ⇔ 둘 다 아님. 배타·합 = N. 여정 초과 후 실제 하차는 physical=arrived, arrival_s 보존. drain 시점 미하차 여정 초과자는 unserved·physical=onboard. |
| V9 | M1 | total>0·censored=0이면 rate=served/total. censored>0이면 rate=None, lower=served/total, upper=(served+censored)/total. total=0이면 모두 None. |
| V10 | M2 사유 | unserved마다 독립 판정: ① 일정 전체에서 출발지 방문 i, 목적지 방문 j>i인 trip이 없으면 `no_connection`; ② 그런 trip의 출발지 도착 시각 중 [time_s, drain_s]인 것이 없으면 `service_end`; ③ 아니면 결과를 확정한 만료 사건 종류(`journey`/`wait`). 사유별 인원이 CSV·metrics와 일치. |
| V11 | M2 만차 거절 | 승차 처리 후 빈 좌석 0인 방문(도착 시 이미 만석 포함)마다 그 trip의 이후 정류장이 목적지인 남은 자격 승객 수의 합 = `refusal_events`. 승객별 `refused` 횟수와 결과별 거절 경험 승객 수 일치. |
| V12 | M3 | 모집단 served·boarded 각각 대기 = boarding_s − time_s의 n, 평균, nearest-rank P90(정렬 후 ⌈0.9n⌉번째), `> threshold` 수·비율. 미탑승 unserved 사유별 수. |
| V13 | M5 | 출발 정류장 지역(stop_area, 미매핑=`unknown`)별 total/served/unserved/censored/served_rate, total>0 지역 중 최저 (rate, area). |
| V14 | M6 | 차량별: return_s ≤ drain_s인 trip 수·Σ(return_s − arrivals[0])·Σdistance_m, boarded 수, served 하차 수, 공차시간 = 탑승 0으로 출발한 구간 중 다음 도착 ≤ drain_s인 구간 시간 + 완료 trip의 회송(return_s − 마지막 departure). |
| V15 | G/R12 | route_of 노선별·정류장별 연속 departure 간격 > max_headway_s 개수 = 0이면 pass. metrics의 pass·위반 수·노선별 최대 간격과 일치. 창 경계 간격은 미검사(TODO 정책). |
| V16 | G 기타 | 차량 수 ≤ 가용 fleet(R10), capacity = 시나리오 값(R11), trip 시각이 운영창 안(R14), 차량 duty 비중첩·같은 출발 거점. R13 방향별 구간은 M02 산출물 확보 전 "미검증"(통과 아님). |
| V17 | 체크섬 | scenario 파일 sha256, realization checksum, 산출물 파일이 measurement·manifest 기록과 일치. |

## 4. brute-force 재계산 oracle (N ≤ 10,000)

엔진과 다른 구조로 구현한다. 대기열·지연 스트림을 쓰지 않는 단순 반복을 권장한다.

1. 모든 사건 키를 만든다: 승객별 (t,1,generate), (t+W,4,wait), (t+J,4,journey), 방문별 (a,0), (a,2), (d,3).
   drain_s 이하만 남겨 전체 정렬한다.
2. 순서대로 처리하며 상태를 승객 배열·차량 배열로만 유지한다. 승차 시 **전체 승객을 선형 탐색**해 자격 승객을
   고르고 정렬한다(성능 목표 없음).
3. 결과·사유·거절·지표를 §3 정의대로 계산해 엔진 산출물과 승객 단위·지표 단위로 비교한다.

10,000명 초과는 V1–V8·V11·V15·V17 불변조건과 checksum만 적용하며 "정확도"라는 표현을 쓰지 않는다.

## 5. 손계산 fixture (검증기 작성자가 재검산할 것)

아래 값은 사람 손계산이며 엔진 출력에서 복사하지 않았다. 검증기 작성자는 표를 다시 계산해 서명한다.

### F1 직행 셔틀 (`sim/fixtures/direct_shuttle.json`)

A→B 정원 2, 출발 0·10초, 주행 5초, 승객 3명 t=0, W=10, J=15, drain 20.

| 승객 | boarding_s | arrival_s | 결과 |
| --- | --- | --- | --- |
| ["d",0] | 0 | 5 | served |
| ["d",1] | 0 | 5 | served |
| ["d",2] | 10 | 15 | served |

served P90(대기 [0,0,10]) = 10. 변형: W=9 → ["d",2] unserved(wait). J=14 → ["d",2] unserved(journey),
arrival_s 15 보존. drain 12 → ["d",2] censored(onboard). W=J=9 → ["d",2] unserved, 사유 journey(동시각
우선), refusal_events 1(첫 차 만석 시 남은 1명).

### F2 지표 fixture (`tests/sim/test_metrics.py`)

t1 v1 정원 2: A(도착 100/출발 110)→B(200/210)→C(300), return 400, 1000 m.
t2 v1 정원 2: A(700/710)→B(800/810)→C(900), return 1000, 1000 m.
t3 v2 정원 5: A(400/410)→B(500/510)→C(1000), return 1050, 500 m.
W=300, J=600, drain 1100. 지역 A·B=dong-1, C·D=dong-2.

| 승객 | OD·발생 | 결과 | 사유 | 대기 | refused |
| --- | --- | --- | --- | --- | --- |
| ["a",0], ["a",1] | A→C 0 | served (도착 300) | – | 100 | 0 |
| ["a",2] | A→C 0 | unserved | wait (300 만료) | – | 1 (t1@A 만석) |
| ["b",0] | B→C 150 | unserved | wait (450 만료, t3 B=500) | – | 1 (t1@B 도착 시 만석) |
| ["c",0] | A→D 0 | unserved | no_connection | – | 0 |
| ["d",0] | C→A 50 | unserved | no_connection | – | 0 |
| ["e",0] | A→B 750 | unserved | service_end (A 마지막 방문 700, 1050 만료) | – | 0 |
| ["f",0] | B→C 820 | censored (waiting) | – | – | 0 |
| ["g",0] | A→C 380 | unserved | journey (980 만료, 1000 도착 보존) | 20 | 0 |

- M1: rate None, lower 2/9, upper 3/9.
- M2: no_connection 2, service_end 1, journey 1, wait 2. refusal_events 2, 거절 경험 unserved 2.
- M3(임계 60): served n 2·평균 100·P90 100·초과 2(1.0). boarded n 3·평균 220/3·P90 100·초과 2.
  미탑승 unserved: no_connection 2, service_end 1, wait 2.
- M5: dong-1 total 8·served 2·unserved 5·censored 1·rate 0.25. dong-2 total 1·rate 0 → 최저 dong-2.
- M6: v1 trips 2·boarded 2·served 2·시간 600·거리 2000·공차 380(100+90+90+100).
  v2 trips 1·boarded 1·served 0·시간 650·거리 500·공차 50.
- R12(t1·t2=r1, t3=r2): r1 최대 간격 600. 상한 600이면 pass, 599이면 A·B·C 3건 위반.

### F3 동시각·경쟁 fixture (`tests/sim/golden_cases.py::tie_case`)

같은 시각 발생 승객, 같은 정류장에 동시에 도착하는 두 trip, 공유 정류장, 정원 구속, 여정 기한 < 대기 기한.
TODO: 엔진 작성자 외 담당자가 손계산 표를 작성해 여기에 추가한다.

## 6. 변조 검출 사례 (모두 검출해야 통과)

정상 산출물(F1, F2, 합성 complex 1천 명 `--sink jsonl --write-realization` 실행)을 복사해 한 가지씩 바꾼다.

| ID | 변조 | 기대 검출 |
| --- | --- | --- |
| T1 | boarded 사건 1줄 삭제 | V4, V5, V8 |
| T2 | 같은 passenger_id의 generated 사건 복제 | V2, V4 |
| T3 | occupancy를 capacity+1로 수정 | V5 |
| T4 | 같은 방문의 두 boarded 승객 순서 교환 | V6 |
| T5 | outcomes CSV served→unserved 1명 | V8, V9 |
| T6 | vehicle_arrived time_s를 일정보다 1초 앞당김 | V2, V3 |
| T7 | 출발지가 아닌 정류장 방문에서 boarded | V4 |
| T8 | 목적지가 아닌 정류장에서 alighted | V4 |
| T9 | 빈 좌석이 있는 방문에서 자격 승객 승차를 삭제하고 이후 사건을 일관되게 수정 | V6 |
| T10 | metrics M1.served +1 | V9 |
| T11 | metrics M2.refusal_events −1 | V11 |
| T12 | 사유 wait→no_connection 1명(CSV·metrics 동시) | V10 |
| T13 | realization time_s를 bin 밖으로 이동하고 checksum 재계산 | V1 |
| T14 | demand_bins 한 행 인원 −1 | V1, V17 |
| T15 | 인접 두 사건 줄 순서 교환 | V2 |
| T16 | drain_s 이후 시각 사건 1줄 추가 | V2 |
| T17 | 배차간격 위반 일정인데 metrics G.R12.pass=true | V15 |
| T18 | censored 승객 1명을 CSV에서 삭제 | V8 |
| T19 | outcomes CSV 한 승객 destination 변경 | V1, V4 |
| T20 | expired 사건을 기한보다 1초 늦게 기록 | V7 |

## 7. 출력과 수용 기준

- `verifier_report.json`: 검사 ID별 pass/fail, 검사 대상 수, 위반 수, 앞 20건 위반 예, 입력 파일 sha256,
  검증기 커밋·작성자, 엔진 커밋.
- 수용: F1·F2(및 F3 작성 후) 100% 일치, T1–T20 전부 검출, 합성 complex 1천·1만 명에서 brute-force와
  승객 단위 완전 일치, `sim`·`opt` import 0건(정적 검사).
- 통과해도 실제 운행 승인·현장 성능 검증이 아니다. `independently_verified` 부여는 M07 절차가 정한다.

## 8. 미정·TODO

- F3 손계산 표(엔진 작성자 외).
- R12 창 경계 간격 정책, M6 공차거리(Trip에 구간 거리 없음).
- R13 방향별 구간 검증 자료(M02).
- C adapter의 `Demand.arrival_s` 의미(정확 시각 / bin 시작) ADR.
- 검증기 담당자·일정은 팀이 정한다(이 문서는 담당을 지정하지 않는다).
