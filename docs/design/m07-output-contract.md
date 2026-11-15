# M05 → M07 출력 계약 (검증기 코드에서 거꾸로 정리)

- 상태: **초안 · giho999 확인 필요**(특히 §6의 "추론" 항목). 팀 공식 M07 검증기(`src/busstrike/evaluation/`, main 버전)가 읽는 형식과 의미를 정리했다.
- 근거 우선순위: ① 검증기 코드(`src/busstrike/evaluation/*.py`) ② 검증기 fixture(`tests/evaluation/fixtures/`) ③ 검증기 테스트 ④ 참고: 검증기가 인용하는 명세 `docs/verifier_spec.md`. 이 파일은 main에 없고 PR #6 브랜치의 커밋 `ca643f6`에만 있다. 예전 엔진 코드는 형식을 이해하는 데에만 읽었고 가져오지 않았다.
- 근거 표기는 `파일:줄`이다. `evaluation/`는 `src/busstrike/evaluation/`, `fixtures/`는 `tests/evaluation/fixtures/`, `spec`은 `docs/verifier_spec.md@ca643f6`을 줄인 것이다.
- 코드로 확정되지 않아 추론한 항목은 본문에 **추론**으로 표시하고 §6에 모았다.
- 엔진 쪽 구현은 [simulator.md](simulator.md) §6–§8에 있고, 새 엔진과 이 계약의 의미 차이는 §7에 있다.

## 1. 폴더 구조

```text
<out>/
  scenario/  network.json · demand_bins.csv · scenario_manifest.json
  run/       events.jsonl · passenger_outcomes.csv · metrics.json · measurement.json · realization.jsonl
             (+ 검증기가 읽지 않는 보조 파일: timetable.json)
```

| 규칙 | 근거 |
| --- | --- |
| scenario 필수 파일은 3개다. 하나라도 없으면 `InputError`(종료 코드 2) | `evaluation/inputs.py:15`, `:131-134` |
| run 필수 파일은 4개다. `realization.jsonl`은 선택이다(없으면 V1이 `demand_bins.csv`와 seed로만 비교하고 checksum 대조를 건너뛴다) | `evaluation/inputs.py:16-17`, `:187-193`, `:286-288`; `evaluation/arrivals_ref.py:175-177` |
| scenario 위치를 찾는 순서: `--scenario` → `<run>/../scenario` → `measurement.json`의 `scenario`(상대 경로는 run 디렉터리 기준) | `evaluation/inputs.py:277-305` |
| **모르는 파일은 무시한다.** 검증기는 이름으로 정해진 파일만 열고, 보고서 digest도 `RUN_FILES`·`SCENARIO_FILES`만 계산한다. 그래서 `timetable.json`을 run/에 두어도 된다 | `evaluation/inputs.py:174-175`, `:269-272`; `evaluation/guards.py:11-14` |
| 검증기는 기본적으로 `<run>/verifier_report.json`을 **쓴다**. 결정론 비교는 검증 전에 하거나 `--out`으로 다른 곳에 쓴다 | `evaluation/verify.py:86-87` |
| 종료 코드: 0 통과 · 1 검사 실패 · 2 입력 오류 | `evaluation/verify.py:79-94`; `evaluation/README.md:21` |

## 2. 파일별 형식

공통: 텍스트는 UTF-8이다(`evaluation/inputs.py:167`, `:208`, `:235`, `:256`). 시각은 정수 초, 인원은 정수다. JSON에서 정수 자리에 bool·실수·문자열이 오면 검사 위반이 아니라 입력 오류다(`evaluation/inputs.py:104-108`; `evaluation/README.md:115-116`). 아래 "형식" 열은 검증기가 실제로 읽는 타입이다.

### 2.1 `scenario/network.json`

| 필드 | 형식 | 의미 | 근거 |
| --- | --- | --- | --- |
| `trips[]` | 목록 | 운행편. 순서는 의미 없음(**추론**: 검증기는 dict로 색인한다) | `evaluation/inputs.py:144-161` |
| `trips[].trip_id` | 문자열, 유일 | 방문 stable_id의 앞부분. `:`가 들어가도 된다(뒤 8자리만 끊는다) | `evaluation/inputs.py:154-155`; `evaluation/invariants.py:32-37` |
| `trips[].vehicle_id` | 문자열 | 차량. 탑승 인원은 차량 단위로 센다 | `evaluation/recompute.py:131`, `:155` |
| `trips[].capacity` | int | 이 운행편의 정원 | `evaluation/inputs.py:151`; `evaluation/recompute.py:155` |
| `trips[].stops[]`, `arrivals[]`, `departures[]` | 같은 길이의 목록, 시각은 int | i번째 방문의 정류장·도착·출발. 승차와 하차는 **도착 시각**에 일어난다. 출발은 승객 상태를 바꾸지 않는다 | `evaluation/inputs.py:146-161`; `evaluation/recompute.py:83-86`, `:177-181` |
| `trips[].return_s` | int | 운행편이 끝나고 차량이 출발 거점에 돌아온 시각. M6 "완료"·운행시간·회송시간과 V16 duty 비중첩에 쓴다. 복귀 공차를 포함한다(**추론**: 검증기 코드는 의미를 적지 않았다. 예전 엔진 `sim/models.py` docstring과 F1 fixture 값 10·20으로 추정) | `evaluation/metrics_ref.py:278-284`; `evaluation/guards.py:159-163`; `fixtures/f1/scenario/network.json` |
| `trips[].distance_m` | int | 완료 운행편의 거리. 복귀 공차를 포함한다(**추론**, 위와 같은 근거) | `evaluation/metrics_ref.py:282` |
| `route_of` | `{trip_id: route_id}` | R12 배차간격을 셀 노선. 없거나 비면 V15는 `not_verified` | `evaluation/inputs.py:138`; `evaluation/guards.py:33-36`, `:65-68` |
| `stop_area` | `{stop_id: area}` | M5 지역. 매핑이 없는 정류장은 `unknown` | `evaluation/inputs.py:137`; `evaluation/metrics_ref.py:11`, `:239` |
| `spec.capacity` | int (선택) | R11 기준 정원. 있으면 모든 운행편 정원이 이 값과 같아야 한다 | `evaluation/guards.py:117-134` |
| `spec.service_start_s`, `spec.service_end_s` | int (선택) | R14 운영창. 있으면 `arrivals[0] ≥ start`, `return_s ≤ end` | `evaluation/guards.py:136-146` |
| `routes`, `stops`, `seed`, `spec`의 나머지 키 | 아무 값 | 읽기는 하지만 어떤 검사에도 쓰지 않는다(기록용) | `evaluation/inputs.py:139-140` |

### 2.2 `scenario/demand_bins.csv`

머리글 `aggregate_id,origin,destination,bin_start_s,bin_width_s,passenger_count`(csv 모듈 `DictReader`, UTF-8). 한 행은 집계 수요 하나다. 정수 칸은 `int()`로 읽는다. 행 순서는 의미가 없다. 생성기가 `aggregate_id`로 정렬하기 때문이다. 근거: `evaluation/inputs.py:167-171`, `evaluation/arrivals_ref.py:62`.

| 열 | 의미 | 근거 |
| --- | --- | --- |
| `aggregate_id` | 행 ID, 유일해야 한다(**추론**: 겹치면 passenger_id가 겹쳐 V1이 실패한다) | `evaluation/arrivals_ref.py:110-111` |
| `origin`, `destination` | 승객 **한 명**의 출발 정류장 1개·목적 정류장 1개. 같으면 V1 위반 | `evaluation/arrivals_ref.py:124-125`; `evaluation/recompute.py:150-153` |
| `bin_start_s`, `bin_width_s` | 발생 구간 `[start, start+width)`. 폭이 1보다 작으면 V1 위반 | `evaluation/arrivals_ref.py:42-43`, `:119-120` |
| `passenger_count` | 그 구간에서 발생하는 인원(정수 ≥ 0) | `evaluation/arrivals_ref.py:63`, `:121-123` |

### 2.3 `scenario/scenario_manifest.json`

| 필드 | 의미 | 근거 |
| --- | --- | --- |
| `sha256` | `{파일명: sha256 hex}`. 적힌 파일마다 실제 해시와 같아야 한다. 없으면 V17 위반 | `evaluation/guards.py:175-188` |
| 그 밖(`demand_spec`, `rows`, `passengers` …) | 기록용. V16이 `demand_spec`을 읽지만 쓰지 않는다 | `evaluation/guards.py:112` |

### 2.4 `run/events.jsonl`

한 줄에 사건 하나가 들어가는 JSON 객체다. 빈 줄은 건너뛴다. 필수 키는 `time_s`·`phase`·`stable_id`·`kind`이고, 선택 키는 `passenger_id`·`vehicle_id`·`occupancy`(없으면 null)다. 근거: `evaluation/inputs.py:207-227`.

| kind | phase | stable_id | passenger_id | vehicle_id | occupancy | 근거 |
| --- | --- | --- | --- | --- | --- | --- |
| `alighted` | 0 | 방문 `<trip_id>:<index 8자리>` | 승객 | 차량 | 하차 직후 차내 인원 | `evaluation/invariants.py:14`, `:96-99`; spec:39-41 |
| `vehicle_arrived` | 0 | 방문 | null | 차량 | 하차를 반영한 차내 인원 | 같음 |
| `generated` | 1 | `generate:<pid>` | 승객 | null | null | `evaluation/invariants.py:100-103` |
| `boarded` | 2 | 방문 | 승객 | 차량 | 승차 직후 차내 인원 | `evaluation/invariants.py:14`; `evaluation/invariants.py:307-316` |
| `departed` | 3 | 방문 | null | 차량 | 차내 인원 | `evaluation/invariants.py:14`; `evaluation/recompute.py:177-181` |
| `expired` | 4 | `wait:<pid>` 또는 `journey:<pid>` | 승객 | 차내 만료면 차량, 아니면 null | 차내 만료면 그 차량의 현재 인원, 아니면 null | `evaluation/invariants.py:104-108`, `:489-490`; `evaluation/recompute.py:191-192`; `fixtures/f1-j14/run/events.jsonl` |

순서와 개수:

- 줄 순서는 키 `(time_s, phase, stable_id)`의 **약한 증가**다. 같은 키에 사건이 여럿 있어도 되는 것은 phase 0(하차 여럿 + 도착 1건, 하차가 먼저)과 phase 2(승차 여럿)뿐이다. phase 1·3·4의 키는 유일해야 한다. 근거: `evaluation/invariants.py:50-119`, `evaluation/README.md:109-114`. 명세의 "엄격 증가"(spec:51)와 다르다. 검증기 구현 쪽을 따른다.
- `time_s ≤ drain_s`인 사건만 쓴다(`evaluation/invariants.py:85-87`).
- 방문마다 `arrivals[i] ≤ drain_s`이면 `vehicle_arrived`를 정확히 1건, `departures[i] ≤ drain_s`이면 `departed`를 정확히 1건 쓴다. 시각은 일정과 같아야 한다(`evaluation/invariants.py:124-153`). 하차·승차가 없는 방문도 포함한다.
- 승객마다 `generated`는 정확히 1건이다(`evaluation/invariants.py:202-205`). **drain_s 뒤에 발생한 승객은 `generated`를 쓸 수 없어서 V4가 실패한다.** 그래서 엔진은 `drain_s ≥ 마지막 발생 시각`이어야 한다(**추론**: 명세 §2와 V4를 같이 읽은 결과).
- `boarded`는 승객당 최대 1건, `alighted`도 최대 1건이다. `expired`는 승객당 최대 1건이고, 결과를 확정한 만료만 쓴다(`evaluation/invariants.py:222-251`, `:478-483`).
- 한 방문 안의 `boarded` 순서는 `(승객 time_s, passenger_id)` FIFO다(`evaluation/invariants.py:454-456`). 한 방문 안의 `alighted` 순서는 검증기가 정하지 않았다. 재계산은 승차 순서로 내린다(`evaluation/recompute.py:21-23`, `:133`).

### 2.5 `run/passenger_outcomes.csv`

머리글은 `passenger_id,origin,destination,time_s,outcome,reason,physical,vehicle_id,boarding_s,arrival_s,refused`이고, 빈 칸은 null이다(`evaluation/inputs.py:111-122`, `:230-248`). passenger_id가 겹치면 입력 오류다. 행 순서는 의미가 없다(**추론**: dict로 읽는다).

| 열 | 값 | 근거 |
| --- | --- | --- |
| `passenger_id` | `json.dumps([aggregate_id, index], separators=(',', ':'))` | `evaluation/arrivals_ref.py:26-28` |
| `origin`, `destination`, `time_s` | 발생과 같다 | `evaluation/arrivals_ref.py:189-192` |
| `outcome` | `served` · `unserved` · `censored` | `evaluation/invariants.py:333-350`, `:377-379` |
| `reason` | unserved면 M2 사유(§4.6), served·censored면 빈 칸 | `evaluation/invariants.py:390-393`; `evaluation/metrics_ref.py:148-152` |
| `physical` | `waiting` · `onboard` · `arrived`. 결과와 따로 둔 물리 상태다 | `evaluation/invariants.py:343-350`, `:380-382` |
| `vehicle_id` | 검증기는 비교하지 않는다. fixture는 `physical=onboard`일 때만 차량을 적는다(**추론**) | `fixtures/f1-drain12/run/passenger_outcomes.csv` |
| `boarding_s`, `arrival_s` | 승차·하차 사건 시각, 없으면 빈 칸. 여정 기한을 넘긴 뒤의 하차도 `arrival_s`에 남는다 | `evaluation/invariants.py:383-389` |
| `refused` | 만차로 남겨진 횟수(§4.3) | `evaluation/metrics_ref.py:175-179` |

### 2.6 `run/metrics.json`

검증기는 아래 경로만 `==`로 비교하고 나머지 키는 무시한다(`evaluation/metrics_ref.py:15-34`). 그래서 실수 값도 검증기와 **같은 식**으로 계산해야 한다(예: `served / total`, `sum(waits) / len(waits)`).

| 경로 | 값 | 근거 |
| --- | --- | --- |
| `M1.served`·`unserved`·`censored`·`total` | 결과별 인원 | `evaluation/invariants.py:402-409`; `evaluation/metrics_ref.py:64-65` |
| `M1.rate` | `censored == 0`이면 `served/total`, 아니면 null, `total == 0`이면 null | `evaluation/metrics_ref.py:39-52` |
| `M1.lower`·`upper` | 선택. 있으면 `served/total`·`(served+censored)/total`(censored=0이면 rate와 같다) | `evaluation/metrics_ref.py:66-73` |
| `M2.unserved_by_reason` | 네 사유 `no_connection`·`service_end`·`journey`·`wait`를 0까지 모두 싣는다 | `evaluation/metrics_ref.py:12`, `:130-135`, `:153-154` |
| `M2.refusal_events` | 만차 거절 연인원 | `evaluation/metrics_ref.py:171` |
| `M2.passengers_refused_by_outcome` | `{served, unserved, censored}`별로 거절을 한 번 이상 겪은 승객 수 | `evaluation/metrics_ref.py:160-165`, `:180-181` |
| `M3.served`·`M3.boarded` | 각각 `{n, mean_s, p90_s, over_threshold_n, over_threshold_rate, threshold_s}`, 표본 0이면 mean·p90·rate는 null. `measurement.policy.wait_threshold_s`가 없으면 V12는 `not_verified`이고 비교하지 않는다 | `evaluation/metrics_ref.py:187-230` |
| `M3.not_boarded_unserved_by_reason` | 타지 못한 unserved의 사유별 수(네 사유) | `evaluation/metrics_ref.py:223-229` |
| `M5.by_origin_area` | `{area: {total, served, unserved, censored, served_rate}}`. 승객이 있는 지역만 싣는다 | `evaluation/metrics_ref.py:235-251` |
| `M5.min_served_rate` | `{area, rate}` = `min((rate, area))`. 지역이 없으면 null | `evaluation/metrics_ref.py:247-251` |
| `M6.vehicles[]` | `network.json`에 운행편이 있는 차량마다 `{vehicle_id, trips_completed, boarded, served_alighted, service_time_s, distance_m, empty_time_s}`. 다른 차량을 넣거나 빼면 위반이다 | `evaluation/metrics_ref.py:267-331` |
| `M6.empty_distance_m` | 선택. 있으면 null이어야 한다 | `evaluation/metrics_ref.py:333-335` |
| `G.R12.max_headway_s`·`max_gap_by_route_s`·`violation_count`·`pass`·`violations[]` | §4.8 | `evaluation/guards.py:74-102` |

### 2.7 `run/measurement.json`

| 필드 | 필수 | 의미 | 근거 |
| --- | --- | --- | --- |
| `config.max_wait_s`, `config.max_journey_s`, `config.drain_s` | 필수(int) | 기한과 관측 끝 | `evaluation/inputs.py:196-202` |
| `arrival_seed` | V1·재계산에 필요 | 생성기 seed(JSON 값 그대로 해시에 들어간다) | `evaluation/verify.py:41`; `evaluation/arrivals_ref.py:100-104` |
| `policy.wait_threshold_s` | 선택 | M3 임계. 없으면 V12 `not_verified` | `evaluation/metrics_ref.py:207-211` |
| `policy.max_headway_s` | 선택 | R12 상한. 없으면 V15 `not_verified` | `evaluation/guards.py:59-64` |
| `policy.name` | 선택 | 보고서 기록용 | `evaluation/report.py:131` |
| `log_sha256`·`log_bytes` / `outcome_sha256`·`outcome_bytes` / `metrics_sha256` / `realization_sha256` | 선택(없으면 V17 note에 "무결성 미확인") | 각 파일의 sha256 hex·바이트 수 | `evaluation/guards.py:11-14`, `:190-230` |
| `realization_checksum` | `realization.jsonl`이 있으면 사실상 필수 | realization 메타 `checksum`과 같아야 한다 | `evaluation/arrivals_ref.py:171-174`; `evaluation/guards.py:212-218` |
| `scenario_manifest` | 선택 | 있으면 그 `sha256`이 `scenario_manifest.json`의 것과 같아야 한다 | `evaluation/guards.py:220-225` |
| `scenario` | 선택 | scenario 디렉터리 경로(run 기준 상대) | `evaluation/inputs.py:294-301` |
| `event_count`, `label`, `env{commit, branch, dirty, …}` | 선택 | 보고서가 **인용**만 한다. 검증하지 않는다 | `evaluation/report.py:52-62`, `:125-133` |

### 2.8 `run/realization.jsonl`(선택)

첫 줄은 메타 `{generator, seed, source_checksum, checksum, passenger_count, rows[]}`이고, 다음 줄부터 승객마다 `[passenger_id, origin, destination, time_s]`가 한 줄씩 온다. 승객 순서는 `aggregate_id` 정렬 후 index 순이다. `rows[]`는 `demand_bins.csv`의 행과 같은 집합이어야 한다(순서 무관). 근거: `evaluation/inputs.py:251-267`, `evaluation/arrivals_ref.py:131-170`.

### 2.9 체크섬(V17)

`scenario_manifest.sha256`의 파일 해시, `measurement.json`의 산출물 해시·크기, realization 메타 checksum과 `measurement.realization_checksum`, `measurement.scenario_manifest.sha256`과 `scenario_manifest.sha256`이 각각 서로 같아야 한다. 근거: `evaluation/guards.py:173-231`. 파일 바이트를 어떻게 직렬화할지(구분자, 키 순서, 마지막 줄바꿈)는 검증기가 보지 않는다. 기록한 해시와 맞기만 하면 된다.

## 3. 판정

| 상태 | 뜻 | 근거 |
| --- | --- | --- |
| `pass` / `fail` | 위반 0 / 1건 이상 | `evaluation/checks.py:41-45` |
| `not_run` | 승객 수가 재계산 상한(기본 10,000)을 넘어 V6·V7·V9–V14를 돌리지 않음 | `evaluation/verify.py:23-58` |
| `not_verified` | 자료가 없어 판단 불가. **V16은 늘 이 상태다**(R10·R13 자료 없음) | `evaluation/guards.py:106-168` |
| 보고서 `pass` | 실패한 검사가 없다. `fully_verified`는 현재 구조에서 늘 false다 | `evaluation/report.py:109-116` |

## 4. 검증기가 재계산하는 의미

### 4.1 승객 발생 — 생성기 `uniform_bin_sha256_v1` (V1)

```text
base(seed, agg)   = json.dumps([generator, seed, agg]).encode() + b"\0"        # 기본 구분자(쉼표·콜론 뒤 공백)
offset(seed, agg, i, w):
    for c = 0, 1, 2, …:
        x = 앞 8바이트(SHA-256(base ‖ i.to_bytes(8,'big') ‖ c.to_bytes(4,'big'))) 를 big-endian 정수로
        if x < 2^64 − (2^64 mod w): return x mod w
time_s = bin_start_s + offset;  passenger_id = json.dumps([agg, i], separators=(',',':'))
순서 = aggregate_id 오름차순, 그 안에서 i = 0 … passenger_count−1
source_checksum = SHA-256(json.dumps([행 dict …], sort_keys=True, separators=(',',':')))   # aggregate_id 정렬
checksum        = SHA-256(json.dumps([generator, seed, source_checksum], separators=(',',':'))
                          + Σ "\n" + json.dumps([pid, origin, destination, time_s], separators=(',',':')))
```

근거: `evaluation/arrivals_ref.py:16-85`, spec:50. 명세 문장으로는 base를 digest로 읽을 수도(A) 있고 해시 입력의 앞 바이트열로 읽을 수도(B) 있다. 검증기는 (B)로 구현했다(`evaluation/arrivals_ref.py:7-11`, `evaluation/README.md:122-125`). 엔진도 (B)를 따른다.

### 4.2 사건 키와 처리 순서

승객마다 `(t,1,generate)`·`(t+W,4,wait)`·`(t+J,4,journey)`, 방문마다 `(arrival,0,alight)`·`(arrival,2,board)`·`(departure,3,depart)` 키를 만든다. `drain_s` 이하만 남겨 `(time_s, phase, stable_id)` 순서로 처리한다. 근거: `evaluation/recompute.py:75-89`, spec:36-37·72-73. 여기서 다음이 나온다.

- 같은 초에 발생한 승객은 그 초에 도착한 차를 탈 수 있다(phase 1 < 2).
- 같은 초에 같은 정류장에 선 두 운행편은 **stable_id(= trip_id) 문자열 순서**로 태운다.
- 같은 초의 하차는 모든 차량의 승차보다 먼저다(phase 0).
- 기한은 inclusive다. `t+W` 시각의 승차는 성공하고(phase 2 < 4), `t+J` 시각의 하차는 served다(phase 0 < 4). 근거: spec:42-43.

### 4.3 승차 자격 · FIFO · 비유휴 · 정원 · 만차 거절 (V6·V11)

- 방문 (trip, i, stop)의 자격 승객은 다음을 모두 만족하는 승객이다: `origin == stop`, 이미 발생함, 아직 타지 않음, 결과 미확정(만료 전), `destination`이 **같은 trip의 i 뒤 정류장 중 하나**. 근거: `evaluation/recompute.py:145-153`.
- 자격 승객을 `(time_s, passenger_id)`로 정렬하고 앞에서부터 `capacity − 차량 현재 인원`명을 태운다. 빈 좌석이 남았는데 자격 승객을 남기면 V6 위반이다. 근거: `evaluation/recompute.py:154-167`, `evaluation/invariants.py:432-465`.
- 정원은 운행편의 `capacity`, 현재 인원은 **차량** 단위로 센다. 근거: `evaluation/recompute.py:155`.
- 만차 거절: 승차 처리 후 빈 좌석이 0인 방문(도착할 때 이미 만석이었던 경우 포함)에서 남은 자격 승객 1명당 1건이다. 승객별 `refused`는 그 횟수다. 근거: `evaluation/recompute.py:168-175`, spec:60.

### 4.4 하차

방문 도착 시각에 그 차량 승객 중 `destination == stop`인 사람이 모두 내린다. 기한 안이면 served로 확정되고, 이미 unserved(journey)면 결과는 그대로 두고 `physical=arrived`·`arrival_s`만 남긴다. 근거: `evaluation/recompute.py:128-143`.

### 4.5 기한 · 결과 (V7·V8)

- `wait` 만료는 아직 타지 않은 미확정 승객에게만 적용되고, `journey` 만료는 미확정 승객 모두에게 적용된다(대기 중이든 차내이든). 이미 확정된 승객에게는 사건을 남기지 않는다. 같은 시각이면 `journey:`가 먼저다(문자열 순서). 근거: `evaluation/recompute.py:183-192`, spec:56.
- 결과: `served` ⇔ 만료 전 목적지 하차, `unserved` ⇔ drain 안 만료 사건, `censored` ⇔ 둘 다 아님. 근거: `evaluation/invariants.py:333-350`, `evaluation/recompute.py:194-196`.
- 여정 기한 `max_journey_s`는 **결과를 정하는 기한**이다. 탄 뒤 `t+J` 전에 내리지 못하면 unserved(journey)다.

### 4.6 M2 미충족 사유 (V10)

unserved 승객마다 배타적 우선순위로 정한다. 근거: `evaluation/metrics_ref.py:79-127`, spec:59.

1. `no_connection`: 어떤 운행편에도 출발지 방문 i와 목적지 방문 j > i가 함께 있지 않다(시각과 무관한 정적 판정).
2. `service_end`: 그런 운행편들의 출발지 **도착** 시각 중 `[time_s, drain_s]` 안에 드는 것이 없다.
3. 그 밖: 결과를 확정한 만료 종류(`journey`·`wait`).

### 4.7 지표

- M3: 대기 = `boarding_s − time_s`, 모집단은 served와 boarded(나중에 unserved·censored가 된 탑승자 포함) 두 가지다. P90은 nearest-rank `sorted[max(1, ceil(0.9n)) − 1]`이고, 임계 초과는 `wait > threshold`다. 근거: `evaluation/metrics_ref.py:187-230`.
- M5: 지역 = `stop_area[origin]`(없으면 `unknown`), `served_rate = served/total`. 근거: `evaluation/metrics_ref.py:235-251`.
- M6: 차량별로 운행편을 모은다. `return_s ≤ drain_s`이면 완료이고, 완료 운행편마다 `trips_completed += 1`, `service_time_s += return_s − arrivals[0]`, `distance_m += distance_m`, `empty_time_s += return_s − departures[-1]`이다. 완료 여부와 관계없이 탑승 0으로 출발했고(출발이 drain 안) 다음 도착이 drain 안인 구간마다 `empty_time_s += arrivals[i+1] − departures[i]`이다. `boarded`는 그 차량에 탄 승객 수, `served_alighted`는 그중 served인 승객 수다. 근거: `evaluation/metrics_ref.py:267-300`, `evaluation/recompute.py:177-181`.

### 4.8 R12 배차간격 (V15)

`route_of`의 노선·정류장별로 모든 방문의 `departures`를 정렬해 연속 간격을 센다. `max_gap_by_route_s[route]`는 그 노선의 최대 간격이다. 연속 출발이 한 쌍도 없는 노선은 생략하거나, 싣더라도 비교하지 않는다. `violation_count`는 `간격 > max_headway_s`인 쌍의 수이고, `pass`는 `violation_count == 0`(bool), `violations[]` 길이는 `violation_count`와 같다. 근거: `evaluation/guards.py:25-103`.

### 4.9 운행 제약 (V16, 늘 `not_verified`)

`spec.capacity`가 있으면 정원이 같은지, `spec.service_*_s`가 있으면 운영창 안인지 본다. 차량별로 운행편을 첫 도착 순으로 늘어놓았을 때 **모든 운행편의 첫 정류장이 같은 출발 거점**이어야 하고, `다음 운행편 첫 도착 ≥ 앞 운행편 return_s`여야 한다. 위반이 있으면 fail, 없으면 `not_verified`다. 근거: `evaluation/guards.py:108-168`, spec:65.

## 5. 엔진이 따르는 결정(검증기가 보지 않는 부분)

| 항목 | 엔진의 선택 | 이유 |
| --- | --- | --- |
| 직렬화 | `events.jsonl`은 키 순서 `time_s, phase, stable_id, kind, passenger_id, vehicle_id, occupancy`, `separators=(",", ":")`, `ensure_ascii=False`, 줄 끝 `\n`. JSON 문서는 `sort_keys=True` + 마지막 `\n`. CSV는 csv 모듈 기본(최소 따옴표) + `\n` | 결정론. 검증기는 해시만 대조한다 |
| 실행 시각·호스트·절대 경로 | 어떤 파일에도 넣지 않는다. `measurement.env`에는 엔진 커밋과 `dirty`(추적 파일 변경 여부)만 둔다 | 같은 입력·같은 체크아웃이면 바이트가 같아야 한다(simulator.md §0.2). 보고서는 엔진 커밋을 인용한다(`evaluation/report.py:52-62`). 검증기 테스트도 fixture에 40자리 커밋이 있다고 본다(`tests/evaluation/test_verify_cli.py:62-64`) |
| 하차 순서 | 한 방문에서 승차 순서대로 | 검증기 재계산과 같게(§2.4) |
| `outcomes` 행 순서 | `passenger_id` 문자열 순 | 결정론 |
| 연속 출발이 없는 노선의 R12 최대 간격 | 생략 | 검증기가 생략을 허용한다(§4.8) |

## 6. 추론 항목 (giho999 확인 요청)

| # | 항목 | 추론한 내용 | 근거·이유 |
| --- | --- | --- | --- |
| I1 | `return_s`의 뜻 | 마지막 정류장 뒤 복귀 공차가 끝나 출발 거점에 돌아온 시각. 순환형처럼 복귀 공차가 없으면 마지막 도착 시각 | 검증기 코드는 쓰기만 하고 정의하지 않는다. 예전 엔진 docstring, F1 값 |
| I2 | `distance_m`의 뜻 | 운행편 영업 구간 + 복귀 공차 거리 | I1과 같음 |
| I3 | 마지막 방문의 `departures[-1]` | 마지막 도착 시각과 같게 쓴다. 따라서 회차 시간은 M6 `empty_time_s`(회송)에 포함된다 | fixture F1·F2가 모두 마지막 출발 = 마지막 도착. 검증기는 값을 정하지 않는다 |
| I4 | CSV `vehicle_id` | `physical=onboard`일 때만 그 차량, 아니면 빈 칸 | 검증기가 비교하지 않음. fixture `f1-drain12` |
| I5 | drain_s와 발생 시각 | `drain_s ≥ 마지막 발생 시각`이어야 한다. 엔진 기본값은 `W_e + max_journey_s` | drain 뒤 발생 승객은 `generated`를 쓸 수 없어 V4가 실패한다(§2.4) |
| I6 | 운행편 하나 = 출발 거점에서 출발해 거점으로 돌아오는 영업 구간 하나 | 영업 leg가 두 개 이상인 노선(두 역 연결형)은 V16 "같은 출발 거점" 때문에 이 형식으로 쓸 수 없다. 엔진은 `OUT_OF_SCOPE`로 막는다(사용자 결정, §7 D2) | `evaluation/guards.py:153-158`, spec:65 |
| I7 | `spec.capacity` | 시나리오 차량 정원이 모두 같을 때만 쓴다. 섞이면 생략한다(R11 미검증) | 검증기는 값 하나와만 비교한다(`evaluation/guards.py:127-134`) |
| I8 | 생성기 base 읽기 (B) | 엔진도 (B)로 구현한다 | 검증기 README가 명세 보완을 요청한 상태다(`evaluation/README.md:122-125`) |
| I9 | 승객 OD | 정류장 1개 → 정류장 1개. 정류장 집합 OD는 표현하지 못한다(사용자 결정으로 단일 정류장만 받음, §7 D1) | §2.2, `evaluation/recompute.py:150-153` |

## 7. 새 엔진과의 의미 차이

[simulator.md](simulator.md)의 6단계 이전 설계(PR #36까지)와 이 계약을 비교했다. 기본 원칙은 **엔진을 M07에 맞춘다**이다. 맞추면 설계 원칙(결정론, 공정성, 엔진 내부 난수 금지)이 깨지는 D1·D2는 코디네이터에게 물었고, 사용자가 둘 다 A를 골랐다(2026-11-15).

| # | 항목 | 6단계 이전 새 엔진 | M07 계약 | 결정 | 설계 원칙 |
| --- | --- | --- | --- | --- | --- |
| D1 | 승객 OD | 정류장 **집합**(access 목적지·egress 출발 = 역에 딸린 정류장 전부, §2.3) | 정류장 1개 → 1개(§2.2, I9) | **사용자 결정 A**: M07 출력은 단일 정류장 OD만 받는다. 역의 어느 정류장으로 붙일지는 운행안과 무관하게 상류(M03)가 정한다. 실현기 입력은 `DemandFlow(kind, stop_id, station_stop_id, …)` | 공정성 손실: 셔틀이 역의 다른 정류장에 서는 안은 그 수요를 못 태운다. 가정 A25로 기록 |
| D2 | 노선 유형 | 피더형·순환형·**두 역 연결형**(영업 leg 2개) | 운행편은 모두 같은 출발 거점에서 시작한다(V16, I6). 영업/공차 구분 없음 | **사용자 결정 A**: M07 운행편 = 영업 leg 1개 + 뒤 공차. cycle의 영업 leg가 첫 leg 하나가 아니면 `OUT_OF_SCOPE`. 시간표 생성·GTFS는 그대로 지원 | 가정 A26 |
| D3 | 정차·회차 | 기본 + 1인당 정차, 실행 중 지연, `cancel`(LATE) | `network.json` 일정이 고정이고, 사건 시각은 일정과 같아야 한다(V3) | 엔진을 **고정 일정 실행기**로 바꿨다. 1인당 정차가 0이 아니면 `OUT_OF_SCOPE`, 지연·취소는 없다. 운행안이 준 시간표가 구간 시간·정차·회차와 맞지 않으면 `PLAN_INFEASIBLE` | 결정론 유지 |
| D4 | 승객 발생 | 체계적 반올림으로 OD별 도착 시각을 바로 만든다(`p000001-00001` ID) | 집계 행 `demand_bins.csv` + seed → 생성기 `uniform_bin_sha256_v1`(§4.1) | 2단계로 나눴다. 시간대별 **인원**은 체계적 반올림(`bin_counts`), 시간대 안 **시각**은 M07 생성기, ID는 `["행 ID",index]`. 엔진 입력은 `demand_bins.csv`다 | SHA-256 결정 함수다. 난수 상태가 없고 엔진 밖(실행 전)에서 정해진다 → 원칙 유지 |
| D5 | 대기 한도 | 승객별 `max_wait_s`(값은 정책 하나) | 전역 `config.max_wait_s` | 전역 하나로 둔다(어차피 같은 값이었다) | — |
| D6 | 여정 한도 | 사후 판정. 수송했으면 served이고, 한도를 넘으면 "충족 아님"(`JOURNEY_EXCEEDED`) | `t+J`에 미확정이면 unserved(journey). 대기 중에도 적용된다(§4.5) | M07을 따른다. 탑승 후 `t+J`를 넘기면 unserved, 하차는 `physical=arrived`로 남긴다. 충족 = M1.served | — |
| D7 | 관측 끝 | `W_e`에 종료하고 남은 승객은 `left_waiting` | `drain_s`. 그때까지 미확정이면 censored | `policy.drain_s`(선택). 기본은 `W_e + max_journey_s`로, 모든 승객이 drain 안에 기한을 맞아 censored가 0이다. `drain_s < 마지막 발생`이면 `INPUT_INVALID`(I5) | — |
| D8 | 같은 초 우선순위 | 승객 도착 → 차량은 `vehicle_id` 순, 같은 차량은 visit 순 | `(time_s, phase, stable_id)`: 모든 하차 → 발생 → 승차(trip_id 순) → 출발 → 만료 | M07을 따른다. 엔진은 세 줄의 사건 키를 `heapq.merge`로 합쳐 처리한다 | 결정론 유지(가정 A22 갱신) |
| D9 | 미충족 분류 | `NO_ROUTE_AT_STOP`·`NO_ROUTE_TO_DEST`(발생 즉시 `no_service`), `FULL`, `WAIT_EXCEEDED`, `SERVICE_ENDED`, `JOURNEY_EXCEEDED` | 사유 4개 `no_connection`·`service_end`·`journey`·`wait`(§4.6). 만차는 사유가 아니라 `refused`·`refusal_events` | M07을 따른다. 연결이 없는 승객도 줄에 섰다가 만료된다. 만차 정보는 `refused`로 남긴다 | — |
| D10 | 만차 기록 | 승객별 첫 `denied` 사건 + `arrive.left_behind` | 사건 없음. `refused`(횟수)·`M2.refusal_events` | 같은 셈법(만석 방문에 남은 자격 승객 1명당 1건)이다. 사건은 쓰지 않는다 | — |
| D11 | 사건 로그 | `seq`·`t`·`type`(arrive·board·alight·denied·depart·cancel·no_service·abandon·left_waiting), 공차 정차 포함 | §2.4 | M07 형식으로 바꿨다. 공차 leg는 방문이 아니어서 로그에 없다(`return_s`·`distance_m`에만 반영) | — |
| D12 | 지표 | `passengers.*`, `wait_s`, `in_vehicle_s`, `vehicles[]`, `trips[]`, `routes[]` … | M1·M2·M3·M5·M6·G.R12(§2.6) | M07 키로 바꿨다. 검증기가 보지 않는 보조 키는 `physical_at_drain`·`drain_s`·`label`·`scope_mode`만 둔다. 재차율·노선별 표는 뺐다(필요하면 사건 로그에서 다시 계산) | — |
| D13 | 실행 기록 | `run.json`(M00 SimulationResult + 해시) | `measurement.json` | `run.json`을 없애고 그 필드(`run_id`·`plan_id`·`input_checksum`·`config_hash` …)를 `measurement.json`에 넣었다. `env`는 커밋·`dirty`만(§5) | 결정론 유지(같은 체크아웃에서 같은 값) |
| D14 | 검증기 | 새 엔진 전용 V01–V15(`validator.py`) | M07 V1–V17 | 새 엔진 전용 검증기를 지우고 M07 검증기로 일원화했다. C `Assessment.independently_verified` = M07 `pass`(simulator.md §7.5) | — |
| D15 | M5 지역 | 없음 | `stop_area` | scenario에 선택 필드 `stop_area`를 추가했다. 없으면 `unknown` | — |
| D16 | R12·M3 임계 | 없음 | `policy.max_headway_s`·`wait_threshold_s` | scenario policy에 선택 필드로 추가했다. 없으면 V15·V12가 `not_verified`(실패는 아님) | — |

기대값이 바뀐 손계산은 simulator.md §9에 다시 적었다(T1–T5, T2b 교체).
