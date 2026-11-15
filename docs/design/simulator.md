# M05 셔틀 시뮬레이터 + 독립 검증기 설계

## 구현 순서 (5줄)

1. **2단계-a** `feat/sim-core`: `sim/models.py`·`io.py`·`traveltime.py` → `timetable.py`(§1). 테스트 T6·T7 시간표 표가 먼저 통과해야 한다.
2. **2단계-b** 같은 브랜치: `arrivals.py`(§2) → `engine.py`(§3·§4) → `eventlog.py`·`metrics.py`·`run.py`(§6). 테스트 T1–T5, 결정론 테스트, 속도 측정.
3. **3단계** `feat/sim-validator`: `evaluation/validator.py`(§8). `busstrike.sim`을 import하지 않고 입력 파일+로그만 읽는다. T1–T4 로그 통과 + 변조 로그 검출 테스트.
4. **4단계** `feat/sim-gtfs-export`: `sim/gtfs.py`(§6.4). `timetable.json` → routes·trips·stop_times·calendar.
5. **5단계** `feat/sim-planning-bridge`: `sim/adapters/planning_bridge.py`(C `Plan`·`Inputs` → 시뮬 입력, `EvaluationPort` 구현), `sim/curve.py`(§7 S(n)), 근사–시뮬 비교 리포트.

## 문서 상태

- 상태: **설계 초안 · 팀 검토 대기 · 2단계(핵심 엔진, `feat/sim-core`)·3단계(독립 검증기, `feat/sim-validator`)·4단계(셔틀 GTFS, `feat/sim-gtfs-export`) 구현.** 5단계(C 연결)는 미착수. 이 문서의 값은 승인된 결정이 아니다(AGENTS.md §1). D02·D03·D06·D07은 §11의 권장값을 "가정"으로 쓰고, 승인 전에는 코드 상수로 박지 않고 Scenario 입력으로만 받는다.
- 요구사항 원문: [simulator-brief.md](simulator-brief.md) · 진행 기록: [simulator-progress.md](simulator-progress.md)
- 관련 모듈: M05(시뮬레이터), M07(독립 검증), M04 연결부, M00 v0.1 계약. 공용 계약(`docs/contracts`, `src/busstrike/contracts`)은 바꾸지 않는다. 아래 스키마는 모두 M00이 "module-owned"로 남긴 영역의 M05 소유 형식이다.
- 시뮬레이션 결과는 실제 운행 성능·승인 근거가 아니다.

---

## 0. 공통 전제

### 0.1 용어

| 용어 | 뜻 |
| --- | --- |
| leg | 노선의 한 방향 운행 구간(정류장 순서열). `in_service=true`면 영업(승객 탑승), `false`면 공차 |
| cycle | 한 차량이 노선의 leg를 처음부터 끝까지 한 바퀴 도는 것 |
| trip | 한 차량이 한 cycle의 한 leg를 달리는 것. 영업 trip만 GTFS로 내보낸다 |
| duty | 한 차량의 trip 순서열 |
| anchor | 노선 첫 leg의 첫 정류장. cycle이 여기서 시작하고 끝난다 |
| visit | 차량이 정류장에 서는 한 번. 차량별 0부터 세는 일련번호 |
| `W_s`, `W_e` | `operating_window.start_s`, `end_s` |
| 스냅숏 | 차량이 정류장에 도착한 순간. 그 순간 대기 중인 승객만 탄다(§3.3) |

시간은 모두 `service_date` 자정 기준 정수 초, 거리는 m, 인원은 정수다(M00 §2.2). 시뮬레이터 안에는 부동소수 계산이 없다(지표의 평균·비율만 예외, §6.3).

### 0.2 파이프라인과 산출물

```text
[준비 단계 — 실행 전 1회, 운행안과 무관]
 DemandProfile(소수 유량) ──arrivals.py──▶ arrivals.jsonl ─┐
 NetworkSnapshot·TPSS ──────(M02)────────▶ travel_times.json ├─▶ run_inputs.json (checksum)
 scenario.json ────────────────────────────────────────────┘
[실행 단계 — 운행안마다, 난수 없음]
 plan.json ──timetable.py──▶ timetable.json ──engine.py──▶ events.jsonl ──metrics.py──▶ metrics.json
                                   │                            │                         │
                                   └──gtfs.py──▶ gtfs/          └────── validator.py(M07, 독립) ──▶ validation.json
                                                                 run.json = SimulationResult + 해시
```

- 엔진은 `timetable.json`만 실행한다. 운행안의 출처(`source`)는 엔진·생성기·검증기 어디서도 읽지 않는다(공정성, §1.6).
- 형식: 작은 구조 문서는 JSON(`sort_keys=True`), 행이 많은 것은 JSONL(키 순서 고정). 공통 직렬화 규칙은 `json.dumps(x, ensure_ascii=False, separators=(",", ":"))`, UTF-8, 줄 끝 `\n`(마지막 줄 포함). 해시는 파일 바이트의 SHA-256 hex. (C의 프로토타입과 같은 방식이며, 팀 전역 해시 채택을 주장하지 않는다.)
- 어떤 산출물에도 실행 시각·호스트명·절대 경로를 넣지 않는다.

### 0.3 입력 스키마

**`scenario.json`** (M00 `Scenario` + M05 소유 세부)

| 필드 | 형식 | 의미 |
| --- | --- | --- |
| `scenario_id`, `service_date`, `timezone` | string | M00 그대로. `timezone`은 `Asia/Seoul`만 허용 |
| `network_snapshot_id`, `demand_profile_id` | string | 기록용 참조 |
| `strike_route_ids` | string[] | 기록용. 엔진은 읽지 않는다 |
| `operating_window` | `{start_s:int, end_s:int}` | `W_s < W_e` |
| `fleet.authorized_count` | int | 인가대수(기록용) |
| `fleet.vehicles` | `Vehicle[]` | **가용** 차량. 엔진은 이것만 쓴다 |
| `policy` | `Policy` | 아래 표 |

`Vehicle`

| 필드 | 형식 | 의미 |
| --- | --- | --- |
| `vehicle_id` | string | 유일 |
| `vehicle_class` | string | 차급(기록·집계용) |
| `seat_capacity` | int ≥ 1 | 좌석 정원 |
| `standing_capacity` | int ≥ 0 | 입석 정원. 전세버스는 0 |
| `available_start_s`, `available_end_s` | int | 가용 시각 |
| `initial_stop_id` | string \| null | null = 배정 노선의 anchor에 미리 배치(§11 D03 권장) |
| `end_stop_id` | string \| null | null = anchor에서 종료 |

`Policy`

| 필드 | 형식 | 쓰는 곳 | 의미 |
| --- | --- | --- | --- |
| `max_wait_s` | int ≥ 0 | 실현기 | 승객별 `max_wait_s`의 기본값(D07) |
| `max_journey_s` | int ≥ 0 | 지표 | 대기+차내 한도. 넘으면 수송했어도 "충족" 아님(D07) |
| `allowed_transfers` | int | 엔진 | v1은 0만 허용, 아니면 `OUT_OF_SCOPE`(D06) |
| `arrival_method` | string | 실현기 | v1은 `"systematic"`만(§2) |
| `dwell_base_s` | int ≥ 0 | 생성기·엔진 | 정류장 기본 정차 |
| `dwell_per_boarding_s` | int ≥ 0 | 엔진 | 승차 1인당 |
| `dwell_per_alighting_s` | int ≥ 0 | 엔진 | 하차 1인당 |
| `turnaround_s` | int ≥ 0 | 생성기·엔진 | 영업 leg 종점 회차 최소 시간 |
| `cycle_rest_s` | int ≥ 0 | 생성기·엔진 | cycle 사이 anchor 휴식 최소 시간 |
| `unknown_link` | string | 로더 | `"error"`만 허용. 없는 구간은 0초로 채우지 않는다 |

**`travel_times.json`** (`travel_time_realization_uri`가 가리키는 것)

| 필드 | 형식 | 의미 |
| --- | --- | --- |
| `schema_version` | int | 1 |
| `network_snapshot_id` | string | 기준 스냅숏 |
| `valid_from_s`, `valid_until_s` | int | `valid_from_s ≤ W_s`, `valid_until_s ≥ W_e` 아니면 `DATA_INCOMPLETE` |
| `segments[]` | object | `(from_stop_id, to_stop_id)` 오름차순, 중복 금지 |
| `segments[].from_stop_id`, `.to_stop_id` | string | 방향 구간(버스 GTFS 연속 정류장) |
| `segments[].distance_m` | int ≥ 0 | 구간 거리 |
| `segments[].profile` | `[[bin_start_s, travel_s], …]` | `bin_start_s` 오름차순, 첫 값 ≤ `valid_from_s`, `travel_s ≥ 1` |

**`arrivals.jsonl`** (`arrival_realization_uri`) — 승객 1명당 1줄, `(arrival_s, passenger_id)` 오름차순

| 키(이 순서) | 형식 | 의미 |
| --- | --- | --- |
| `passenger_id` | string | §2.4의 규칙으로 만든 유일 ID |
| `kind` | `"access"` \| `"egress"` | 정류장→역 / 역→정류장 |
| `origin_stop_ids` | string[] (정렬) | 탈 수 있는 정류장 집합. access는 원소 1개 |
| `dest_stop_ids` | string[] (정렬) | 내리면 성공인 정류장 집합. `origin`과 겹치면 `INPUT_INVALID` |
| `arrival_s` | int | 정류장 도착 시각. `W_s ≤ arrival_s ≤ W_e` |
| `max_wait_s` | int | 이 승객의 대기 한도 |

**`run_inputs.json`** (M00 `RunInputs` + 실현 메타)

| 필드 | 의미 |
| --- | --- |
| `input_bundle_id`, `scenario_id`, `seed` | M00 그대로 |
| `arrival_realization_uri`, `travel_time_realization_uri` | 위 두 파일 |
| `arrival_meta` | `{method, bin_length_s, passenger_count, sha256}` |
| `travel_time_sha256`, `scenario_sha256` | 각 파일 해시 |
| `checksum` | `sha256(canonical_json({"arrivals": …, "travel_times": …, "scenario": …}))` — 값은 세 파일의 SHA-256 hex, 해시 입력은 줄바꿈 없는 UTF-8 |

- 두 URI는 `run_inputs.json`이 있는 디렉터리 기준 상대 경로(POSIX `/`)다. `scenario.json`은 실행 때 따로 받고 `scenario_sha256`으로 묶는다.
- 실행(`run.py`)은 세 해시·`checksum`·`arrival_meta.passenger_count`·`scenario_id`가 실제 파일과 맞는지, `travel_times.network_snapshot_id`가 scenario와 같은지 확인하고, 어긋나면 `INPUT_INVALID`로 멈춘다.

**`plan.json`** (M00 `ServicePlan`, `routes`는 M05가 읽는 형식)

| 필드 | 형식 | 의미 |
| --- | --- | --- |
| `plan_id`, `scenario_id`, `revision`, `status` | M00 | `(plan_id, revision)` 불변 |
| `source` | string | 기록용. **엔진·생성기는 읽지 않는다** |
| `routes[].route_id` | string | 유일 |
| `routes[].legs[]` | `{in_service:bool, stop_ids:string[]}` | leg 순서 = `leg_index`. 정류장 2개 이상, 연속 leg는 끝=시작, 마지막 leg 끝 = 첫 leg 시작(anchor) |
| `routes[].vehicle_count` | int ≥ 0 | 배정 대수 n |
| `routes[].vehicle_ids` | string[] (선택) | 명시 배정. 없으면 §1.4 규칙 |
| `trips`, `vehicle_duties` | 배열 | 비어 있으면 §1 생성기가 채운다. 있으면 `timetable.json`과 같은 형식 |

노선 유형은 leg 조합으로만 표현한다. 생성기·엔진에 유형별 분기는 없다.

| 유형 | legs |
| --- | --- |
| 피더형(편도+회송) | `[영업 A→…→역, 공차 역→…→A]` |
| 순환형 | `[영업 A→…→A]` (첫 정류장 = 마지막 정류장) |
| 두 역 연결형 | `[영업 역1→…→역2, 영업 역2→…→역1]` |

C의 `Route`는 `outbound` 구간열 → 영업 leg, `return_segments` → 공차 leg로 옮긴다(5단계 어댑터).

### 0.4 오류

M00 §4의 코드를 쓴다. `SimError(code, message, details)` 하나로 던진다.

| 상황 | 코드 |
| --- | --- |
| 필드·단위·참조 오류, 집합 겹침, 창 밖 도착 | `INPUT_INVALID` |
| 구간·시간대 누락, 구간 시간 범위 부족 | `DATA_INCOMPLETE` |
| 대수 초과, leg 불연속, 차량 중복 배정, 차고지 공차 자료 없음 | `PLAN_INFEASIBLE` |
| 환승 > 0, 지원하지 않는 발생 방식 | `OUT_OF_SCOPE` |

운영시간 안에 cycle이 하나도 들어가지 않는 노선은 오류가 아니다. trip 0개로 편성되고 그 승객은 `no_service`가 된다.

---

## 1. 시간표 생성기

### 1.1 결정

- 노선마다 **고정 주기 P**를 하나 정하고, 차량 k(0…n−1)를 `floor(k·P/n)`씩 어긋나게 출발시켜 주기마다 반복한다.
- P = 운영시간 안 여러 시각에서 계산한 계획 순환시간의 **최댓값**.
- 배차간격은 `P/n`이다(오프셋을 초 단위로 내림하므로 인접 간격은 `⌊P/n⌋` 또는 `⌈P/n⌉`). 첫차는 `start = max(W_s, 배정 차량의 가용 시작)`에 anchor를 떠난다.
- 예정 시각은 "스냅숏 시각"이다: 정류장에 도착해 승차를 받는 순간. 정차는 다음 구간 출발 전에 붙는다.
- cycle은 통째로만 편성한다. 마지막 정류장(anchor) 도착 + 승하차 여유가 종료 시각을 넘으면 그 cycle과 이후는 만들지 않는다.
- 모든 차량 이동(공차 포함)은 `[max(W_s, 가용 시작), min(W_e, 가용 종료)]` 안에 있어야 한다.

이유
- C의 근사 평가(`evaluator.py`: `start + phase*cycle//count`, 주기 `cycle`, `departure + cycle <= end`)와 **같은 위상·같은 종료 규칙**이다. 1인당 정차가 0이고 구간 시간이 고정이면 시간표가 초 단위로 같아서, §7의 근사–시뮬 차이를 "탑승 규칙·정차 모델" 탓으로만 돌릴 수 있다.
- 최댓값 P는 계획상 지각 출발을 없앤다. 남는 시간은 anchor 대기로 흡수된다.
- 정수 나눗셈만 쓰므로 결정론적이다.

### 1.2 계획 순환시간

```text
planned_cycle(route, t0) -> (leg_times, finish, ready)
    t = t0
    for leg in route.legs:
        times = [t]                              # 첫 정류장 스냅숏
        for (a, b) in 연속 정류장쌍(leg.stop_ids):
            dep = t + (dwell_base_s if leg.in_service else 0)
            t   = dep + tt.lookup(a, b, dep)     # 출발 시각 기준 조회(§3.5)
            times.append(t)
        leg_times.append(times)
        finish = t                               # 이 leg 마지막 정류장 도착
        t = t + (turnaround_s if leg.in_service else 0)
    ready = t + cycle_rest_s                     # 다음 cycle을 시작할 수 있는 가장 이른 시각
    return leg_times, finish, ready
```

- 영업 leg: 마지막 정류장을 뺀 모든 정류장에서 `dwell_base_s`. 종점 하차 시간은 계획에 넣지 않고 `turnaround_s`가 덮는다.
- 공차 leg: 정차 0, 회차 0. 중간 정류장은 통과만 한다(구간 시간 조회 지점).

### 1.3 주기·출발·종료

```text
build_route_trips(route, vehicles, window, policy, tt) -> trips
    n = len(vehicles);  if n == 0: return []
    start = max(W_s, max(v.available_start_s for v in vehicles))
    samples = [start] + [b for b in tt.bin_starts() if start < b < W_e]   # 오름차순, 중복 제거
    P = max(planned_cycle(route, s).ready - s for s in samples)
    n_rev = 영업 leg 수
    for k, v in enumerate(vehicles):             # vehicles는 vehicle_id 오름차순
        end   = min(W_e, v.available_end_s)
        allow = (dwell_per_boarding_s + dwell_per_alighting_s) * (v.seat_capacity + v.standing_capacity) * n_rev
        m = 0; prev_ready = None
        loop:
            s = start + (k * P) // n + m * P
            if prev_ready is not None: s = max(s, prev_ready)
            leg_times, finish, ready = planned_cycle(route, s)
            if finish + allow > end: break       # 이 cycle과 이후는 편성하지 않음
            for li, leg in enumerate(route.legs):
                emit Trip(trip_id=f"{route_id}-{vehicle_id}-{m:03d}-{li}", route_id, vehicle_id,
                          cycle_index=m, leg_index=li, in_service=leg.in_service,
                          stop_times=[(seq, stop_id, leg_times[li][seq]) …])
            prev_ready = ready; m += 1
```

- `allow`(승하차 여유): 실행 중 1인당 정차로 생기는 지연이 종료 시각을 넘기지 않게 잡는 여유. leg당 만차 1회분이다. 1인당 정차가 0이면 0이 되어 C의 규칙과 같아진다.
- `route_id`·`vehicle_id`에는 `-`를 쓸 수 있지만 `trip_id`는 불투명 문자열로만 취급한다(파싱 금지).

### 1.4 차량 배정·차고지

```text
assign_vehicles(plan, fleet) -> {route_id: [Vehicle]}
    남은 차량 = fleet.vehicles를 vehicle_id 오름차순
    for route in routes를 route_id 오름차순:
        vehicle_ids가 있으면 그 차량들(없는 ID·중복이면 PLAN_INFEASIBLE)
        없으면 남은 차량 앞에서 vehicle_count대
    합계가 가용 대수를 넘으면 PLAN_INFEASIBLE
    for v in 배정 차량: initial_stop_id가 null도 anchor도 아니면 PLAN_INFEASIBLE(details.reason="DEADHEAD_DATA_REQUIRED")
                       end_stop_id도 같은 검사
```

- v1은 **차고지 공차를 계산하지 않는다.** 차량은 anchor에 미리 가 있다고 본다(§11 D03). 경로 탐색으로 공차 시간을 지어내지 않는 것은 M00 §2.2("미확인 구간을 임의 보완하지 않는다")와 C의 `route_error`("차고지 공차 이동 자료가 필요한 후보")에 맞춘 것이다.
- 확장 지점: 출고·입고를 `cycle_index=-1`/마지막의 공차 trip으로 duty 앞뒤에 넣으면 엔진 변경 없이 실행된다. 정류장 순서열은 운행안이 명시해야 한다.
- 차급이 섞인 fleet에서 명시 배정이 없으면 ID 순서 배정이라 정원이 노선에 맞지 않을 수 있다. 그 경우 운행안이 `vehicle_ids`를 준다.
- (2단계 구현에서 정함) 명시 배정 차량은 **먼저 예약**하고 남은 차량으로 나머지 노선을 채운다. 그래야 route_id가 앞선 무배정 노선이 뒤 노선의 명시 차량을 가져가지 않는다. `vehicle_ids` 길이는 `vehicle_count`와 같아야 한다(아니면 `PLAN_INFEASIBLE`).

### 1.5 duty와 `timetable.json`

| 필드 | 형식 | 의미 |
| --- | --- | --- |
| `schema_version` | int | 1 |
| `plan_id`, `plan_revision` | | 대상 운행안 |
| `origin` | `"generated"` \| `"provided"` | 생성기가 만들었는지, 운행안이 준 것인지(기록용) |
| `routes[]` | `{route_id, period_s, vehicle_ids[]}` | `provided`면 `period_s`는 null |
| `trips[]` | 아래 | `(vehicle_id, cycle_index, leg_index)` 오름차순 |
| `duties[]` | `{vehicle_id, route_id, trip_ids[], start_s, end_s}` | `vehicle_id` 오름차순. `start_s`·`end_s`는 첫·마지막 예정 시각 |

`trips[]`

| 필드 | 형식 | 의미 |
| --- | --- | --- |
| `trip_id` | string | 유일 |
| `route_id`, `vehicle_id` | string | |
| `cycle_index`, `leg_index` | int | |
| `in_service` | bool | 공차면 false |
| `stop_times[]` | `{stop_seq:int, stop_id:string, sched_s:int}` | `stop_seq`는 0부터. `sched_s`는 스냅숏 예정 시각 |

`validate_timetable`(생성·제공 공통, 실패 시 `PLAN_INFEASIBLE`): 차량이 fleet에 있음 · 차량당 노선 1개 · 같은 차량의 trip이 예정 시각상 겹치지 않음 · 연속 trip은 앞의 마지막 정류장 = 뒤의 첫 정류장 · 모든 예정 시각이 차량의 유효 창 안 · 연속 정류장쌍이 `travel_times.json`에 있음(없으면 `DATA_INCOMPLETE`).
2단계 구현에서 더한 검사: trip의 정류장열·`in_service`가 `plan.json`의 해당 leg와 같음 · `stop_seq`가 0부터 연속 · trip 안 `sched_s` 비감소 · duty의 `trip_ids`·`start_s`·`end_s`가 trip과 일치 · 노선별 사용 차량 수 ≤ `vehicle_count`(명시 배정이면 그 안) · 차량의 `initial_stop_id`/`end_stop_id`가 있으면 duty의 첫/마지막 정류장과 같음.

- `duties[]`에는 trip이 1개 이상인 차량만 넣는다. 배정됐지만 cycle이 하나도 들어가지 않은 차량은 `routes[].vehicle_ids`에만 남는다.
- `routes[].period_s`는 배정 대수가 0이면 `null`이다(주기를 계산하지 않는다). `provided`면 `routes[].vehicle_ids`는 trip에 나온 차량이다.
- 운행안이 trip을 직접 줄 때(`plan.trips`, `plan.vehicle_duties`)는 `(vehicle_id, cycle_index, leg_index)`·`vehicle_id` 순으로 다시 정렬해 쓴다.

### 1.6 공정성: 시간표가 없는 안

```text
normalize_plan(plan, scenario, tt) -> Timetable
    if plan.trips 가 비어 있음: tt = build_timetable(plan, …); origin = "generated"
    else:                        tt = plan의 trips·duties;      origin = "provided"
    validate_timetable(tt, …)
    return tt
```

- 지자체안·범용 AI안·최적화안 모두 이 함수 하나를 지난다. `source`로 갈라지는 코드는 없고, 이를 테스트로 고정한다(§9 T7).
- 3안 비교 실험에서는 **세 안 모두 노선+대수만 주고 생성기를 쓰는 것**을 권장한다. 한 안만 손으로 다듬은 시간표를 쓰면 시간표 품질 차이가 섞인다. C의 `to_service_plan`도 `trips=[]`, `schedule_status="pending_b"`로 내보내므로 이미 이 형태다.

### 1.7 버린 대안

- 차량이 준비되는 대로 계속 도는 방식(시간표 없음): 배차간격이 시간대마다 흔들리고 GTFS로 내보낼 예정 시각이 없다.
- 평균 순환시간으로 P 결정: 혼잡 시간대에 지각이 누적된다.
- `h = ceil(P/n)`로 간격을 먼저 반올림: C의 위상과 1초씩 어긋나 비교가 흐려진다.
- 마지막 편도 trip만 추가 편성: 종료 조건(anchor 복귀)과 충돌하고 유형별 예외가 생긴다.
- "마지막 출발이 W_e 이전"으로 운영시간 해석: C와 종료 규칙이 달라지고 검증 불변식이 복잡해진다. 필요하면 Scenario에서 `W_e`를 늘린다(§11).
- 시뮬레이터가 최단경로로 차고지 공차 계산: 미확인 연결을 지어내게 된다.

---

## 2. 승객 발생 실현기

실현기는 시뮬레이터 실행 **전에** 한 번 돌아 `arrivals.jsonl`을 만든다. 운행안을 입력으로 받지 않으므로 모든 안이 같은 승객을 받는다. 엔진은 이 파일만 읽고 난수를 쓰지 않는다.

### 2.1 결정: 체계적 반올림(누적 유량 교차)

OD 하나의 시간대별 유량을 누적해, 누적값이 `u, u+1, u+2, …`를 넘는 순간마다 승객 1명을 만든다. `u ∈ [0,1)`은 OD별로 seed에서 해시로 정한다.

이유
- OD별 인원이 원래 유량 합과 ±1 안에서 맞고, `u`가 균등이면 기댓값이 유량과 정확히 같다. 0.3명 같은 작은 유량도 버려지지 않는다.
- 도착이 고르게 퍼져 seed에 따른 결과 흔들림이 작다. 운행안 사이 차이와 S(n) 곡선이 발생 잡음에 묻히지 않는다.
- 정수 연산만 쓴다(유량을 백만분의 1명 단위 정수로 바꿈). 플랫폼·Python 버전에 관계없이 같은 파일이 나온다.
- 난수 상태가 없어 OD 처리 순서를 바꿔도 결과가 같다(M00 §3.2의 "seed만으로 같은 입력이라 주장하지 않는다"에 대한 구조적 대응. 그래도 비교의 기준은 seed가 아니라 파일 체크섬이다).

### 2.2 알고리즘

```text
MICRO = 1_000_000

u_micro(seed, kind, origin_stop_ids, dest_stop_ids) -> int
    key = json.dumps([str(seed), kind, sorted(origin_stop_ids), sorted(dest_stop_ids)],
                     ensure_ascii=False, separators=(",", ":"))
    return int.from_bytes(sha256(key.encode("utf-8")).digest()[:8], "big") % MICRO

realize_od(flows_micro, bin_starts, bin_length_s, u) -> [arrival_s]
    # flows_micro[i]: i번째 시간대 유량 × MICRO (정수), bin_starts 오름차순
    out = []; cum = 0; k = 0
    for f, b in zip(flows_micro, bin_starts):
        while f > 0 and u + k*MICRO < cum + f:
            out.append(b + (u + k*MICRO - cum) * bin_length_s // f)
            k += 1
        cum += f
    return out
```

- 유량 → 정수: 입력 문자열·수치를 `decimal.Decimal`로 읽어 `MICRO`를 곱하고 `ROUND_HALF_EVEN`으로 정수화한다. float 덧셈을 거치지 않는다.
- 같은 `(kind, origin 집합, dest 집합)` 행이 여러 개면 시간대별로 먼저 합친다.
- 고정 벡터(구현 고정용): `u_micro(42, "access", ["A"], ["S1","S2"]) = 699417`, `u_micro(42, "egress", ["S1","S2"], ["A"]) = 480388`, `u_micro(7, "access", ["B"], ["S"]) = 658692`.

### 2.3 승객 필드와 egress 처리

| kind | `origin_stop_ids` | `dest_stop_ids` |
| --- | --- | --- |
| access (정류장→역) | `[정류장]` | 그 역에 딸린 버스 정류장 전부 |
| egress (역→정류장) | 그 역에 딸린 버스 정류장 전부 | `[정류장]` |

- "역에 딸린 정류장"은 NetworkSnapshot의 역–정류장 연결에서 가져온다(실현기 입력 `station_stops: {station_id: [stop_id…]}`). 운행안과 무관하게 정해지므로 공정하다.
- 요구사항의 "출발 정류장"을 집합으로 넓힌 이유: egress 승객을 정류장 하나에 미리 묶으면, 셔틀이 역의 어느 쪽 정류장에 서느냐에 따라 운행안마다 유불리가 갈린다.
- 역 정류장 사이 걷는 시간은 0으로 본다(§11 D06).
- egress 승객은 영업 leg가 역에서 출발해야 탈 수 있다. 피더형(편도+회송)은 access만 실어 나르고 egress는 `NO_ROUTE_*`로 남는다. egress를 실으려면 회송 leg를 `in_service=true`로 주거나(두 역 연결형과 같은 구조) 순환형을 쓴다.
- `max_wait_s`는 `policy.max_wait_s`를 그대로 넣는다. 대기 한도는 대기열 동작을 바꾸므로 실행 전에 고정해야 하고, 그래서 입력 파일에 들어 있다(§11 D07).
- **M03에 확인할 것**: egress 유량의 시간대가 "역 정류장에 나타나는 시각"인지 "여정 첫 승차 시각"인지. 실현기는 전자로 가정한다. 후자라면 M03이 지하철 이동시간만큼 옮겨서 줘야 한다.

### 2.4 ID·정렬·체크섬

```text
realize_arrivals(rows, station_stops, policy, window, seed, bin_length_s) -> [Passenger]
    od_list = (kind, origin 집합, dest 집합) 키를 JSON 문자열 기준 오름차순으로 정렬
    for od_seq, od in enumerate(od_list, start=1):
        times = realize_od(od.flows_micro, od.bin_starts, bin_length_s, u_micro(seed, …))
        for k, t in enumerate(times, start=1):
            if not (W_s <= t <= W_e): INPUT_INVALID
            emit Passenger(passenger_id=f"p{od_seq:06d}-{k:05d}", …, arrival_s=t, max_wait_s=policy.max_wait_s)
    return (arrival_s, passenger_id) 오름차순
```

- `passenger_id`는 폭이 고정이라 문자열 정렬 = 생성 순서다. 원시 카드 식별자와 무관하다.
- (2단계 구현에서 정함) 실현기 입력 행은 `DemandFlow(kind, stop_id, station_id, bin_start_s, flow)`이다. access는 `stop_id → station_stops[station_id]`, egress는 그 반대로 바꾼다. 같은 OD의 시간대 시작은 `bin_length_s` 이상 떨어져야 한다(겹치면 `INPUT_INVALID`). `flow`는 문자열·정수·`Decimal`을 권장하고, float는 `str()`을 거쳐 `Decimal`로 읽는다.
- 파일 해시와 `run_inputs.json`의 `checksum`은 §0.3. 비교 실험은 `input_bundle_id`와 `checksum`이 같을 때만 "같은 입력"이다.

### 2.5 버린 대안

- 포아송(시간대별 인원을 Poisson, 시각은 균등): 현실의 무작위성은 담지만 seed 하나로는 안끼리 차이가 잡음에 묻힌다. 여러 seed 강건성 실험용 확장으로 남긴다(`arrival_method="poisson"`, v1은 `OUT_OF_SCOPE`).
- 시간대별 반올림 후 균등 배치: 0.5 미만 유량이 전부 사라져 총수요가 줄어든다.
- 시간대 시작에 한꺼번에 발생: 대기열이 실제보다 뭉쳐 만차가 과장된다.
- `random.Random(seed)` 순차 호출: OD 순서가 바뀌면 결과가 바뀐다.

---

## 3. 이벤트 모델

### 3.1 이벤트 종류

| type | 주체 | 언제 | 내용 |
| --- | --- | --- | --- |
| `arrive` | 차량 | 정류장 스냅숏 시각 | 하차·승차·남겨짐을 그 순간에 확정 |
| `alight` | 승객 | `arrive`와 같은 t | 하차 |
| `board` | 승객 | `arrive`와 같은 t | 승차 |
| `denied` | 승객 | `arrive`와 같은 t | 만차로 남겨짐. **승객당 첫 번째만** 기록 |
| `depart` | 차량 | `arrive.t + 정차` | 다음 정류장으로 출발. trip의 마지막 정류장에는 없다 |
| `cancel` | 차량 | cycle 시작 판단 시각 | 지연 때문에 cycle을 취소(§3.4) |
| `no_service` | 승객 | `arrival_s` | 탈 수 있는 노선이 없음(§4.5) |
| `abandon` | 승객 | `arrival_s + max_wait_s` | 대기 한도 초과 |
| `left_waiting` | 승객 | `W_e` | 한도 전인데 시뮬레이션이 끝남 |

승객 도착 자체는 로그에 쓰지 않는다(입력 파일에 있다).

### 3.2 처리 순서

차량 이벤트만 우선순위 큐(`heapq`)에 넣는다. 차량마다 대기 중인 이벤트는 항상 1개다.

```text
큐 키 = (t, vehicle_id, visit, kind)        # kind: ARRIVE=0, DEPART=1

simulate(timetable, passengers, scenario, tt) -> [Event]
    pax = passengers를 (arrival_s, passenger_id)로 정렬;  i = 0
    for 차량 v in duties: push (첫 trip의 sched_s[0], v, 0, ARRIVE)
    while 큐가 비지 않음:
        (t, v, visit, kind) = pop
        while i < len(pax) and pax[i].arrival_s <= t:  admit(pax[i]); i += 1     # ① 승객 먼저
        if kind == ARRIVE: on_arrive(t, v, visit)                                  # ②
        else:              on_depart(t, v, visit)
    남은 승객 모두 admit
    finalize_waiting()                                                             # ③ abandon / left_waiting
    return canonical_sort(events)                                                  # §6.1 정렬 키

admit(p):
    cause = static_cause(p)                    # §4.5
    if cause: emit no_service(t=p.arrival_s, p, cause)
    else:     p.state = WAITING;  for s in p.origin_stop_ids: queue[s].append(p)
```

같은 시각의 순서(결과에 영향을 주는 것만):

1. 그 시각까지 도착한 승객이 먼저 대기열에 들어간다 → **차량과 같은 초에 도착한 승객은 탄다**(대기 0초 허용, C의 `0 <= wait`와 같음).
2. 차량끼리는 `vehicle_id` 오름차순. 같은 정류장에 같은 초에 선 두 차량 중 ID가 앞선 차가 먼저 태운다.
3. 한 차량 안에서는 `visit` 순(앞 trip의 종점 도착 → 다음 trip의 첫 정류장).
4. 대기 한도는 포함이다: `t ≤ arrival_s + max_wait_s`인 차량에는 탄다. `abandon`은 같은 초의 차량 처리 뒤에 온다.

엔진은 `set` 순회 순서나 dict 삽입 순서에 결과를 맡기지 않는다. 집합이 필요한 곳은 정렬해서 돈다.

### 3.3 정류장 처리: 하차 → 승차 → 정차 → 출발

```text
on_arrive(t, v, visit):
    trip, k = v의 현재 trip, 정류장 순번;  last = 마지막 순번
    alighters = [];  boarders = [];  left = 0
    if trip.in_service:
        alighters = v.onboard.pop(k, [])                    # ① 하차 (passenger_id 순)
        v.load -= len(alighters)
        if k < last:                                        # ② 승차 (§4.2)
            boarders, left = board_fifo(t, v, trip, k)
            v.load += len(boarders)
    emit arrive(t, v, trip, visit, stop, k, alighted, boarded, left_behind=left, load=v.load)
    emit alight…, board…, denied…
    if not trip.in_service:        dwell = 0
    elif k < last:                 dwell = dwell_base_s + dwell_per_boarding_s*len(boarders) + dwell_per_alighting_s*len(alighters)   # ③
    else:                          dwell = dwell_per_alighting_s*len(alighters)       # 종점: 기본 정차 없음
    if k < last: push (t + dwell, v, visit, DEPART)                                    # ④
    else:        start_next_trip(t + dwell, v, visit)

on_depart(t, v, visit):
    emit depart(t, v, trip, visit, stop, k, load=v.load)
    push (t + tt.lookup(stop_k, stop_k+1, t), v, visit + 1, ARRIVE)
```

- **도착 스냅숏 승차**: 정차 중에 도착한 승객은 그 차에 타지 못하고 다음 차를 기다린다. C의 "도착한 시점에 탑승 처리하고 정차 후 출발"과 같다. 이벤트가 정류장당 2개로 끝나 빠르고 검증하기 쉽다.
- 정차 모델은 "기본 + 1인당"의 단순 합이다(문 하나로 내리고 타는 전세버스 가정).
- trip의 첫 정류장 `arrive`는 물리적 도착이 아니라 "출발 준비가 되어 승차를 받는 순간"이다.

### 3.4 trip 전환과 지연

```text
start_next_trip(free, v, visit):
    nxt = duty의 다음 trip;  없으면 종료
    ready = free + (turnaround_s if 방금 끝난 trip.in_service else 0)
    if nxt.leg_index == 0:                                   # 새 cycle
        ready += cycle_rest_s
        s = max(nxt.sched_s[0], ready)
        dur   = cycle(nxt)의 마지막 trip 마지막 sched_s − nxt.sched_s[0]      # 시간표에서 읽음
        allow = (dwell_per_boarding_s + dwell_per_alighting_s) * capacity(v) * cycle(nxt)의 영업 trip 수
        if s + dur + allow > min(W_e, v.available_end_s):
            emit cancel(t=ready, v, trip_id=nxt.trip_id, visit=visit+1, cause="LATE");  종료(이후 cycle도 실행 안 함)
    else:
        s = max(nxt.sched_s[0], ready)
    push (s, v, visit + 1, ARRIVE)                           # 예정보다 일찍 출발하지 않는다
```

- 차량은 trip 첫 정류장에서만 예정 시각을 기다린다. 중간 정류장에서는 정차가 끝나면 바로 떠난다.
- 1인당 정차 때문에 실제 운행은 예정보다 늦어질 수 있다. 지연은 anchor의 여유(P − 계획 순환시간)에서 회복되고, 회복이 안 되면 다음 출발이 밀린다.
- 밀린 끝에 종료 시각을 넘길 cycle은 시작 전에 취소한다. 그래도 넘는 드문 경우(한 leg에서 정원보다 많은 사람이 타고 내림)는 검증기 V10이 위반으로 보고한다.
- 지표에 trip별 `start_delay_s`를 남겨 시간표가 얼마나 지켜졌는지 보여 준다.

### 3.5 시간대별 구간 시간 조회

```text
tt.lookup(from_stop, to_stop, t) -> travel_s
    seg = segments[(from_stop, to_stop)]             # 없으면 DATA_INCOMPLETE
    i   = bisect_right(seg.bin_starts, t) - 1        # bin_start_s <= t 인 마지막 시간대
    if i < 0: DATA_INCOMPLETE
    return seg.travel_s[i]
```

- 조회 기준은 **상류 정류장 출발 시각**이다. 구간을 달리는 도중 시간대가 바뀌어도 출발 때 값 그대로다.
- 경계는 포함: `t == bin_start_s`면 새 시간대.
- 마지막 시간대는 `valid_until_s` 이후에도 유지한다(로딩 때 `valid_until_s ≥ W_e`를 이미 확인).
- v1에서 생성기와 엔진은 같은 표를 본다(계획 = 실현).

### 3.6 버린 대안

- 정차 중 도착 승객도 승차: 승객 도착마다 출발 시각을 다시 계산해야 하고 동시각 규칙이 늘어난다. 배차간격 대비 정차가 짧아 효과가 작다.
- 승하차가 없으면 무정차 통과: 실제 운행이 시간표보다 빨라져 C와의 비교 조건이 하나 더 생긴다.
- 구간 시간 선형 보간: 정수가 깨지고 검증식이 복잡해진다.
- SimPy 등 프레임워크: 외부 의존성이고, 처리 순서를 직접 못 박기 어렵다(기존 Draft PR `feat/5-simulation-core`는 SimPy 기반이며 이 설계의 구현은 그 코드를 쓰지 않는다).
- 승객 도착·포기도 큐 이벤트로: 이벤트 수가 승객 수만큼 늘어난다. 정렬된 배열 포인터와 지연 판정으로 같은 결과를 낸다.

---

## 4. 탑승 규칙

### 4.1 탈 수 있는 차

정류장 순번 k에 선 영업 trip이 승객 p에게 **유효**하다 ⇔ `stop_k ∈ p.origin_stop_ids`이고, 같은 trip의 `j > k` 중 `stop_j ∈ p.dest_stop_ids`인 j가 있다. 내리는 곳은 그런 j 중 **가장 작은 것**이다.

```text
alight_index(trip, k, p) -> j | None            # (노선, leg, k, dest 집합)으로 캐시
    for j in k+1 … last: if trip.stop_id[j] in p.dest_stop_ids: return j
    return None
```

- 승객은 **유효한 첫 차**를 탄다. 더 빠른 다음 차를 기다리지 않는다(실시간 도착 정보를 모른다고 본다).
- 순환형처럼 한 leg에 같은 정류장이 두 번 나오면 각각 다른 순번이다. trip이 끝나면 모두 내려야 하므로, 그 trip 안에서 목적지에 닿지 않으면 타지 않는다.

### 4.2 FIFO와 만차

```text
board_fifo(t, v, trip, k) -> (boarders, left)
    cap = v.seat_capacity + v.standing_capacity
    keep = []
    for p in queue[stop_k]:                      # (arrival_s, passenger_id) 순으로 유지되는 리스트
        if p.state != WAITING: continue          # 다른 정류장에서 이미 탐 → 버림
        if p.arrival_s + p.max_wait_s < t: continue    # 한도 지남 → 버림(abandon은 finalize에서)
        j = alight_index(trip, k, p)
        if j is None: keep.append(p); continue   # 이 차는 목적지에 안 감 → 줄은 유지, 뒤 사람을 막지 않음
        if v.load + len(boarders) < cap:
            boarders.append(p); p.state = ONBOARD; p.board = (t, v, trip, k); v.onboard[j].append(p)
        else:
            left += 1; p.denied_count += 1
            if p.denied_count == 1: emit denied(t, v, trip, visit, stop_k, k, p)
            keep.append(p)
    queue[stop_k] = keep
    return boarders, left
```

- 여러 노선이 서는 정류장: 대기열은 정류장당 하나다. 차량은 줄 앞에서부터 **자기에게 유효한 승객만** 순서대로 태우고, 유효하지 않은 승객은 건너뛴다.
- 목적지 집합에 여러 역이 있는 승객은 먼저 온 유효한 차를 타고 그 차가 먼저 닿는 허용 정류장에서 내린다.
- egress 승객은 `origin_stop_ids`의 모든 정류장 줄에 동시에 선다. 한 곳에서 타면 나머지 줄에서는 상태 검사로 빠진다.
- 만차 기록: `arrive.left_behind`(그 정차에서 남겨진 유효 승객 수, 매번) + 승객별 첫 `denied` 이벤트. 승객별 남겨진 횟수는 지표에서 집계한다.
- 줄 길이만큼 도는 단순 구현이다. 속도 목표(§7.4)를 못 맞추면 `(정류장, dest 집합)`별 하위 줄로 나누되 결과는 같아야 한다.

### 4.3 좌석·입석

- 탑승 한도 = `seat_capacity + standing_capacity`. 입석을 허용하지 않는 차급은 `standing_capacity=0`으로 준다.
- 지표는 좌석 기준과 총정원 기준 재차율을 둘 다 낸다(§6.3). C의 `capacity`는 `seat_capacity`로 옮기고 입석 0으로 둔다.

### 4.4 환승(v1)

- 없다. 승객은 셔틀을 한 번만 타고, 허용 목적지에 내리면 끝이다(`single_bus_leg`, D02·D06).
- 확장 지점: `Passenger`에 leg 목록을 두고 하차 시 다음 leg의 대기열에 `arrival_s = 하차 시각 + 도보`로 다시 넣는다. `admit`·`board_fifo`는 그대로 쓴다.

### 4.5 못 탄 원인

```text
static_cause(p) -> cause | None                 # 시간표의 영업 trip 패턴(정류장 순서열 집합)만으로 판정
    if 어떤 패턴도 p.origin 정류장을 마지막이 아닌 순번에 갖지 않음: return "NO_ROUTE_AT_STOP"
    if 어떤 패턴에도 origin 뒤에 dest가 오지 않음:               return "NO_ROUTE_TO_DEST"
    return None

finalize_waiting():
    for p in state == WAITING (passenger_id 순):
        deadline = p.arrival_s + p.max_wait_s
        if deadline <= W_e: emit abandon(t=deadline, p, cause = "FULL" if p.denied_count else "WAIT_EXCEEDED")
        else:               emit left_waiting(t=W_e, p, cause = "FULL" if p.denied_count else "SERVICE_ENDED")
```

| 결과(outcome) | cause | 뜻 | 요구사항의 원인 |
| --- | --- | --- | --- |
| `no_service` | `NO_ROUTE_AT_STOP` | 영업 노선이 이 정류장에서 태우지 않는다 | 노선이 정류장에 안 옴 |
| `no_service` | `NO_ROUTE_TO_DEST` | 정류장에는 서지만 목적지로 가지 않는다 | 목적 역으로 안 감 |
| `abandon` / `left_waiting` | `FULL` | 유효한 차가 왔지만 만차로 한 번 이상 남겨졌다 | 만차 |
| `abandon` | `WAIT_EXCEEDED` | 한도 안에 유효한 차가 오지 않았다 | 대기 한도 초과 |
| `left_waiting` | `SERVICE_ENDED` | 한도 전인데 운영이 끝났다 | (종료 시 대기) |
| `served`인데 미충족 | `JOURNEY_EXCEEDED` | 수송했지만 `대기+차내 > max_journey_s` (지표에서만 판정) | — |

- 인원 보존: **발생 = 수송(served) + 포기(abandon + no_service) + 종료 시 대기(left_waiting)**.
- `no_service` 승객은 대기열에 들어가지 않고 대기시간 통계에서 빠진다.
- 대수 0인 노선은 trip이 없으므로 패턴에 들어가지 않는다.
- "이동 포기·수단 전환은 반영하지 않음"과의 관계: `abandon`은 행동 모형이 아니라 **평가 절단**이다. 한도를 넘긴 승객을 계속 줄에 두면 뒤 승객의 탑승 순서를 왜곡하므로 줄에서 뺀다.

### 4.6 버린 대안

- 목적지 도착이 가장 빠른 차 선택(C의 근사 방식): 승객이 모든 차의 예정 시각을 안다고 가정해야 하고, 일부러 차를 보내는 동작이 생긴다.
- 유효하지 않은 승객이 줄 앞을 막는 엄격 FIFO: 다른 노선 승객 때문에 못 타는 비현실적 결과가 나온다.
- 남겨질 때마다 `denied` 기록: 과부하 시 로그가 승객 수 × 통과 차량 수로 불어난다.
- `max_journey_s`를 넘길 차는 타지 않기: 승객이 도착 시각을 미리 알아야 한다. 대신 지표에서 구분한다.

---

## 5. 비파업 노선

### 결정: 3단계(M03 잔여 수요) 전처리로 끝낸다. v1 시뮬레이터 안에서는 차량으로 돌리지 않는다.

근거
1. 3단계 산출물의 정의가 이미 "파업 노선이 빠진 뒤 비파업 노선(마을버스 등)이 흡수하고 남은 수요"다. 시뮬레이터에서 비파업 차량까지 돌리면 같은 흡수를 두 번 센다.
2. 비파업 노선의 흡수량은 셔틀 운행안과 무관하게 정해진다고 보면, 세 안에 똑같이 적용되는 입력 조건이다. 입력에서 한 번 고정하는 쪽이 공정성과 재현성에 맞다.
3. 비파업 차량을 돌리려면 그 차량의 기존 승객(배경 부하)과 남은 정원이 필요한데 지금 자료에는 배차간격·운행횟수만 있다.
4. 이벤트 수가 몇 배로 늘어 S(n) 반복 실행(§7)이 느려진다.

잃는 것: 셔틀이 만차일 때 마을버스로 옮겨 타는 식의 상호작용. 3단계의 흡수율이 고정이라 셔틀이 좋아져도 잔여 수요가 줄지 않는다. 가정 목록 A12에 방향을 적었다.

확장 지점: 엔진은 `timetable.json`의 trip만 실행하므로, 비파업 노선을 **배경 trip**(`service_kind="background"`, 남은 정원 = 차량 정원 − 배경 부하)으로 시간표에 추가하면 엔진 수정 없이 같이 돈다. 바꿀 곳은 (a) 시간표 병합 함수, (b) 지표에서 `service_kind`별 분리, (c) 이때 입력은 3단계 "잔여"가 아니라 2단계 수요 벡터여야 한다는 점뿐이다. v1 스키마에는 `service_kind`를 넣지 않는다.

버린 대안
- 시뮬레이터 안에서 비파업 차량 실행: 위 1·3·4.
- 비파업 노선을 무한 정원의 "대기 0" 대안으로 모형화: 흡수량을 과대평가한다.

---

## 6. 출력

### 6.1 이벤트 로그 `events.jsonl`

한 줄에 이벤트 하나. 키는 아래 표의 순서로 쓰고, **값이 없는 키는 생략**한다. 모든 값은 정수 또는 문자열이다.

| 키(이 순서) | 형식 | 있는 이벤트 | 의미 |
| --- | --- | --- | --- |
| `seq` | int | 전부 | 0부터 1씩 증가 = 줄 번호 |
| `t` | int | 전부 | 시각(초) |
| `type` | string | 전부 | §3.1 |
| `vehicle_id` | string | 차량·승하차·`denied`·`cancel` | |
| `trip_id` | string | 위와 같음 | `cancel`은 취소된 cycle의 첫 trip |
| `visit` | int | 위와 같음 | 차량별 정차 일련번호. `cancel`은 "다음에 받았을 번호" |
| `stop_id` | string | `arrive` `depart` `alight` `board` `denied` | |
| `stop_seq` | int | 위와 같음 | trip 안 정류장 순번 |
| `passenger_id` | string | 승객 이벤트 전부 | |
| `alighted` | int | `arrive` | 이 정차의 하차 수 |
| `boarded` | int | `arrive` | 이 정차의 승차 수 |
| `left_behind` | int | `arrive` | 만차로 남겨진 유효 승객 수 |
| `load` | int | `arrive`(승하차 반영 후), `depart` | 차내 인원 |
| `cause` | string | `cancel` `no_service` `abandon` `left_waiting` | §4.5, `cancel`은 `"LATE"` |

정렬 키(로그 순서의 **정의**. 엔진의 처리 순서와 같지만, 구현은 마지막에 이 키로 정렬해 `seq`를 매긴다):

```text
key(e) = (t, phase, vehicle_id 또는 "", visit 또는 0, type_rank, order)
  phase:      no_service=0 | 차량 묶음(arrive, alight, board, denied, depart, cancel)=1 | abandon=2 | left_waiting=3
  type_rank:  arrive=0, alight=1, board=2, denied=3, depart=4, cancel=0
  order:      alight → passenger_id
              board, denied → (승객 arrival_s, passenger_id)   # 줄 선 순서
              no_service, abandon, left_waiting → passenger_id
              그 외 → 빈 값
```

- 공차 trip도 정류장마다 `arrive`/`depart`를 남긴다(구간 시간 검증용). 값은 `alighted=boarded=left_behind=load=0`.
- 같은 `RunInputs`와 같은 `ServicePlan`이면 이 파일이 바이트 단위로 같다. 파일 해시를 `run.json`에 기록한다.
- 버린 대안: CSV(이벤트마다 쓰는 열이 달라 빈 칸이 대부분), 모든 키를 null로 채운 JSONL(크기 약 2배), Parquet(외부 의존성).

### 6.2 `run.json` (M00 `SimulationResult` + 추적 정보)

| 필드 | 의미 |
| --- | --- |
| `run_id` | `"run-" + sha256(plan_sha256 + "|" + input_checksum + "|" + config_hash)[:20]` — 같은 실행은 같은 ID |
| `plan_id`, `plan_revision`, `input_bundle_id` | M00 |
| `metrics_uri`, `event_log_uri` | M00. 저장 위치는 호출자가 준 출력 디렉터리 기준 상대 경로 |
| `config_hash` | 아래 |
| `input_checksum`, `plan_sha256`, `timetable_sha256`, `event_log_sha256`, `metrics_sha256` | 추적용 해시 |
| `timetable_origin` | `generated` / `provided` |
| `sim_version` | 엔진 버전 문자열(2단계: `busstrike-sim/0.1.0`) |
| `timetable_uri` | `timetable.json`(검증기 입력, 2단계에서 추가) |

**`config_hash`의 범위** = 엔진이 읽었지만 `RunInputs`·`ServicePlan`으로 식별되지 않는 것 전부:

```text
config_hash = sha256(canonical_json({
    "sim_version", "timetable_rule_version", "log_schema_version", "metrics_schema_version",
    "policy": {dwell_base_s, dwell_per_boarding_s, dwell_per_alighting_s, turnaround_s, cycle_rest_s,
               max_journey_s, allowed_transfers},
    "operating_window": {start_s, end_s},
    "fleet": [vehicle_id 순으로 Vehicle 전체 필드],
}))
```

- 넣지 않는 것: 운행안 내용(`plan_sha256`), 승객·구간 시간(`input_checksum`), `max_wait_s`(승객 파일 안에 있음), 출력 경로, 실행 시각, 기계 정보.
- 결과 하나는 `(plan_id, revision, plan_sha256) + (input_bundle_id, input_checksum) + config_hash`로 식별한다.

### 6.3 지표 `metrics.json`

지표는 엔진 안에서 세지 않고 **로그 + 입력에서 계산**한다(`metrics.py`). 검증기가 같은 값을 독립적으로 다시 계산할 수 있다(V13).

정의
- 대기 = `board.t − arrival_s`. 차내 = `alight.t − board.t`. 총 이동 = `alight.t − arrival_s`.
- 수송(transported) = `alight`가 있는 승객. 충족(satisfied) = 수송 중 `총 이동 ≤ policy.max_journey_s`.
- 백분위는 nearest-rank: 오름차순 N개 중 `ceil(p·N/100)`번째. 평균은 `round(x, 3)`, 비율은 `round(x, 6)`. 표본이 0이면 `null`(0으로 채우지 않는다).

| 경로 | 내용 |
| --- | --- |
| `scope_mode` | `"single_bus_leg"` — 다른 scope와 같은 그래프에 섞지 않는다 |
| `passengers.generated` / `.transported` / `.satisfied` | 인원 |
| `passengers.abandoned` / `.no_service` / `.left_waiting` | 미수송 분류. 합이 `generated − transported` |
| `passengers.transport_rate`, `.satisfaction_rate` | `/generated`. **충족률 = `satisfaction_rate`** |
| `passengers.by_kind.{access,egress}` | 위 항목을 종류별로 |
| `wait_s` | 탑승 승객: `count, mean, p50, p90, p95, max` |
| `in_vehicle_s`, `journey_s` | 수송 승객: 같은 통계 |
| `denied.boardings` | `left_behind` 합(남겨짐 연인원) |
| `denied.passengers` | 한 번 이상 남겨진 승객 수 |
| `unserved.by_cause` | cause별 인원(§4.5, `JOURNEY_EXCEEDED` 포함) |
| `unserved.by_stop[]` | `{stop_id, cause, count}` — 승객의 `origin_stop_ids[0]` 기준, `(stop_id, cause)` 순 |
| `vehicles[]` | `{vehicle_id, route_id, revenue_m, deadhead_m, first_s, last_s, span_s, revenue_s, deadhead_s, boardings, cycles_run, cycles_cancelled}` |
| `fleet` | `vehicles_used, total_m, revenue_m, deadhead_m, total_span_s, boardings_per_vehicle`(=승차 합/사용 대수) |
| `trips[]` | 영업 trip별 `{trip_id, route_id, vehicle_id, boardings, max_load, load_factor_seat, load_factor_total, start_delay_s, end_s}` |
| `routes[]` | 노선별 `{route_id, vehicles, trips_run, boardings, satisfied, mean_wait_s, max_load_factor_total}` |

- `revenue_s`·`deadhead_s`는 각 trip의 첫 `arrive`~마지막 `arrive` 시간의 합. `span_s = last_s − first_s`(대기 포함).
- `load_factor_seat = max_load / seat_capacity`(1을 넘으면 입석), `load_factor_total = max_load / (seat+standing)`.
- `start_delay_s = trip 첫 arrive.t − sched_s[0]`, `end_s = trip 마지막 arrive.t`. `cycles_cancelled`는 시간표에 있지만 실행되지 않은 cycle 수.
- C의 `Assessment`로 옮길 때(5단계): `served = satisfied`, `total = generated`, `p90_wait_s = wait_s.p90`, `distance_m = fleet.total_m`.

2단계 구현에서 정한 세부(검증기 V13이 같은 규칙으로 재계산해야 한다)
- 최상위에 `schema_version`(1), `plan_id`, `plan_revision`을 둔다.
- `unserved.by_cause`는 여섯 cause(`FULL`, `JOURNEY_EXCEEDED`, `NO_ROUTE_AT_STOP`, `NO_ROUTE_TO_DEST`, `SERVICE_ENDED`, `WAIT_EXCEEDED`)를 0까지 모두 싣는다. `by_stop`은 0이 아닌 칸만.
- `vehicles[]`는 `timetable.routes[].vehicle_ids`에 배정된 차량 전부(`vehicle_id` 순). `first_s`·`last_s`는 그 차량의 `arrive`·`depart` 시각만 본다(`cancel` 제외). 한 번도 움직이지 않으면 `first_s`·`last_s`·`span_s`는 `null`. 거리는 실행된 정차마다 직전 정류장→그 정류장 구간의 `distance_m` 합. `cycles_run`은 trip이 하나라도 실행된 cycle 수.
- `fleet.vehicles_used`는 이벤트가 있는 차량 수, `total_span_s`는 그 차량들의 `span_s` 합, `boardings_per_vehicle`은 `round(x, 3)`.
- `trips[]`는 **실행된** 영업 trip만, `timetable.trips` 순서로 싣는다. `boardings`는 그 trip `arrive.boarded` 합, `max_load`는 `arrive.load` 최댓값.
- `routes[]`는 `timetable.routes` 순서. `trips_run`은 실행된 영업 trip 수, `satisfied`·`mean_wait_s`는 그 노선 trip에서 탄 승객 기준. 값이 없으면 `null`.

### 6.4 셔틀 GTFS

`timetable.json`의 **예정** 시각을 내보낸다(실현 로그가 아님). 영업 trip만 내보내고 공차는 `block_id`로만 이어진다. 파일은 UTF-8(BOM 없음), 줄 끝 `\n`, 행 순서는 표의 정렬 기준.

| 파일 | 열 | 값 |
| --- | --- | --- |
| `agency.txt` | `agency_id, agency_name, agency_url, agency_timezone` | `busstrike-sim`, `파업 대체 셔틀(시뮬레이션)`, 자리표시 URL, `Asia/Seoul` |
| `routes.txt` | `route_id, agency_id, route_short_name, route_long_name, route_type` | `route_id` 그대로, 이름은 `route_id`와 `첫 정류장–마지막 정류장`, `route_type=3`. `route_id` 순 |
| `trips.txt` | `route_id, service_id, trip_id, direction_id, block_id` | `direction_id = leg_index`(0/1, leg가 3개 이상이면 `leg_index % 2`), `block_id = vehicle_id`. `trip_id` 순 |
| `stop_times.txt` | `trip_id, arrival_time, departure_time, stop_id, stop_sequence, pickup_type, drop_off_type, timepoint` | 아래 |
| `calendar.txt` | `service_id, monday…sunday, start_date, end_date` | `service_id = "STRIKE_" + YYYYMMDD`, `service_date`의 요일만 1, 시작·끝 = `service_date` |

`stop_times.txt` 규칙
- `arrival_time = departure_time = sched_s`를 `HH:MM:SS`로(24시 넘으면 `25:10:00`처럼). **둘을 같게 두는 이유**: 엔진의 승차 마감이 도착 스냅숏이므로, D의 경로 탐색이 "출발 시각까지 오면 탄다"고 안내해도 시뮬레이션과 어긋나지 않는다.
- `stop_sequence = stop_seq`. 첫 정류장 `drop_off_type=1`, 마지막 정류장 `pickup_type=1`, 나머지 0. `timepoint`는 첫 정류장만 1.
- `(trip_id, stop_sequence)` 순.

- `stop_id`는 NetworkSnapshot(A의 버스 GTFS)의 ID를 그대로 쓴다. `stops.txt`는 스냅숏이 좌표를 줄 때만 쓰인 정류장을 복사해 넣고, 없으면 내보내지 않는다(그때는 A의 feed와 합쳐야 완결된 feed가 된다).
- `feed_info.txt`에 `plan_id`·`revision`을 `feed_version`으로 적는다. `plan_mode=simulation`임을 `agency_name`에 드러낸다.
- **4단계 구현에서 정한 세부**(`sim/gtfs.py`)
  - `feed_info.txt` 열은 `feed_publisher_name, feed_publisher_url, feed_lang, feed_version`. 값은 `agency_name`·자리표시 URL·`ko`·`<plan_id>@<revision>`.
  - `routes.txt`는 영업 trip이 하나라도 있는 노선만 쓴다(대수 0 노선은 빠진다). `route_short_name = route_id`, `route_long_name = 첫–끝 정류장`(`leg_index`가 가장 작은 영업 trip 기준, 구분자 `–`).
  - `service_date`는 `YYYY-MM-DD`만 받고 아니면 `INPUT_INVALID`. 요일은 `calendar` 모듈로 계산한다(`datetime` 미사용, 결정론 검사 유지).
  - `stops.txt`는 호출자가 `stop_coords`(정류장 ID → 위도·경도)를 줄 때만, 쓰인 정류장만 쓴다. 스냅숏에서 좌표를 읽는 연결은 5단계 몫이다.
  - 같은 `timetable`·`service_date`면 파일 바이트가 항상 같다. `read_gtfs()`가 다시 읽어 trip 단위로 돌려준다(왕복 테스트용).
  - CLI: `python -m busstrike.sim gtfs --scenario S --timetable T --out DIR`. 입력 오류는 종료 코드 2.
- 버린 대안: `frequencies.txt`(차량별 trip이 사라져 block을 못 잇는다), 실현 시각 내보내기(안내용 시간표가 승객 수에 따라 달라진다).

---

## 7. 대수 곡선 S(n)

### 7.1 실행 방식

`S_r(n)` = 노선 r에 n대를 넣었을 때 **충족 인원**(다른 지표도 같이 반환).

```text
fleet_curve(route, n_max, ctx) -> [CurvePoint]
    pax_r = [p for p in ctx.passengers if route의 영업 leg로 정적 서비스 가능(§4.5)]     # 한 번만 계산
    ref   = ctx.fleet를 vehicle_id 순으로 정렬한 앞 n_max대
    for n in 0 … n_max:
        tt  = build_timetable(plan={route: n}, fleet=ref[:n], …)
        ev  = simulate(tt, pax_r, …)                       # 로그 파일은 쓰지 않음
        pts.append(CurvePoint(n, satisfied, transported, wait_p90, total_m, eligible=len(pax_r)))
        if satisfied == len(pax_r): 나머지 n은 같은 값으로 채우고 중단
    return pts
```

- 노선을 **혼자** 돌린다. 승객은 그 노선으로 갈 수 있는 사람만 넣는다(나머지는 어차피 `no_service`라 결과에 영향이 없다). 실행 비용이 노선 크기에 비례한다.
- 입력 파싱·정렬·서비스 가능 판정은 `SimContext`에 한 번 만들어 두고 재사용한다.
- S(n)은 위상 효과 때문에 단조·오목이 아닐 수 있다. 값은 그대로 주고 다듬지 않는다(다듬을지는 C가 정한다).
- 차급이 섞인 fleet이면 차급별로 곡선을 따로 낸다(v1은 동일 조건 차량만 쓰는 C에 맞춰 단일 곡선).

### 7.2 정류장을 공유할 때

```text
route_clusters(routes, ctx) -> [[route_id]]
    eligible[r] = r로 갈 수 있는 승객 집합
    r1—r2 간선: eligible[r1] ∩ eligible[r2] ≠ ∅
    return 연결 성분 (각 성분과 성분 목록은 route_id 순)
```

- **성질**: 공통 승객이 없는 두 노선은 서로 영향을 주지 않는다(차량을 공유하지 않고, 대기열은 유효하지 않은 승객을 건너뛰며, 정류장 용량 제약이 없다). 따라서 클러스터가 다르면 `전체 충족 = Σ 클러스터별 충족`이 **정확히** 성립한다. 정류장만 같고 목적지가 달라 승객이 겹치지 않는 노선도 독립이다.
- 클러스터에 노선이 1개면 `S_r(n)`을 그대로 더해 쓴다.
- 2개 이상이면 단독 곡선은 **상한**이다(같은 승객을 중복 계산). 이때는
  - `overlap_ratio[r] = |다른 노선과 겹치는 승객| / |eligible[r]|`을 같이 돌려주고,
  - `cluster_eval(cluster, allocation)`로 클러스터 전체를 한 번에 시뮬레이션한다(그 클러스터 승객만 넣으므로 가볍다).
  - C는 겹치는 클러스터 안에서만 "한 대 추가 시 한계 이득"을 `cluster_eval`로 다시 계산한다.
- 최종 후보는 언제나 전체 시뮬레이션(`EvaluationPort`)으로 재평가한다. 곡선은 탐색용이다.

### 7.3 C의 근사 평가와 비교·보정

같은 `Inputs`·같은 `Plan`에 대해 C의 `evaluate()`와 시뮬레이션을 나란히 돌린다(5단계 `compare_with_estimate`).

| 열 | 내용 |
| --- | --- |
| `plan_id`, `allocation` | 대상 |
| `est_served`, `sim_satisfied`, `diff`, `ratio` | 충족 인원 비교 |
| `est_mean_wait_s`, `sim_mean_wait_s` | 대기 비교 |
| `est_distance_m`, `sim_total_m` | 운행거리 비교(같아야 정상) |
| `rank_est`, `rank_sim` | 후보 안에서의 순위 |

요약: 평균 절대 오차, 편향(평균 `diff`), 스피어만 순위상관, "근사 상위 3안 안에 시뮬 1위가 있는가".

이미 알고 있는 차이(원인별로 분리해서 본다)

| 항목 | C 근사 | 시뮬레이터 |
| --- | --- | --- |
| 차 선택 | 목적지 도착이 가장 빠른 trip | 유효한 첫 차 |
| 정원 배정 순서 | 수요의 도착 시각 순(하류 승객이 먼저 좌석을 잡을 수 있음) | 차량이 지나가는 순서 |
| 수요 단위 | 묶음(남은 정원만큼 분할) | 1명씩 |
| 정차 | 고정 | 기본 + 1인당 |
| 구간 시간 | 고정 | 출발 시각의 시간대 값 |
| 대기 | 평균 | 분포(P90 등) |

보정 순서
1. **일치 조건 테스트**: 1인당 정차 0, 고정 구간 시간, 정원이 남는 입력에서는 `est_served == sim_satisfied`여야 한다(시간표가 같고 정원 경합이 없으므로). 다르면 어댑터나 규칙 구현의 버그다.
2. 곡선은 보정하지 않고 **교체**한다: C의 배분 탐색이 노선별 근사 대신 `fleet_curve`·`cluster_eval` 값을 쓴다.
3. 탐색 중 근사를 계속 써야 하면 노선별 보정 계수 `β_r = Σ_n S_sim(n) / Σ_n S_est(n)`(분모 0이면 1)을 근사값에 곱한다. 계수와 근거 표를 실험 기록에 남긴다.
4. 순위상관이 낮거나 상위 3안에 시뮬 1위가 없으면 재평가 후보 수를 늘린다(판단 기준 값은 5단계에서 실측 후 제안).

버린 대안
- 다른 노선 배분을 고정해 두고 전체 시뮬레이션으로 한 점씩 계산: 점 하나에 전체 실행 비용이 들고 결과가 고정한 배분에 묶인다.
- 대기행렬 공식 같은 해석식 곡선: 시간표 위상·만차 누적을 못 담아 결국 시뮬레이션으로 다시 맞춰야 한다.
- 겹치는 노선의 승객을 미리 한 노선에 나눠 배정: 나누는 규칙이 결과를 좌우하고, 실제 탑승 규칙(§4)과 달라진다.

### 7.4 속도 목표

가정 규모(구로, 06–10시): 승객 3만–5만 명, 차량 30–60대, 영업 trip 수백 개, 이벤트 약 15만–25만 줄.

| 실행 | 목표(노트북 1코어, CPython 3.11 이상) |
| --- | --- |
| 전체 1회(시간표+엔진+로그 파일+지표) | **2초 이내** |
| 곡선용 1회(로그 파일 없음, 전체 승객) | 1초 이내 |
| 단독 노선 곡선의 한 점 | 0.1초 이내 |
| 노선 30개 × n 0–15 전체 곡선 | 1분 이내 |

- 근거: 이벤트당 수 µs 수준의 `heapq`+dict 연산. 참고로 기존 SimPy 기반 Draft PR이 합성 90만 명을 41–46초로 측정했다(독립 검증 전 잠정값, 5만 명 환산 약 2.5초).
- 2단계에서 합성 5만 명 입력을 테스트 안에서 생성해 측정하고 결과를 진행 기록에 남긴다. 목표를 못 맞추면 §4.2의 하위 줄 최적화를 먼저 적용한다.
- 2단계 실측(`tests/sim/test_determinism.py::SpeedMeasurementTests`, 합성 5만 명·30노선·62대·영업+공차 trip 810개·이벤트 약 9.8만 줄, Python 3.14, WSL2 단일 프로세스): 시간표+준비 0.08초, 엔진 0.31초, 로그 직렬화+지표 0.50초, 파일 읽기·쓰기를 포함한 `run()` 1.41초. 목표 안이라 하위 줄 최적화는 넣지 않았다. 이벤트 수가 추정(15만–25만)보다 적은 것은 합성 입력이 과부하(대당 수요가 많아 대기 한도 초과가 많음)이기 때문이다.

---

## 8. 독립 검증기

### 8.1 원칙

- 위치 `src/busstrike/evaluation/validator.py`. `busstrike.sim`을 import하지 않는다(테스트가 AST로 검사). 파일 파서·구간 시간 조회·정차 계산을 **이 문서를 보고 따로** 구현한다.
- 입력: `scenario.json`, `run_inputs.json`, `arrivals.jsonl`, `travel_times.json`, `plan.json`, `timetable.json`, `events.jsonl`, `metrics.json`, `run.json`.
- `timetable.json`은 엔진의 입력이므로 검증기도 입력으로 받는다. 대신 시간표 자체를 `plan.json`·fleet·정책에 비춰 검사한다(V14). 생성 규칙(P 계산)을 다시 구현하지는 않는다.
- 로그에서 재구성하는 것: 차량별 정차 순서·차내 인원, 승객별 결과, 정류장별 대기 집합.

### 8.2 불변식

| ID | 이름 | 검사 |
| --- | --- | --- |
| V01 | 로그 형식 | 알려진 type, type별 필수 키, `seq`가 0부터 연속, §6.1 정렬 키가 비감소 |
| V02 | 인원 보존 | 입력 승객마다 최종 결과가 정확히 1개(`alight` / `abandon` / `no_service` / `left_waiting`). 모르는 `passenger_id` 없음. `board` 수 = `alight` 수. `발생 = 수송 + 포기 + 종료 시 대기` |
| V03 | 정원 | `board`/`alight`로 재구성한 차내 인원이 항상 `0 ≤ load ≤ seat+standing`. `arrive`·`depart`의 `load`, `alighted`, `boarded`가 재구성값과 같음. trip 종료 시 0 |
| V04 | 차량 일정 | 차량별 `visit`가 0부터 연속. 정차마다 `arrive` 뒤 `depart`(trip 마지막 정류장 제외). 정차 순서가 `timetable.json`의 duty·정류장 순서와 일치(취소된 cycle 이후는 없어야 함). 한 차량이 두 trip에 동시에 있지 않음 |
| V05 | 시간 단조 | 차량별·승객별 이벤트 시각 비감소. `arrive.t ≤ depart.t ≤ 다음 arrive.t` |
| V06 | 승차 전 도착 | `board.t ≥ arrival_s`, `board.t ≤ arrival_s + max_wait_s`, `stop_id ∈ origin_stop_ids`, 같은 `(vehicle_id, visit)`의 `arrive`와 시각·정류장 일치, 영업 trip, 마지막 정류장 아님. 승객당 `board` 최대 1회 |
| V07 | 하차는 승차 뒤 | 같은 차량·같은 trip, `stop_seq(alight) > stop_seq(board)`, `stop_id ∈ dest_stop_ids`, 그 사이에 허용 목적지를 지나치지 않음(가장 이른 허용 정류장), `arrive`와 시각 일치 |
| V08 | 구간 시간 | 연속 정차에서 `다음 arrive.t − depart.t == travel_times[(a,b)]`의 `depart.t` 시간대 값 |
| V09 | 정차·회차 | `depart.t − arrive.t ==` §3.3의 정차식. trip 사이: `다음 trip 첫 arrive.t == max(sched_s, 앞 trip 종점 arrive.t + 하차 정차 + turnaround(영업 뒤) + cycle_rest(새 cycle))`. `cancel`이 있으면 §3.4의 취소 조건이 실제로 성립하고, 없으면 성립하지 않음 |
| V10 | 운영시간 | 차량 이벤트(`cancel` 제외) 시각이 모두 `[max(W_s, available_start_s), min(W_e, available_end_s)]` 안 |
| V11 | 탑승 규칙 | 정차마다 대기 집합을 재구성해: ① 남겨진 유효 승객이 있으면 그 차는 만차로 떠났다 ② 탄 승객보다 줄 앞에 있던 유효 승객이 남겨지지 않았다 ③ `left_behind`가 재구성값과 같다 ④ 승객의 첫 남겨짐에 `denied`가 있고 그 외에는 없다 |
| V12 | 미수송 처리 | `abandon.t == arrival_s + max_wait_s ≤ W_e`. `left_waiting.t == W_e < arrival_s + max_wait_s`. `no_service.t == arrival_s`. cause가 §4.5 규칙과 일치(`FULL` ⇔ 남겨진 적 있음, `NO_ROUTE_*` ⇔ 시간표 패턴으로 정적 판정) |
| V13 | 지표 재계산 | `metrics.json`의 인원·비율·대기/차내/총 이동 통계·차량 거리·trip 재차율이 로그에서 다시 계산한 값과 같음 |
| V14 | 시간표 | `plan.json`의 노선·대수와 일치(노선별 차량 수, leg 정류장열). 차량이 fleet에 있고 노선당 중복 없음. 예정 시각이 차량 유효 창 안, trip끼리 겹치지 않고 이어짐. `origin=="generated"`면 `cycle_index=0` trip의 anchor 예정 시각이 `start + floor(k·period_s/n)`(k = 노선 안 차량 순번)이고, cycle m의 anchor 예정 시각이 `start + floor(k·period_s/n) + m·period_s` 이상(3단계에서 고침, §8.3 아래) |
| V15 | 식별·해시 | `run.json`의 `plan_id`·`revision`·`input_bundle_id`가 입력과 같고, 기록된 SHA-256이 실제 파일과 같음. `run_inputs.checksum` 재계산 일치 |

재구성 의사코드(V03·V06·V07·V11이 공유)

```text
visits = 로그의 arrive를 (vehicle_id, visit)로 색인;  각 visit에 board/alight/denied 목록을 붙임
pattern(trip_id) = timetable.json의 stop_times
for visit in 로그 순서(= 시간순):
    t, stop, k, trip = visit
    # 이 순간의 대기 집합: 정적으로 서비스 가능하고, arrival_s ≤ t ≤ arrival_s + max_wait_s 이며,
    # stop ∈ origin_stop_ids 이고, 아직 board 기록이 없는(또는 이 visit에서 board한) 승객
    waiting  = 위 조건의 승객을 (arrival_s, passenger_id)로 정렬
    eligible = [p for p in waiting if pattern(trip)에서 k 뒤에 p의 dest가 있음]      # 영업 trip, k < last 일 때만
    room     = capacity − (이 visit 하차 반영 후 load)
    expected_board = eligible[:room];  expected_left = eligible[room:]
    check: 로그의 board 목록 == expected_board (순서 포함)                          # V11 ①②
    check: left_behind == len(expected_left)                                        # V11 ③
    check: denied 목록 == [p for p in expected_left if p가 처음 남겨짐]              # V11 ④
    남겨진 횟수[p] += 1 for p in expected_left                                       # V12의 FULL 판정에 사용
```

요구사항 대응: 인원 보존 V02 · 정원 초과 없음 V03 · 차량 일정 중복 없음 V04 · 시간 단조성 V05 · 승차 전 도착 V06 · 하차 정류장이 승차 이후 V07 · 구간 시간 일치 V08 · 운영시간 준수 V10 · 그 밖은 규칙 구현 오류를 잡기 위한 추가 검사.

### 8.3 위반 리포트 `validation.json`

| 필드 | 형식 | 의미 |
| --- | --- | --- |
| `validator_version` | string | |
| `run_id`, `plan_id`, `plan_revision`, `input_bundle_id` | | 대상 |
| `verified` | bool | 모든 검사가 `pass`일 때만 true |
| `checks[]` | `{id, name, status, violations}` | `status`: `pass` / `fail` / `skipped`(앞 검사 실패로 재구성 불가). `violations`는 건수. V01→V15 순 |
| `violations[]` | 아래 | `(check_id, seq, passenger_id, vehicle_id)` 순. 검사당 최대 100건만 싣고 건수는 `checks[].violations`에 전부 |
| `truncated` | bool | 100건 제한에 걸렸는지 |

`violations[]` 항목: `{check_id, message, seq, t, vehicle_id, trip_id, stop_id, passenger_id, expected, actual}` — 해당 없는 키는 생략. `seq`는 문제가 된 로그 줄.

- `independently_verified`(C `Assessment`) = `verified`. `violations`(C) = 실패한 `check_id` 목록.
- V01이 실패하면 나머지는 `skipped`로 두고 끝낸다(깨진 로그로 다른 결론을 내지 않는다).
- CLI: `python -m busstrike.evaluation.validator --run-dir <dir>` → `validation.json` 쓰고, 위반이 있으면 종료 코드 1.

3단계 구현에서 정한 세부
- **입력 위치**: `run.json`·`timetable.json`·`events.jsonl`·`metrics.json`은 `--run-dir`에서 읽는다(뒤의 셋은 `run.json`의 `*_uri`). `scenario.json`·`run_inputs.json`·`plan.json`은 `run.json`에 경로가 없으므로(§0.2 절대 경로 금지) 기본은 같은 디렉터리이고, 다르면 `--scenario`·`--run-inputs`·`--plan`으로 준다. `arrivals.jsonl`·`travel_times.json`은 `run_inputs.json`의 URI를 따른다. `--out`으로 리포트 경로를 바꿀 수 있다. 공개 함수는 `validate_run(run_dir, *, scenario_path=None, run_inputs_path=None, plan_path=None) -> dict`(파일은 쓰지 않음)와 `write_report`·`failed_checks`.
- **입력을 읽을 수 없을 때**(파일 없음, 로그 외 입력 문서의 JSON·필드 형식 오류): 위반이 아니라 `ValidatorInputError`, CLI 종료 코드 **2**. 이벤트 로그의 형식 오류만 V01 위반이다.
- `validator_version = "busstrike-validator/0.1.0"`. `plan_id`·`plan_revision`은 `plan.json`, `run_id`는 `run.json`, `input_bundle_id`는 `run_inputs.json`에서 가져온다. 리포트는 §0.2 구조 문서 규칙(정렬 키 JSON + `\n`)으로 쓰고, 실행 시각 등은 넣지 않아 같은 입력이면 바이트가 같다.
- `violations[]` 정렬은 `(check_id, seq, passenger_id, vehicle_id)`이고 키가 없으면 `seq=-1`·빈 문자열로 앞에 둔다. 같으면 `message`, `expected/actual/trip_id/t`로 끊는다. 100건 제한은 이 정렬 뒤 앞에서부터.
- 검사가 깨진 입력 조합 때문에 예외로 멈추면 그 검사는 `fail`이고 위반 `check aborted: …` 한 건을 남긴다(결론을 "통과"로 두지 않는다).
- **V01** 세부: type별 키 집합이 §6.1 표와 정확히 같음(없는 키·남는 키 모두 위반), 정수 키는 bool 아닌 int, 문자열 키는 빈 문자열 아님, 키 순서 = §6.1, 줄이 `json.dumps(…, ensure_ascii=False, separators=(",", ":"))` 결과와 같음, 마지막 줄 `\n`. 정렬 키의 `arrival_s`는 입력에 없는 승객이면 -1로 두고 V02가 따로 보고한다.
- **검사 분담**(같은 문제를 여러 검사가 중복 보고하지 않게): `board`의 `t`·`stop_id`·`stop_seq`·`trip_id`가 그 `arrive`와 다르면 V06, `alight`이면 V07, `depart`·`denied`면 V04. 같은 `(vehicle_id, visit)`의 `arrive`가 없는 하위 이벤트는 V04. 최종 결과가 없거나 둘 이상인 승객은 V02만 보고하고, V12는 있는 결과 이벤트의 시각·cause만 본다.
- **V04** "한 차량이 두 trip에 동시에 있지 않음"은 visit 순서로 trip이 바뀔 때 새 trip의 첫 `arrive.t`가 앞 trip의 마지막 이벤트 시각 이상인지로 본다. duty가 있는데 이벤트가 없거나 중간에 끊겼고 `cancel`도 없으면 위반이다.
- **V09**: 차량의 첫 visit은 첫 trip `sched_s[0]`에 와야 한다. 첫 cycle은 엔진이 취소 판정 없이 시작하므로(§3.4는 "다음" cycle 판단) 취소 조건을 보지 않는다.
- **V10**에서 `cancel`은 뺀다. 이동이 아니라 판단 기록이고 `t = ready`라, 영업 leg 회차 뒤에는 `W_e`를 넘을 수 있다(§3.4). `cancel`의 시각은 V09가 §3.4 식으로 본다.
- **V11**의 "아직 board 기록이 없는" 승객은 "로그 순서로 앞서 처리한 visit에서 탄 적이 없는" 승객이다(승차 시각이 아니라 승차가 속한 visit 기준). 그래서 `board.t`만 바꾼 변조는 V11이 아니라 V06이 잡는다. 정적 판정으로 `no_service`인 승객은 대기 집합에 넣지 않는다.
- **V13**은 §6.3과 위의 "2단계 구현에서 정한 세부"를 따로 구현해 `metrics.json` 전체를 잎(leaf) 경로 단위로 비교한다(`passengers.satisfied` 등). 수치는 같은 반올림 뒤 `==`.
- **V14 고침**: 1단계 표의 "같은 차량의 연속 cycle 간격이 `period_s` 이상"은 §1.3과 맞지 않았다. 생성기는 `s = max(start + floor(k·P/n) + m·P, prev_ready)`라, 직전 cycle이 시간대 경계에 걸려 계획 순환시간이 P보다 길면 그 cycle만 밀리고 다음 cycle은 슬롯으로 돌아온다(간격 < P). 합성 실행에서 실제로 나왔다. 검사를 "cycle m의 anchor ≥ 슬롯 `start + floor(k·P/n) + m·P`, `cycle_index`는 0부터 연속"으로 바꿨다. `prev_ready` 자체는 P 계산을 다시 구현해야 알 수 있어(§8.4) 보지 않는다. 나머지 V14 항목은 §1.5의 `validate_timetable` 목록과 같다.
- 속도(합성 5만 명·30노선·62대·이벤트 약 9.8만 줄, Python 3.14, WSL2): `validate_run` 4.75초(엔진 `run()` 1.48초).

### 8.4 버린 대안

- 검증기가 `sim`의 파서·모델을 재사용: 같은 버그를 같이 갖게 되어 독립성이 없어진다.
- 검증기가 시간표 생성 규칙(P 계산)까지 다시 구현: 생성기를 두 번 만드는 셈이다. 시간표는 입력으로 받고 제약만 검사한다.
- 엔진을 한 번 더 돌려 로그를 비교: 결정론 테스트일 뿐 규칙이 맞는지는 확인하지 못한다.
- 위반을 전부 싣기: 과부하 로그에서 리포트가 로그보다 커진다.

---

## 9. 테스트

`tests/sim/`, `tests/evaluation/`, fixture는 `tests/fixtures/sim/`(합성만). 실행: `python3 -m unittest discover -s tests -t . -v`.

아래 기대값은 손으로 계산한 것이고, 문서 작성 때 임시 스크립트로 한 번 더 맞춰 봤다(레포에 없음). **표가 기준**이다. 로그 SHA-256은 참고값이며, 다르면 표와 로그를 줄 단위로 비교해 어느 쪽이 설계와 다른지 확인한다.

### 9.1 공통 fixture F1

- 정류장 A, B, S(역). 구간(시간대 1개, `bin_start_s=0`): A→B 120초/1000m, B→S 180초/1500m, S→B 180초/1500m, B→A 120초/1000m.
- 노선 R1: leg0 영업 `[A, B, S]`, leg1 공차 `[S, B, A]`.
- 정책: `dwell_base_s=10`, `dwell_per_boarding_s=2`, `dwell_per_alighting_s=1`, `turnaround_s=60`, `cycle_rest_s=0`.
- 운영시간 `[0, 1500]`, 차량 가용 `[0, 1500]`, 입석 0.

계획 순환시간(t0=0): A 0 → 출발 10 → B 130 → 출발 140 → S 320 → 회차 60 → S 380 → B 560 → A 680. `P = 680`.

### 9.2 T1 — 정류장 3·차량 1·승객 5 (기본 흐름)

차량 V1(좌석 3), R1에 1대. `max_wait_s=600`, `max_journey_s=1200`. 승하차 여유 `allow = (2+1)×3×1 = 9`.

시간표: cycle 0 `0+680+9 ≤ 1500` ✔, cycle 1 `680+680+9=1369 ≤ 1500` ✔, cycle 2 `1360+680+9 > 1500` ✘.

| trip | 영업 | 예정 시각 |
| --- | --- | --- |
| `R1-V1-000-0` | ✔ | A 0, B 130, S 320 |
| `R1-V1-000-1` | 공차 | S 380, B 560, A 680 |
| `R1-V1-001-0` | ✔ | A 680, B 810, S 1000 |
| `R1-V1-001-1` | 공차 | S 1060, B 1240, A 1360 |

승객

| ID | 출발 | 목적지 | 도착 |
| --- | --- | --- | --- |
| p1 | A | {S} | 0 |
| p2 | A | {B} | 0 |
| p3 | A | {S} | 5 |
| p4 | B | {S} | 100 |
| p5 | B | {S} | 700 |

차량 진행

| visit | trip | 정류장 | arrive | 하차 | 승차 | 정차 | depart |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 000-0 | A | 0 | – | p1, p2 | 10+2·2=14 | 14 |
| 1 | 000-0 | B | 134 | p2 | p4 | 10+2+1=13 | 147 |
| 2 | 000-0 | S | 327 | p1, p4 | – | 1·2=2 | (종점) |
| 3 | 000-1 | S | 389 = max(380, 327+2+60) | | | 0 | 389 |
| 4 | 000-1 | B | 569 | | | 0 | 569 |
| 5 | 000-1 | A | 689 | | | 0 | (종점) |
| 6 | 001-0 | A | 689 = max(680, 689), 검사 689+680+9=1378 ≤ 1500 | – | – (p3는 605에 만료) | 10 | 699 |
| 7 | 001-0 | B | 819 | – | p5 | 12 | 831 |
| 8 | 001-0 | S | 1011 | p5 | – | 1 | (종점) |
| 9 | 001-1 | S | 1072 = max(1060, 1011+1+60) | | | 0 | 1072 |
| 10 | 001-1 | B | 1252 | | | 0 | 1252 |
| 11 | 001-1 | A | 1372 | | | 0 | (종점) |

승객 결과

| ID | 결과 | 승차 | 대기 | 하차 | 차내 | 총 | 비고 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| p1 | served | 0 | 0 | 327 | 327 | 327 | |
| p2 | served | 0 | 0 | 134 | 134 | 134 | 중간 하차 |
| p3 | abandon @605 | | | | | | `WAIT_EXCEEDED` (5초 늦어 첫 차를 놓침) |
| p4 | served | 134 | 34 | 327 | 193 | 227 | |
| p5 | served | 819 | 119 | 1011 | 192 | 311 | |

지표: generated 5, transported 4, satisfied 4, abandoned 1, no_service 0, left_waiting 0, `transport_rate = satisfaction_rate = 0.8`. 대기 `[0,0,34,119]` → mean 38.25, p50 0, p90 119, max 119. 차내 mean 211.5. 총 이동 mean 249.75, p90 327. V1: revenue 5000m, deadhead 5000m, `first_s=0`, `last_s=1372`. trip `000-0`: 승차 3, 최대 재차 2, 좌석 재차율 0.666667, 지연 0. trip `001-0`: 승차 1, 최대 재차 1, 0.333333, 지연 9. 남겨짐 0.

기대 로그 전체(29줄, 4093바이트, SHA-256 `796c7b6a88ec8a1379a1e80ebe3726b7e3b20e20cdc0ccde4e0b504ad3b05a78`):

```json
{"seq":0,"t":0,"type":"arrive","vehicle_id":"V1","trip_id":"R1-V1-000-0","visit":0,"stop_id":"A","stop_seq":0,"alighted":0,"boarded":2,"left_behind":0,"load":2}
{"seq":1,"t":0,"type":"board","vehicle_id":"V1","trip_id":"R1-V1-000-0","visit":0,"stop_id":"A","stop_seq":0,"passenger_id":"p1"}
{"seq":2,"t":0,"type":"board","vehicle_id":"V1","trip_id":"R1-V1-000-0","visit":0,"stop_id":"A","stop_seq":0,"passenger_id":"p2"}
{"seq":3,"t":14,"type":"depart","vehicle_id":"V1","trip_id":"R1-V1-000-0","visit":0,"stop_id":"A","stop_seq":0,"load":2}
{"seq":4,"t":134,"type":"arrive","vehicle_id":"V1","trip_id":"R1-V1-000-0","visit":1,"stop_id":"B","stop_seq":1,"alighted":1,"boarded":1,"left_behind":0,"load":2}
{"seq":5,"t":134,"type":"alight","vehicle_id":"V1","trip_id":"R1-V1-000-0","visit":1,"stop_id":"B","stop_seq":1,"passenger_id":"p2"}
{"seq":6,"t":134,"type":"board","vehicle_id":"V1","trip_id":"R1-V1-000-0","visit":1,"stop_id":"B","stop_seq":1,"passenger_id":"p4"}
{"seq":7,"t":147,"type":"depart","vehicle_id":"V1","trip_id":"R1-V1-000-0","visit":1,"stop_id":"B","stop_seq":1,"load":2}
{"seq":8,"t":327,"type":"arrive","vehicle_id":"V1","trip_id":"R1-V1-000-0","visit":2,"stop_id":"S","stop_seq":2,"alighted":2,"boarded":0,"left_behind":0,"load":0}
{"seq":9,"t":327,"type":"alight","vehicle_id":"V1","trip_id":"R1-V1-000-0","visit":2,"stop_id":"S","stop_seq":2,"passenger_id":"p1"}
{"seq":10,"t":327,"type":"alight","vehicle_id":"V1","trip_id":"R1-V1-000-0","visit":2,"stop_id":"S","stop_seq":2,"passenger_id":"p4"}
{"seq":11,"t":389,"type":"arrive","vehicle_id":"V1","trip_id":"R1-V1-000-1","visit":3,"stop_id":"S","stop_seq":0,"alighted":0,"boarded":0,"left_behind":0,"load":0}
{"seq":12,"t":389,"type":"depart","vehicle_id":"V1","trip_id":"R1-V1-000-1","visit":3,"stop_id":"S","stop_seq":0,"load":0}
{"seq":13,"t":569,"type":"arrive","vehicle_id":"V1","trip_id":"R1-V1-000-1","visit":4,"stop_id":"B","stop_seq":1,"alighted":0,"boarded":0,"left_behind":0,"load":0}
{"seq":14,"t":569,"type":"depart","vehicle_id":"V1","trip_id":"R1-V1-000-1","visit":4,"stop_id":"B","stop_seq":1,"load":0}
{"seq":15,"t":605,"type":"abandon","passenger_id":"p3","cause":"WAIT_EXCEEDED"}
{"seq":16,"t":689,"type":"arrive","vehicle_id":"V1","trip_id":"R1-V1-000-1","visit":5,"stop_id":"A","stop_seq":2,"alighted":0,"boarded":0,"left_behind":0,"load":0}
{"seq":17,"t":689,"type":"arrive","vehicle_id":"V1","trip_id":"R1-V1-001-0","visit":6,"stop_id":"A","stop_seq":0,"alighted":0,"boarded":0,"left_behind":0,"load":0}
{"seq":18,"t":699,"type":"depart","vehicle_id":"V1","trip_id":"R1-V1-001-0","visit":6,"stop_id":"A","stop_seq":0,"load":0}
{"seq":19,"t":819,"type":"arrive","vehicle_id":"V1","trip_id":"R1-V1-001-0","visit":7,"stop_id":"B","stop_seq":1,"alighted":0,"boarded":1,"left_behind":0,"load":1}
{"seq":20,"t":819,"type":"board","vehicle_id":"V1","trip_id":"R1-V1-001-0","visit":7,"stop_id":"B","stop_seq":1,"passenger_id":"p5"}
{"seq":21,"t":831,"type":"depart","vehicle_id":"V1","trip_id":"R1-V1-001-0","visit":7,"stop_id":"B","stop_seq":1,"load":1}
{"seq":22,"t":1011,"type":"arrive","vehicle_id":"V1","trip_id":"R1-V1-001-0","visit":8,"stop_id":"S","stop_seq":2,"alighted":1,"boarded":0,"left_behind":0,"load":0}
{"seq":23,"t":1011,"type":"alight","vehicle_id":"V1","trip_id":"R1-V1-001-0","visit":8,"stop_id":"S","stop_seq":2,"passenger_id":"p5"}
{"seq":24,"t":1072,"type":"arrive","vehicle_id":"V1","trip_id":"R1-V1-001-1","visit":9,"stop_id":"S","stop_seq":0,"alighted":0,"boarded":0,"left_behind":0,"load":0}
{"seq":25,"t":1072,"type":"depart","vehicle_id":"V1","trip_id":"R1-V1-001-1","visit":9,"stop_id":"S","stop_seq":0,"load":0}
{"seq":26,"t":1252,"type":"arrive","vehicle_id":"V1","trip_id":"R1-V1-001-1","visit":10,"stop_id":"B","stop_seq":1,"alighted":0,"boarded":0,"left_behind":0,"load":0}
{"seq":27,"t":1252,"type":"depart","vehicle_id":"V1","trip_id":"R1-V1-001-1","visit":10,"stop_id":"B","stop_seq":1,"load":0}
{"seq":28,"t":1372,"type":"arrive","vehicle_id":"V1","trip_id":"R1-V1-001-1","visit":11,"stop_id":"A","stop_seq":2,"alighted":0,"boarded":0,"left_behind":0,"load":0}
```

### 9.3 T2 — 만차·FIFO·충족 한도

F1, 차량 V1(좌석 **2**), `max_wait_s=900`, `max_journey_s=1000`. `allow = 3×2×1 = 6`. 시간표는 T1과 같다.

승객: q1·q2·q3 = A→{S}, 도착 0. q4·q5 = B→{S}, 도착 100.

| visit | 정류장 | arrive | 승차 | 남겨짐(`left_behind`) | 정차 | depart |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | A | 0 | q1, q2 | q3 (1) — 첫 `denied` | 14 | 14 |
| 1 | B | 134 | – | q4, q5 (2) — 첫 `denied` 둘 | 10 | 144 |
| 2 | S | 324 | (q1, q2 하차) | | 2 | |
| 3–5 | 공차 | S 386, B 566, A 686 | | | | |
| 6 | A | 686, 검사 686+680+6=1372 ≤ 1500 | q3 | 0 | 12 | 698 |
| 7 | B | 818 | q4 | q5 (1) — `denied` 재기록 없음 | 12 | 830 |
| 8 | S | 1010 | (q3, q4 하차) | | 2 | |
| 9–11 | 공차 | S 1072, B 1252, A 1372 | | | | |

| ID | 결과 | 대기 | 총 | 남겨진 횟수 | 충족 |
| --- | --- | --- | --- | --- | --- |
| q1, q2 | served, 하차 324 | 0 | 324 | 0 | ✔ |
| q3 | served, 승차 686, 하차 1010 | 686 | 1010 | 1 | ✘ `JOURNEY_EXCEEDED` |
| q4 | served, 승차 818, 하차 1010 | 718 | 910 | 1 | ✔ |
| q5 | abandon @1000 | | | 2 | `FULL` |

지표: generated 5, transported 4, **satisfied 3**, abandoned 1. `denied.boardings = 4`, `denied.passengers = 3`. 대기 mean 351.0, p90 718. 로그 32줄, SHA-256 `167c8484a4b4cc79fffc88203410c1c9d9f66b95309814ccbc51eb3afe50a4f0`.

**T2b — 지연으로 cycle 취소**: T2에서 운영시간·가용 종료만 **1370**으로 줄인다. 시간표에는 cycle 1이 있다(`680+680+6=1366 ≤ 1370`). 실행에서는 `686+680+6=1372 > 1370`이라 취소된다.

- `{"t":686,"type":"cancel","vehicle_id":"V1","trip_id":"R1-V1-001-0","visit":6,"cause":"LATE"}` (seq 17).
- q3 abandon @900 `FULL`, q4·q5 abandon @1000 `FULL`. transported 2, satisfied 2, abandoned 3. V1 `last_s=686`, revenue 2500m, deadhead 2500m, `cycles_cancelled=1`.
- 로그 21줄, SHA-256 `fbcf73f559c6967af8e082d01701bc7d80cf26633e7d828b253bc8c8e86674e9`.

### 9.4 T3 — 정류장 공유·목적지 집합·동시각·노선 없음

정류장 A, B, S1, S2, X. 구간: B→S1 180초/1500m, S1→B 180/1500, B→S2 100초/800m, S2→B 100/800. 정책은 F1과 같다. 운영시간 `[0, 450]`.

- R1: 영업 `[B, S1]`, 공차 `[S1, B]`. R2: 영업 `[B, S2]`, 공차 `[S2, B]`. 각 1대.
- 차량 V1·V2(좌석 1). 배정 규칙(§1.4)으로 R1←V1, R2←V2.
- 계획: R1 B 0 → S1 190 → 공차 S1 250 → B 430 (`P=430`). R2 B 0 → S2 110 → 공차 S2 170 → B 270 (`P=270`). `allow=3`. 두 노선 모두 cycle 0만 편성(R2 cycle 1: `270+270+3=543 > 450`).

승객(모두 도착 0, `max_wait_s=600`, `max_journey_s=1200`)

| ID | 출발 | 목적지 | 기대 |
| --- | --- | --- | --- |
| w1 | B | {S1, S2} | V1(ID가 앞선 차)에 승차 → S1 하차 192 |
| w2 | B | {S1, S2} | V1에서 만차로 남겨짐(`denied` 1회) → 같은 초 V2에 승차 → S2 하차 112 |
| w3 | B | {A} | `no_service` @0 `NO_ROUTE_TO_DEST` |
| w4 | X | {S1} | `no_service` @0 `NO_ROUTE_AT_STOP` |
| w5 | S1 | {B} | `no_service` @0 `NO_ROUTE_AT_STOP` (S1은 영업 leg의 종점과 공차 leg로만 방문) |

t=0의 로그 순서: `no_service` w3, w4, w5 → V1 `arrive`(boarded 1, left_behind 1), `board` w1, `denied` w2 → V2 `arrive`(boarded 1, left_behind 0), `board` w2 → t=12 V1 `depart`, V2 `depart`.

차량: V1 B 0 → 출발 12 → S1 192(하차 정차 1) → 공차 S1 253 → B 433. V2 B 0 → 출발 12 → S2 112 → 공차 S2 173 → B 273.

지표: generated 5, transported 2, satisfied 2, no_service 3(`NO_ROUTE_AT_STOP` 2, `NO_ROUTE_TO_DEST` 1), 대기 모두 0, `denied.boardings=1`, `denied.passengers=1`. V1 revenue 1500m/deadhead 1500m, V2 800m/800m. 로그 20줄, SHA-256 `caee12748b596343cdd1d9e1cacfa8f702e17e5878467da7f0d0da6c025b662d`.

클러스터 테스트(§7.2): w1·w2가 두 노선 모두에 유효하므로 `route_clusters = [["R1","R2"]]`. w1·w2의 목적지를 각각 {S1}, {S2}로 바꾸면 `[["R1"],["R2"]]`이고 단독 실행 결과의 합이 함께 돌린 결과와 같다. (2단계는 `curve.py`가 없으므로 "단독 실행 합 = 함께 실행" 성질만 엔진으로 검사한다. `route_clusters` 자체의 기대값 검사는 5단계.)

### 9.5 T4 — 시간대별 구간 시간·종료 시 대기

F1에서 A→B만 `profile = [[0,120],[600,240]]`. 운영시간 `[0, 1700]`. V1(좌석 3), `max_wait_s=600`, `max_journey_s=1200`.

- `lookup(A,B,599)=120`, `lookup(A,B,600)=240`.
- `P = max(cycle(0)=680, cycle(600)=800) = 800`. cycle(600): A 600 → 출발 610(240초) → B 850 → 출발 860 → S 1040 → 1100 → B 1280 → A 1400.
- 시간표: cycle 0은 T1과 같음(A 0 … A 680). cycle 1: A 800, B 1050, S 1240 / 공차 S 1300, B 1480, A 1600 (`800+800+9 ≤ 1700`). cycle 2 없음.

| ID | 출발→목적지 | 도착 | 기대 |
| --- | --- | --- | --- |
| x1 | A→{S} | 790 | 승차 800(대기 10), 출발 812, B 1052(A→B 240초 적용), 출발 1062, S 1242 하차. 차내 442, 총 452 |
| x2 | A→{S} | 801 | 1초 차이로 놓침 → abandon @1401 `WAIT_EXCEEDED` |
| x3 | B→{S} | 1500 | 한도 2100 > 1700 → left_waiting @1700 `SERVICE_ENDED` |

차량: cycle 0은 승객 없이 예정대로(A 0/10, B 130/140, S 320, 공차 380·560·680). cycle 1: A 800 … S 1242, 공차 S 1303 = max(1300, 1242+1+60), B 1483, A 1603.

지표: generated 3, transported 1, satisfied 1, abandoned 1, left_waiting 1. 로그 24줄, SHA-256 `4de62ffa4f405121a74f4b3891239d474cf049c519e01b43a9f669806ab1651f`.

### 9.6 T5 — 승객 발생 실현기

시간대 길이 600초, 시작 `[0, 600, 1200]`, 유량 `[2.5, 0, 1.2]`명 → `flows_micro = [2_500_000, 0, 1_200_000]`(합 3.7명).

| `u` (micro) | 인원 | 도착 시각 |
| --- | --- | --- |
| 0 | 4 | 0, 240, 480, 1450 |
| 500 000 | 4 | 120, 360, 1200, 1700 |
| 699 999 | 4 | 167, 407, 1299, 1799 |
| 700 000 | 3 | 168, 408, 1300 |
| 800 000 | 3 | 192, 432, 1350 |

- 계산 예(`u=500 000`): 문턱 0.5M, 1.5M, 2.5M, 3.5M. 0.5M → `0 + 500000·600//2500000 = 120`. 2.5M은 첫 시간대 `[0, 2.5M)`에 속하지 않고, 유량 0인 둘째를 건너뛰어 셋째 `[2.5M, 3.7M)`의 시작 → 1200. 3.5M → `1200 + 1000000·600//1200000 = 1700`.
- `u < 700 000`이면 4명, 아니면 3명 → 기댓값 3.7명.
- `u_micro` 고정 벡터 3개(§2.2).
- 유량 문자열 `"0.1"`×10개 시간대의 합이 정확히 1 000 000 micro인지(float 누적 오차 없음).
- OD 행의 입력 순서를 섞어도 `arrivals.jsonl`이 바이트 단위로 같은지.

### 9.7 T6 — 시간표 생성기(대수별)

F1, 좌석 3(`allow=9`), 운영시간 `[0, 1500]`, `P=680`.

| n | 차량 | 오프셋 `floor(k·680/n)` | 편성된 cycle의 A 출발 | 편성 안 된 첫 cycle |
| --- | --- | --- | --- | --- |
| 1 | V1 | 0 | 0, 680 | 1360 (`1360+689 > 1500`) |
| 2 | V1 / V2 | 0 / 340 | V1: 0, 680 · V2: 340 | V2 1020 (`1020+689=1709`) |
| 3 | V1 / V2 / V3 | 0 / 226 / 453 | V1: 0, 680 · V2: 226 · V3: 453 | V2 906 (`1595`), V3 1133 (`1822`) |
| 0 | – | – | trip 없음 | – |

n=2의 V2: 영업 A 340, B 470, S 660 / 공차 S 720, B 900, A 1020. n=3의 V2: A 226, B 356, S 546 / S 606, B 786, A 906. V3: A 453, B 583, S 773 / S 833, B 1013, A 1133.

추가 검사: 대수 합이 가용 대수 초과 → `PLAN_INFEASIBLE` · leg가 이어지지 않음 → `PLAN_INFEASIBLE` · `initial_stop_id`가 anchor가 아님 → `PLAN_INFEASIBLE`(`DEADHEAD_DATA_REQUIRED`) · 구간 누락 → `DATA_INCOMPLETE` · 순환형 `[A,B,S,A]`와 두 역 연결형 `[S1…S2],[S2…S1]`이 같은 함수로 편성됨.

### 9.8 T7 — 노선+대수만 있는 지자체안 재현, 공정성

1. `plan.json`에 `routes=[R1, vehicle_count=1]`만 있고 `trips=[]`인 안(지자체안 형태)을 T1 조건으로 실행 → 시간표가 §9.2 표와 같고 로그가 T1 기대 로그와 **바이트 단위로 같다**.
2. 같은 안의 `source`만 `"municipal"` / `"general_ai"` / `"optimizer"`로 바꿔 세 번 실행 → `timetable.json`의 trips·duties와 `events.jsonl`이 모두 같다(`plan_id`가 들어가는 필드만 다름).
3. 1에서 만들어진 trips를 `plan.json`에 그대로 넣은 안(`origin="provided"`) → 로그가 1과 같다.
4. `busstrike/sim/` 소스에 `source` 값을 읽는 코드가 없음을 AST 검사로 확인한다.

### 9.9 결정론 테스트

- 같은 입력으로 두 번 실행 → `events.jsonl`, `metrics.json`, `timetable.json`, `run.json` 바이트 동일.
- `PYTHONHASHSEED=0`과 `=12345`로 각각 하위 프로세스 실행 → 동일(set·dict 순서 의존 검출).
- `plan.json`의 routes 순서, fleet의 vehicles 순서, 메모리에 올린 승객 리스트 순서를 섞어도 로그 동일.
- `sim/engine.py`·`timetable.py`·`eventlog.py`·`metrics.py`가 `random`, `time`, `datetime`, `uuid`, `os.urandom`을 import하지 않음(AST 검사).
- 무작위 합성 입력 수십 개(테스트 코드 안에서 고정 seed로 생성, 실현기와 무관)로 엔진을 돌린 뒤 검증기 통과 — 3단계에서 추가.

### 9.10 검증기 테스트(3단계)

T1–T4 기대 로그는 `verified=true`. T1 로그를 한 군데씩 고쳐 해당 검사만 실패하는지 본다.

| 변조 | 실패해야 하는 검사 |
| --- | --- |
| `alight` p5 줄 삭제 | V02 (V03도 실패 가능) |
| `board` 한 줄 복제해 정원 초과 | V03 |
| visit 4·5 순서 바꿈 | V01 |
| `depart` t=147 → 146 | V08, V09 |
| `board` p4의 t=134 → 90 | V06 |
| `alight` p2의 `stop_seq` 1 → 0 | V07 |
| 마지막 `arrive` t=1372 → 1501 | V08, V10 |
| `abandon` p3의 cause → `FULL` | V12 |
| T2에서 `board` q2를 q3로 교체(새치기) | V11 |
| `metrics.json`의 `satisfied` +1 | V13 |

3단계 구현에서 정한 것(`tests/evaluation/test_tamper.py`)
- 위 표는 "반드시 실패할" 최소 집합이다. 테스트는 실패하는 검사 집합을 **정확히** 고정한다. 로그를 바꾸면 지표가 로그와 어긋나 V13이 함께 실패하고, 승하차 수가 바뀌면 V09(정차식)·V03(재차 인원)이 함께 실패하는 연쇄를 그대로 둔다. 예: `board` p4 t→90은 {V06, V13}, `abandon` p3 cause→`FULL`은 {V12, V13}, 마지막 `arrive` t→1501은 {V08, V10, V13}.
- 의미 검사를 보려는 변조는 로그를 §6.1 키로 다시 정렬하고 출력 해시를 `run.json`에 다시 적는다(그래야 V01·V15가 아니라 해당 검사가 잡는다). 해시를 다시 적지 않으면 V15가 더해진다.
- "board 한 줄 복제해 정원 초과"는 T1(좌석 3)에서는 정원을 넘지 않는다. 그래서 정원 초과는 T2(좌석 2) visit 0의 `denied` q3를 `board`로 바꾼 변조로 본다(V03 `capacity exceeded`, load 3 > 2). 복제 변조도 남겨 둔다.
- 요구사항 완료 기준에 맞춰 더한 변조: 인원 누락(`abandon` 줄 삭제 → {V02, V13}, 모르는 승객 `no_service` 추가 → {V02}), 차량 일정 중복(T3에서 V2의 정차를 V1로 → V04 "trip starts before the previous trip…", 시간표에서 cycle 1 trip을 앞 trip과 겹치게 → V14), 시간 역행(공차 `depart` t 569→700 → V05), 승차 전 하차(p4 하차를 승차 전 visit 0으로 → V05·V07).

---

## 10. 모듈 구조

```text
src/busstrike/sim/
  __init__.py        공개 API 재노출
  models.py          불변 dataclass: Vehicle, Policy, Window, Leg, RouteSpec, PlanSpec, StopTime, Trip, Duty,
                     Timetable, Passenger, Event, SimError
  io.py              JSON·JSONL 읽기/쓰기, canonical_json, sha256_bytes/file, 입력 검증(§0.3·§0.4)
  traveltime.py      TravelTimeTable(lookup, distance, bin_starts)
  timetable.py       planned_cycle, assign_vehicles, build_timetable, validate_timetable, normalize_plan
  arrivals.py        u_micro, realize_od, realize_arrivals, write_arrivals, build_run_inputs
  engine.py          SimContext, prepare, simulate (파일 입출력·난수·시계 없음)
  eventlog.py        canonical_key, canonical_sort, dumps_events, write_events
  metrics.py         compute_metrics
  run.py             config_hash, run (디렉터리 단위 실행)
  __main__.py        CLI `python -m busstrike.sim run --scenario S --run-inputs R --plan P --out DIR` (SimError면 종료 코드 2)
  gtfs.py            export_gtfs                                         (4단계)
  curve.py           eligible_passengers, route_clusters, fleet_curve, cluster_eval   (5단계)
  adapters/planning_bridge.py   C Inputs·Plan → 시뮬 입력, evaluation_port, compare_with_estimate   (5단계)
src/busstrike/evaluation/
  __init__.py        설명만. CLI 경고를 피하려고 하위 모듈을 미리 import하지 않는다
  validator.py       validate_run, write_report, failed_checks, CLI       (3단계)
  _inputs.py         검증기 자체 파서·구간 시간 조회·해시 (sim import 금지)
  _checks.py         V01–V15, 로그 색인, 지표 재계산
tests/sim/, tests/evaluation/, tests/fixtures/sim/
```

의존 규칙
- `sim`의 핵심 모듈은 표준 라이브러리만 쓴다. `busstrike.planning`을 import하는 곳은 `sim/adapters/`뿐이다(M05는 M04를 역호출하지 않고, 어댑터는 타입 변환만 한다).
- `evaluation`은 `busstrike.sim`을 import하지 않는다.
- Python은 `pyproject.toml`의 `>=3.10`에 맞춘다.

공개 함수 시그니처

```python
# traveltime.py
class TravelTimeTable:
    @classmethod
    def from_dict(cls, raw: dict) -> "TravelTimeTable": ...
    def lookup(self, from_stop_id: str, to_stop_id: str, t: int) -> int: ...
    def distance(self, from_stop_id: str, to_stop_id: str) -> int: ...
    def bin_starts(self) -> tuple[int, ...]: ...

# timetable.py
def planned_cycle(route: RouteSpec, t0: int, policy: Policy,
                  tt: TravelTimeTable) -> tuple[tuple[tuple[int, ...], ...], int, int]: ...
def assign_vehicles(plan: PlanSpec, vehicles: Sequence[Vehicle]) -> dict[str, tuple[Vehicle, ...]]: ...
def build_timetable(plan: PlanSpec, vehicles: Sequence[Vehicle], window: Window,
                    policy: Policy, tt: TravelTimeTable) -> Timetable: ...
def validate_timetable(timetable: Timetable, plan: PlanSpec, vehicles: Sequence[Vehicle],
                       window: Window, tt: TravelTimeTable) -> None: ...          # SimError
def normalize_plan(plan: PlanSpec, vehicles: Sequence[Vehicle], window: Window,
                   policy: Policy, tt: TravelTimeTable) -> Timetable: ...

# arrivals.py
def u_micro(seed: int | str, kind: str, origin_stop_ids: Sequence[str], dest_stop_ids: Sequence[str]) -> int: ...
def realize_od(flows_micro: Sequence[int], bin_starts: Sequence[int], bin_length_s: int, u: int) -> list[int]: ...
def realize_arrivals(rows: Iterable[DemandFlow], station_stops: Mapping[str, Sequence[str]], policy: Policy,
                     window: Window, seed: int | str, bin_length_s: int) -> list[Passenger]: ...
def write_arrivals(passengers: Sequence[Passenger], path: Path) -> str: ...        # sha256

# engine.py
def prepare(passengers: Sequence[Passenger], vehicles: Sequence[Vehicle], window: Window,
            policy: Policy, tt: TravelTimeTable) -> SimContext: ...
def simulate(ctx: SimContext, timetable: Timetable,
             passengers: Sequence[Passenger] | None = None) -> list[Event]: ...   # 정렬·seq 부여까지 끝난 목록

# eventlog.py
def canonical_key(event: Event, arrival_s_by_passenger: Mapping[str, int]) -> tuple: ...
def dumps_events(events: Sequence[Event]) -> bytes: ...
def write_events(events: Sequence[Event], path: Path) -> str: ...                  # sha256

# metrics.py
def compute_metrics(events: Sequence[Event], passengers: Sequence[Passenger], timetable: Timetable,
                    vehicles: Sequence[Vehicle], policy: Policy, tt: TravelTimeTable) -> dict: ...

# run.py
def config_hash(policy: Policy, window: Window, vehicles: Sequence[Vehicle]) -> str: ...
def run(scenario_path: Path, run_inputs_path: Path, plan_path: Path, out_dir: Path) -> dict: ...   # run.json 내용

# gtfs.py
def export_gtfs(timetable: Timetable, service_date: str, out_dir: Path,
                stops: Mapping[str, StopInfo] | None = None) -> dict[str, str]: ...  # 파일명 → sha256

# curve.py
def route_clusters(routes: Sequence[RouteSpec], ctx: SimContext) -> list[list[str]]: ...
def fleet_curve(route: RouteSpec, n_max: int, ctx: SimContext) -> list[CurvePoint]: ...
def cluster_eval(routes: Sequence[RouteSpec], allocation: Mapping[str, int], ctx: SimContext) -> CurvePoint: ...

# adapters/planning_bridge.py
def evaluation_port(plan: "planning.models.Plan", inputs: "planning.models.Inputs") -> "planning.integration.Assessment": ...
def compare_with_estimate(inputs, plans) -> list[dict]: ...

# evaluation/validator.py
def validate_run(run_dir: Path, *, scenario_path: Path | None = None, run_inputs_path: Path | None = None,
                 plan_path: Path | None = None) -> dict: ...                       # validation.json 내용(쓰지 않음)
def write_report(report: dict, path: Path) -> None: ...
def failed_checks(report: dict) -> list[str]: ...                                   # C Assessment.violations
```

---

## 11. 미확정 결정 — 시뮬레이터 관점 권장값

승인된 값이 아니라 **권장**이다. 팀이 ADR로 정할 때까지 구현은 이 값을 Scenario 입력의 예시로만 쓴다.

| ID | 권장 | 이유 | 다른 값을 고르면 |
| --- | --- | --- | --- |
| **D02** 평가 단위 | `single_bus_leg` | 수요 벡터가 access·egress 한 구간 단위이고, C도 `single_bus_leg`만 받는다. `full_journey`는 M00이 "환승 연결과 중복 집계 검증 뒤"로 조건을 달았는데 그 검증이 없다. 엔진도 환승이 없다(§4.4) | `full_journey`는 §4.4 확장(승객 leg 목록)과 지하철 구간 시간 입력이 필요하다. 두 scope의 충족률을 한 그래프에 섞지 않는다 |
| **D03** 가용 차량·초기 위치 | ① 차량 수 = 지자체가 실제 투입한 대수(세 안 공통 상한) ② 모두 같은 차급 ③ 초기 위치 = 배정 노선의 anchor(`initial_stop_id=null`), 공차 출고 0 ④ 종료 = anchor 복귀 ⑤ 가용 시각 = 운영시간과 같게 | 전세버스 차고지 위치와 차고지–기점 구간 시간이 자료에 없다. 지어내면 M00 §2.2 위반이다. 세 안에 같은 가정이라 비교는 공정하다. C의 첫 버전 제약(동일 조건 차량, 동일 거점 복귀)과도 맞는다 | 차고지를 넣으려면 운행안이 출고·입고 정류장열을 주고 `travel_times.json`이 그 구간을 포함해야 한다(§1.4 확장) |
| **D06** 환승·도보·정상 수단 | 환승 0회, 도보 0(자기 정류장에서만 타고, 역에 딸린 정류장끼리는 0초), 정상 수단(지하철·비파업 버스)은 시뮬레이터 밖 | 승객이 "역 정류장에 내리면 성공"이라 지하철 구간은 필요 없다. 비파업 버스는 §5대로 3단계 잔여 수요에서 처리한다. 도보를 넣으면 정류장 선택 모형이 필요해 결정론·설명 가능성 부담이 커진다 | 도보 허용은 실현기에서 `origin_stop_ids`를 넓히는 것으로 일부 표현할 수 있다(도보 시간은 여전히 0) |
| **D07** 최대 대기·총 이동시간 | `max_wait_s = 1200`(20분), `max_journey_s = 3600`(60분). 민감도는 대기 600·900·1800으로 | 대기 한도는 대기열 동작을 바꾸므로 **실행 전에 고정**해야 하고, 배차간격(대수가 적은 노선은 10–20분)보다 짧으면 한 대만 놓쳐도 전부 포기가 된다. 총 이동 한도는 **사후 판정**이라 같은 로그로 값을 바꿔 다시 계산할 수 있으니 넉넉히 잡고 분포를 같이 보고한다. 값 자체는 기존 Draft PR이 제안한 가정과 같다 | 대기 한도를 바꾸면 `arrivals.jsonl`을 다시 만들어야 한다(새 `input_bundle_id`). `single_bus_leg`에서 "누적 대기"는 정류장 한 곳의 대기다 |

함께 정해야 하는 것(D 번호는 없지만 결과에 영향)

- `W_e`: 수요 창은 06–10시다. `W_e=10:00`이면 09:40 이후 승객은 대부분 `left_waiting`이 된다. **권장 `W_e = 10:30`**(차량 가용도 같이). 세 안에 같은 값이어야 한다.
- 정차·회차 값(가정 A5·A6): 권장 `dwell_base_s=10`, `dwell_per_boarding_s=3`, `dwell_per_alighting_s=2`, `turnaround_s=180`, `cycle_rest_s=0`. 근거 자료가 없는 추정이며 현장값으로 바꿔야 한다. 운수종사자 휴게시간 규정 적용 여부는 확인이 필요하다.
- 차량 정원: 전세버스라면 좌석 45·입석 0, 시내버스형이라면 좌석+입석. 지자체 실제 투입 차종 확인 필요.

---

## 가정 목록

방향: **좋게** = 실제보다 결과(충족률·대기)가 좋게 나오는 쪽, **나쁘게** = 나쁘게 나오는 쪽, **중립** = 방향이 정해지지 않음. 세 운행안에 똑같이 적용되므로 순위보다 절대 수준에 영향을 준다(순위에 영향을 줄 수 있는 것은 따로 표시).

| # | 가정 | 방향 | 비고 |
| --- | --- | --- | --- |
| A1 | 차량이 `W_s`에 이미 노선 기점에 있다(출고 공차 0, D03) | 좋게 | 차고지가 먼 노선일수록 유리해진다 → **순위 영향 가능** |
| A2 | 모든 차량 이동이 `W_e` 안에 끝나야 한다(마지막 cycle 통째로 제외) | 나쁘게 | `W_e`를 수요 종료보다 늦추면 완화 |
| A3 | cycle 편성·시작 때 만차 1회분 승하차 여유(`allow`)를 뺀다 | 나쁘게(약함) | 마지막 cycle 하나가 빠질 수 있다 |
| A4 | 정차 중 도착한 승객은 그 차를 못 탄다(도착 스냅숏) | 나쁘게(약함) | 기점에서 대기 중인 차에 늦게 온 승객도 못 탄다 |
| A5 | 정차 = 기본 + 승차·하차 1인당 합(문 하나, 동시 승하차 없음), 값은 추정 | 나쁘게 | 값이 클수록 나빠진다. 현장값 필요 |
| A6 | 회차 `turnaround_s`만 두고 운전자 휴게(`cycle_rest_s`)는 0 | 좋게 | 휴게 규정을 적용하면 순환시간이 늘어난다 |
| A7 | 구간 시간은 평시 TPSS·버스 GTFS 값이고 계획 = 실현(변동 없음) | 좋게 | 파업일 도로 혼잡, 일별 변동, 전세버스의 낯선 노선 주행을 반영하지 않는다 |
| A8 | 구간 시간은 출발 시각의 시간대 값을 구간 끝까지 쓴다 | 중립 | 시간대 경계 근처에서만 차이 |
| A9 | 승객은 시간대 안에서 고르게 도착한다(체계적 반올림) | 좋게(약함) | 실제의 몰림·열차 도착에 따른 egress 뭉침이 없다 |
| A10 | 수요의 시간대 = 정류장 도착 시각 | 중립 | 실제 자료는 승차 태그 기준이라 진짜 도착은 더 이르다. egress는 §2.3의 확인 사항 |
| A11 | 수요는 고정이다(셔틀이 좋아져도 늘지 않고, 나빠도 다른 수단으로 가지 않는다) | 중립 | 요구사항의 전제. `abandon`은 평가 절단 |
| A12 | 비파업 노선의 흡수는 3단계에서 끝났고 셔틀과 상호작용하지 않는다 | 중립 | 셔틀 만차 승객이 마을버스로 가는 경우 누락(나쁘게), 마을버스가 이미 꽉 찬 경우 누락(좋게) |
| A13 | 승객은 노선·정류장을 알고, 목적지로 가는 첫 차를 탄다 | 좋게 | 안내 부족·오탑승이 없다. 다만 더 빠른 다음 차를 기다리지 않는 점은 약하게 나쁘게 |
| A14 | 역에 딸린 정류장끼리 이동은 0초, 역 정류장에 내리면 성공 | 좋게 | 역 출입구까지 도보·승강장 이동 제외 |
| A15 | 환승 없음, 도보로 다른 정류장에 가지 않음 | 나쁘게 | 한 번 갈아타면 갈 수 있는 승객이 `no_service`가 된다 → 환승 전제로 짠 안에 불리, **순위 영향 가능** |
| A16 | 대기 한도를 넘기면 줄에서 빠진다(`max_wait_s`) | 중립 | 한도가 짧으면 충족률은 내려가고 남은 승객의 대기는 줄어든다 |
| A17 | 입석 정원까지 다 태운다. 좌석 선호·혼잡 기피 없음 | 좋게 | |
| A18 | 정류장에 동시에 설 수 있는 차량 수 제한 없음, 추월 자유 | 좋게 | 역 앞 정류장 혼잡 미반영 |
| A19 | 차량 고장·결행·운전자 교대 없음 | 좋게 | |
| A20 | 시간표는 고정 주기·균등 오프셋이다. 수요가 몰리는 시간에 차를 더 넣지 않는다 | 나쁘게 | 시간표를 직접 주는 안은 이 제약이 없다 → 비교 실험은 세 안 모두 생성기 사용 권장(§1.6) |
| A21 | 차급이 섞였을 때 명시 배정이 없으면 ID 순으로 노선에 배정 | 중립 | v1 권장은 동일 차급(D03) |
| A22 | 같은 초에 같은 정류장에 선 차량은 `vehicle_id` 순으로 태운다 | 중립 | 결정론을 위한 임의 규칙 |
| A23 | 순환형에서 승객은 기점(anchor)을 넘어 계속 타지 못한다 | 나쁘게 | anchor를 주요 역에 두면 영향이 없다 |
| A24 | 속도 목표(전체 1회 2초)는 추정이다 | — | 2단계에서 실측 |
