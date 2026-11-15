# M05 시뮬레이터 진행 기록

단계마다 아래 형식으로 덧붙인다. 설계는 [simulator.md](simulator.md), 요구사항 원문은 [simulator-brief.md](simulator-brief.md).

## 1단계 — 설계 문서

- PR: https://github.com/OpenSwProject26-2/2team/pull/28 (Draft, base `main`, 브랜치 `docs/simulator-design`)
- 한 일
  - 요구사항 원문을 `simulator-brief.md`로 그대로 복사했다(바이트 동일 확인).
  - `simulator.md`에 설계 항목 1~11, 구현 순서 5줄, 가정 목록 24개, 손계산 시나리오 T1~T7과 기대 결과를 썼다.
  - 코드는 쓰지 않았다. 공용 계약과 C의 planning 코드는 읽기만 했다.
- 설계와 달라진 점: 없음(첫 설계)
- 결정한 것(요구사항에 없던 판단. 모두 `simulator.md`에 근거와 함께 있음)
  - 시간표 위상·종료 규칙을 C의 근사 평가(`evaluator.py`)와 같게 맞췄다: 고정 주기 P, 오프셋 `floor(k·P/n)`, cycle 통째 편성(§1).
  - 요구사항의 "출발 정류장"을 `origin_stop_ids` **집합**으로 넓혔다. egress 승객이 역의 어느 쪽 정류장에서든 탈 수 있어야 운행안끼리 공정하다(§2.3).
  - 승객 발생은 체계적 반올림을 골랐고, 유량을 백만분의 1명 정수로 바꿔 정수 연산만 쓴다(§2).
  - 승차는 차량 도착 순간의 스냅숏으로만 한다. 정차 중 도착한 승객은 다음 차를 탄다(§3.3).
  - 1인당 정차로 생긴 지연이 종료 시각을 넘지 않도록 승하차 여유 `allow`와 cycle 취소(`cancel` 이벤트)를 넣었다(§1.3, §3.4).
  - 못 탄 원인에 `SERVICE_ENDED`(종료 시 대기)와 `JOURNEY_EXCEEDED`(수송했지만 총 이동 한도 초과)를 추가했다. "수송"과 "충족"을 구분한다(§4.5, §6.3).
  - 이벤트 로그는 값이 없는 키를 생략한 JSONL이고, 순서는 정렬 키로 정의한다. 지표는 엔진이 아니라 로그에서 계산한다(§6).
  - GTFS `arrival_time = departure_time`으로 내보낸다(승차 마감이 도착 스냅숏이라서, §6.4).
  - 공통 승객이 없는 노선끼리는 결과가 정확히 더해진다는 성질로 S(n)을 노선 단독 실행으로 구한다. 승객이 겹치는 노선 묶음만 함께 돌린다(§7.2).
  - 검증기는 `timetable.json`을 입력으로 받고 생성 규칙은 다시 구현하지 않는다(§8.1).
  - 비교 대상인 기존 Draft PR `feat/5-simulation-core`(SimPy)는 참고만 했고 그 코드를 쓰지 않는다. 구현은 표준 라이브러리 `heapq`다.
- 남은 문제 / 다음 단계가 알아야 할 것
  - **사용자가 확인해야 할 결정**(승인 전에는 권장값을 코드 상수로 박지 말고 Scenario 입력으로만 받는다)
    1. D02 평가 단위 `single_bus_leg`
    2. D03 차량: 대수 = 지자체 실제 투입 대수, 동일 차급, 기점 사전 배치(출고 공차 0), 차량 정원(전세버스 45석·입석 0인지)
    3. D06 환승 0·도보 0, 비파업 노선은 3단계 잔여 수요에서 처리
    4. D07 최대 대기 1200초, 최대 총 이동 3600초
    5. 운영 종료 `W_e`를 10:00으로 할지 10:30으로 할지, "모든 차량 이동이 `W_e` 안"이라는 해석
    6. 정차·회차 값(10 / 3 / 2 / 180초, 휴게 0)과 운전자 휴게 규정 적용 여부 — 근거 자료 없는 추정
    7. 정차 중 도착 승객 승차 불허(도착 스냅숏)
    8. 3안 비교에서 세 안 모두 같은 시간표 생성기를 쓰는 방식
    9. egress 수요의 시간대 의미를 A(M03)에 확인
  - 손계산 기대값은 문서 작성 중 임시 스크립트로 한 번 더 맞춰 봤다. 스크립트는 레포에 없다. 2단계에서 구현이 표와 다르면 표와 로그를 줄 단위로 비교해 어느 쪽이 설계와 다른지 먼저 가린다. 설계를 고쳐야 하면 `simulator.md`를 같은 PR에서 고친다.
  - T2·T2b·T3·T4의 로그 SHA-256은 참고값이다(T1만 로그 전체가 문서에 있다).
  - 속도 목표(전체 1회 2초)는 추정이다. 2단계에서 합성 5만 명으로 측정해 이 파일에 적는다.
  - 2단계는 이 브랜치(`origin/docs/simulator-design`)에서 `feat/sim-core`를 따고 PR base를 이 브랜치로 잡는다.
  - 테스트 실행에 `pip install -e .` 또는 `PYTHONPATH=src`가 필요하다.
  - 이 작업에 연결된 Issue가 없다. 브랜치명은 지시받은 이름을 그대로 썼다.
- 테스트 실행 명령과 결과
  - `PYTHONPATH=src python3 -m unittest discover -s tests -t .` → 기존 22개 통과(Python 3.14.4). 이 단계에서 추가한 테스트는 없다.
  - 린트·타입 검사는 저장소에 명령이 없어 실행하지 않았다.

## 2단계 — 핵심 엔진

- PR: https://github.com/OpenSwProject26-2/2team/pull/32 (Draft, base `docs/simulator-design` — 스택 PR, 브랜치 `feat/sim-core`)
- 한 일
  - `src/busstrike/sim/`에 `models.py`·`io.py`·`traveltime.py`·`timetable.py`·`arrivals.py`·`engine.py`·`eventlog.py`·`metrics.py`·`run.py`·`__main__.py`를 만들었다. 입력 스키마 검증, 시간표 생성기, 승객 발생 실현기, 이벤트 엔진, 이벤트 로그 기록, 지표 계산, 디렉터리 단위 실행·CLI까지다. 표준 라이브러리만 쓴다.
  - `tests/sim/`에 손계산 시나리오 T1–T7 전부, 결정론 테스트, 입력 오류 테스트, 속도 측정을 넣었다. 입력은 `tests/sim/cases.py`가 코드로 만든다(합성). T1 기대 로그는 `simulator.md`에서 그대로 잘라 `tests/fixtures/sim/t1_events.jsonl`로 두었다.
  - `simulator.md`에 설계가 비워 둔 세부를 구현하며 정한 대로 적었다(같은 PR).
- 설계와 달라진 점: 없음. 손계산 표와 참고 SHA-256(T1·T2·T2b·T3·T4)이 첫 구현에서 모두 맞았다.
- 결정한 것(설계가 비워 둔 부분. 모두 `simulator.md`에 반영)
  - 명시 배정(`vehicle_ids`) 차량을 먼저 예약하고 나머지 노선을 채운다. `vehicle_ids` 길이 ≠ `vehicle_count`면 `PLAN_INFEASIBLE`(§1.4).
  - `duties[]`는 trip이 있는 차량만. 대수 0인 노선의 `period_s`는 `null`. 제공 시간표는 정렬 후 사용(§1.5).
  - 시간표 검사에 trip 정류장열 = plan leg, duty–trip 일치, 노선별 차량 수 ≤ `vehicle_count`, `initial/end_stop_id` 일치를 더했다(§1.5).
  - 실현기 입력 행은 `DemandFlow(kind, stop_id, station_id, bin_start_s, flow)`(§2.4).
  - `run_inputs.json`의 `checksum` 키 이름(`arrivals`·`travel_times`·`scenario`), URI는 `run_inputs.json` 기준 상대 경로, 실행 때 해시·인원·`network_snapshot_id` 불일치는 `INPUT_INVALID`(§0.3).
  - `run.json`에 `timetable_uri` 추가, `sim_version = "busstrike-sim/0.1.0"`(§6.2).
  - 지표 세부: `by_cause`는 6개 cause를 0까지 모두, `trips[]`는 실행된 영업 trip만, 차량 `first_s`/`last_s`는 arrive·depart만(cancel 제외) 등(§6.3 "2단계 구현에서 정한 세부"). 3단계 검증기 V13은 이 규칙으로 재계산해야 한다.
  - CLI: `python -m busstrike.sim run --scenario S --run-inputs R --plan P --out DIR`, `SimError`면 종료 코드 2(§10).
- 남은 문제 / 다음 단계가 알아야 할 것
  - 1단계의 "사용자가 확인해야 할 결정" 9개는 그대로 남아 있다(D02·D03·D06·D07, `W_e`, 정차·회차 값, 도착 스냅숏, 3안 공통 생성기, egress 시간대 의미). 코드는 모두 Scenario 입력으로만 받고 상수로 박지 않았다.
  - T3의 클러스터 검사 중 `route_clusters` 기대값은 `curve.py`(5단계) 몫이라 넣지 않았다. 2단계는 "목적지가 갈리면 단독 실행 합 = 함께 실행" 성질만 엔진으로 검사한다.
  - 3단계(검증기)는 `busstrike.sim`을 import하지 않아야 한다. 입력 디렉터리는 `tests/sim/cases.py`의 `Case.write()`로, 실행 결과는 `run()`으로 만들 수 있다(테스트 쪽에서 sim을 써서 fixture를 만드는 것은 검증기 코드의 독립성과 별개).
  - 속도 실측(합성 5만 명·30노선·62대·trip 810개·이벤트 약 9.8만 줄, Python 3.14, WSL2): 시간표+준비 0.08초, 엔진 0.31초, 로그 직렬화+지표 0.50초, 파일 입출력 포함 `run()` 1.41초. 목표(2초) 안이라 §4.2 하위 줄 최적화는 넣지 않았다. 합성 입력이 과부하라 이벤트 수는 추정(15만–25만)보다 적다.
  - Python 3.10–3.13에서는 실행해 보지 않았다(이 환경에 3.14만 있음). 3.10 이후 문법만 썼다(`dataclass(slots=True)` 등).
  - 연결된 Issue가 없다.
- 테스트 실행 명령과 결과
  - `PYTHONPATH=src python3 -m unittest discover -s tests -t .` → 86개 통과(기존 22 + 이번 64, Python 3.14.4, 약 6초).
  - 린트·타입 검사는 저장소에 명령이 없어 실행하지 않았다.

## 3단계 — 독립 검증기

- PR: https://github.com/OpenSwProject26-2/2team/pull/33 (Draft, base `feat/sim-core`, 스택 PR, 브랜치 `feat/sim-validator`)
- 한 일
  - `src/busstrike/evaluation/`에 `_inputs.py`(자체 파서·구간 시간 조회·해시), `_checks.py`(V01–V15, 로그 색인, 대기열 재구성, 지표 재계산), `validator.py`(`validate_run`·`write_report`·`failed_checks`·CLI)를 만들었다. `busstrike.sim`은 import하지 않고 설계 문서만 보고 따로 구현했다. 표준 라이브러리만 쓴다.
  - `tests/evaluation/`에 손계산 시나리오 통과, 합성 30개 통과, 변조 18종 검출, 리포트 형식, CLI, 독립성(AST + `sys.modules`) 테스트를 넣었다. 실행 디렉터리는 `tests/evaluation/runs.py`가 `tests/sim/cases.py` + `busstrike.sim.run`으로 만든다(입력과 출력을 한 디렉터리에 둔다).
  - `simulator.md` §8.2·§8.3·§9.10·§10에 3단계에서 정한 세부를 적었다(같은 PR).
- 설계와 달라진 점(문서를 먼저 고침)
  - **V14**: "같은 차량의 연속 cycle 간격 ≥ `period_s`"는 §1.3과 맞지 않았다. 생성기는 `s = max(슬롯, prev_ready)`이다. 직전 cycle이 시간대 경계에 걸려 P보다 길면 그 cycle만 밀리고, 다음 cycle은 슬롯으로 돌아와 간격이 P보다 짧아진다. 합성 seed 1·2에서 정상 시간표인데도 실패했다. "cycle m anchor ≥ `start + floor(k·P/n) + m·P`, `cycle_index` 0부터 연속"으로 바꿨다. 엔진·생성기는 바꾸지 않았다.
  - **V10**: `cancel`은 뺀다. 이동이 아니라 판단 기록이고 `t = ready`(회차 포함)라 `W_e`를 넘을 수 있다. 시각은 V09가 §3.4 식으로 본다.
- 결정한 것(설계가 비워 둔 부분. 모두 `simulator.md` §8.3 "3단계 구현에서 정한 세부"에 있음)
  - 입력 위치: `run.json`·`timetable.json`·`events.jsonl`·`metrics.json`은 `--run-dir`에서 읽는다. `run.json`에 입력 경로가 없으므로 `scenario.json`·`run_inputs.json`·`plan.json`은 기본이 같은 디렉터리이고, 다르면 `--scenario`·`--run-inputs`·`--plan`으로 준다. 입력을 읽을 수 없으면 위반이 아니라 `ValidatorInputError`, 종료 코드 2.
  - 검사 분담: `board` 필드가 `arrive`와 다르면 V06, `alight`면 V07, `depart`·`denied`면 V04. 최종 결과 누락·중복은 V02만 본다.
  - V11의 "아직 안 탄 승객"은 승차 시각이 아니라 승차가 속한 visit 기준이다. V09는 첫 cycle의 취소 조건을 보지 않는다(엔진과 같음).
  - 변조 테스트는 실패 검사 집합을 정확히 고정한다. 로그를 바꾸면 V13 등이 함께 실패하는 연쇄도 그대로 고정한다. 설계 표 "board 복제해 정원 초과"는 T1(좌석 3)에서 정원을 넘지 않는다. 그래서 정원 초과는 T2의 `denied` q3→`board` 변조로 본다.
  - 검사가 예외로 멈추면 `fail` + `check aborted` 위반 1건(통과로 두지 않음). `validator_version = "busstrike-validator/0.1.0"`.
- 남은 문제 / 다음 단계가 알아야 할 것
  - 1단계의 "사용자가 확인해야 할 결정" 9개는 그대로 남아 있다. 검증기도 값은 모두 Scenario 입력에서 읽는다.
  - V14는 P를 다시 계산하지 않으므로(§8.4) 밀린 cycle이 정확히 `prev_ready`에 출발했는지는 보지 않는다.
  - 5단계 어댑터는 `independently_verified = report["verified"]`, `violations = failed_checks(report)`로 옮기면 된다(§8.3).
  - 4단계(GTFS)는 검증기와 무관하다. 다만 `timetable.json` 형식을 바꾸면 `_inputs.parse_timetable`과 V14도 같이 고쳐야 한다.
  - 다른 검증기 브랜치 `feat/28-evaluation-m05-verifier`·`feat/29-evaluation-m05-verifier`(SimPy 기반 PR #6용)가 있다. 읽기만 했다. 모듈 파일 이름은 겹치지 않지만 `src/busstrike/evaluation/__init__.py`를 양쪽이 서로 다른 내용으로 추가한다(`tests/evaluation/__init__.py`는 둘 다 빈 파일). 그래서 나중에 병합되는 쪽은 그 파일에서 add/add 충돌이 난다. 어느 검증기를 M07로 쓸지와 병합 순서는 팀이 정해야 한다.
  - 속도(합성 5만 명·이벤트 약 9.8만 줄, Python 3.14, WSL2): `validate_run` 4.75초, 엔진 `run()` 1.48초. S(n) 반복(5단계)에서는 전부를 검증하지 말고 표본 실행만 검증하는 편이 낫다.
  - Python 3.10–3.13에서는 실행해 보지 않았다. 연결된 Issue가 없다.
- 테스트 실행 명령과 결과
  - `PYTHONPATH=src python3 -m unittest discover -s tests -t .` → 104개 통과(2단계까지 86 + 이번 18, Python 3.14.4, 약 10초).
  - 린트·타입 검사는 저장소에 명령이 없어 실행하지 않았다.

## 4단계 — 셔틀 GTFS 내보내기

- PR: https://github.com/OpenSwProject26-2/2team/pull/34 (Draft, base `feat/sim-validator`, 스택 PR, 브랜치 `feat/sim-gtfs-export`)
- 한 일
  - `src/busstrike/sim/gtfs.py`: `timetable.json`의 예정 시각을 `agency`·`routes`·`trips`·`stop_times`·`calendar`·`feed_info`(.txt)로 내보낸다(`export_gtfs`, 바이트 생성은 `build_gtfs_files`). 내보낸 feed를 다시 읽는 `read_gtfs`도 넣었다. `python -m busstrike.sim gtfs --scenario S --timetable T --out DIR` CLI를 추가했다. 표준 라이브러리만 쓴다.
  - `tests/sim/test_gtfs.py`(13개): 왕복 테스트(trip 수, 노선별 정차 순서, 출발 시각, 차량(block), 정차 시각·`stop_sequence` 전부) — T1·합성 3개 seed·`run()` 산출물 경유, 파일·헤더·줄 끝·BOM, 달력, pickup/drop_off/timepoint, direction_id, 결정론, 시각 형식(25시), 오류.
  - `simulator.md` §6.4에 4단계에서 정한 세부를 적었다(같은 PR).
- 설계와 달라진 점: 없음. 엔진·생성기·검증기·`timetable.json` 형식은 바꾸지 않았다.
- 결정한 것(설계가 비워 둔 부분, 모두 §6.4 "4단계 구현에서 정한 세부")
  - `feed_info.txt` 열과 값, 영업 trip이 없는 노선은 `routes.txt`에서 제외, 노선 긴 이름 구분자 `–`.
  - `service_date` 요일 계산에 `datetime` 대신 `calendar`를 썼다(2단계의 "시계·난수 import 금지" 검사를 그대로 유지).
  - `stops.txt`는 호출자가 좌표(`stop_coords`)를 줄 때만 쓴다. 좌표 출처 연결은 하지 않았다.
- 남은 문제 / 다음 단계가 알아야 할 것
  - 1단계의 "사용자가 확인해야 할 결정" 9개는 그대로다(특히 7번: `arrival_time = departure_time`은 도착 스냅숏 승차를 전제로 한 내보내기 규칙).
  - `stop_id`는 `timetable`의 ID를 그대로 쓴다. A의 버스 GTFS ID와 같은지는 5단계 어댑터와 M00 확인이 필요하다. 이 feed 단독으로는 `stops.txt`가 없어 완결된 feed가 아니다(A의 feed와 합쳐야 함). 표준 GTFS 검증기(예: gtfs-validator)로는 돌려 보지 않았다.
  - 공차 trip은 내보내지 않으므로 같은 `block_id` 안에서 시각 간격이 벌어질 수 있다(설계대로).
  - 5단계(`sim/adapters/planning_bridge.py`, `curve.py`)는 이 브랜치에서 `feat/sim-planning-bridge`를 따고 base를 이 PR 브랜치로 잡는다.
  - Python 3.10–3.13 미실행(3.14만). 연결된 Issue 없음.
- 테스트 실행 명령과 결과
  - `PYTHONPATH=src python3 -m unittest discover -s tests -t .` → 117개 통과(3단계까지 104 + 이번 13, Python 3.14.4, 약 11초).
  - 린트·타입 검사는 저장소에 명령이 없어 실행하지 않았다.

## 5단계 — C와 연결(어댑터·S(n)·근사–시뮬 비교)

- PR: https://github.com/OpenSwProject26-2/2team/pull/36 (Draft, base `feat/sim-gtfs-export`, 스택 PR, 브랜치 `feat/sim-planning-bridge`)
- 한 일
  - `src/busstrike/sim/adapters/planning_bridge.py`: C `Inputs`·`Plan` → `scenario.json`·`travel_times.json`·`arrivals.jsonl`·`run_inputs.json`·`plan.json` 어댑터, `run_plan`(어댑터 → `run()` → 검증기 → 지표), C `EvaluationPort` 구현(`evaluation_port`·`make_evaluation_port`), `curve_report`(S(n) 표 + C 근사), `compare_with_estimate`(근사–시뮬 비교표), Markdown 렌더링, CLI `python -m busstrike.sim.adapters.planning_bridge`.
  - `src/busstrike/sim/curve.py`: `eligible_passengers`·`fleet_curve`·`marginal_gains`·`route_clusters`·`overlap_ratios`·`cluster_eval`. 파일 없이 `normalize_plan → simulate → compute_metrics`를 반복한다.
  - 테스트 23개: `tests/sim/test_curve.py`(T1·T2·T3 기반 곡선·클러스터·가법성), `tests/sim/test_planning_bridge.py`(어댑터 변환, C 합성 예제 끝까지 실행·손계산 이벤트, C `checked_assessment`·`rerank` 통과, 결정론, 근사–시뮬 비교, 정원 여유 일치 조건, CLI, 구로 규모 1회 시간 측정). C 형식 합성 입력 생성기는 `tests/sim/planning_cases.py`.
  - `simulator.md` §7.4(5단계 실측)·§7.5(어댑터 세부·차이 관찰)·§10(시그니처)와 `sim/README.md`를 고쳤다(같은 PR).
- 설계와 달라진 점(문서를 먼저 고침)
  - `fleet_curve`의 포화 뒤 점: 설계는 "같은 값으로 채움"이었지만 거리·대기는 복사하지 않고 `null`, `simulated=False`로 둔다(차량이 늘면 거리도 늘어 복사값이 틀림).
  - `compare_with_estimate` 반환: `list[dict]` → `{rows, summary}`.
  - 비교표 행에 운행안 출처 대신 **만든 방법 라벨**(`equal`·`proportional`·`search-topN`·`exhaustive`)을 쓴다. 기존 `test_fairness`의 AST 검사가 `sim/` 아래 전부에서 `source` 읽기를 막기 때문이다. `plan.json`은 C `to_service_plan()`의 키를 그대로 옮기고 `routes`만 바꾼다(출처 키는 손대지 않고 복사만).
- 결정한 것(모두 `simulator.md` §7.5)
  - 승객 ID `c{수요 순번:06d}-{k:05d}`(순번 = `demand_id` 정렬 순)로 같은 도착 시각의 줄 순서를 C의 `(arrival_s, demand_id)`와 맞췄다. 접두어 → `demand_id`는 `demand_index.json`.
  - C에 없는 값: 1인당 정차 0, `cycle_rest_s` 0, 입석 0, 차급 `planning-uniform`, `kind = access`, `arrival_method = "precomputed"`, `seed = "none"`, `bin_length_s = 1`.
  - `PLAN_INFEASIBLE`은 `schedule_feasible=False` Assessment로, 그 밖의 `SimError`는 예외로 올린다.
  - `rank_sim`은 C `rerank`와 같은 키 `(-충족, P90, 거리, 배분)`. 차이에 판정 기준은 두지 않았다.
- **S(n) 표 — C 합성 예제**(`synthetic.json`, 정원 2·차량 2, 클러스터 `[r1, r2, r3]`, `β` 모두 1)

  | 노선 | eligible | 겹침 비율 | n=1: S_sim / ΔS / S_est | n=2: S_sim / ΔS / S_est |
  | --- | --- | --- | --- | --- |
  | r1 | 6 | 0.166667 | 5 / 5 / 5 | 6 / 1 / 6 |
  | r2 | 3 | 0.333333 | 3 / 3 / 3 | 3 / 0 / 3 (포화, 미실행) |
  | r3 | 1 | 1 | 1 / 1 / 1 | 1 / 0 / 1 (포화, 미실행) |

- **근사–시뮬 비교표 — C 합성 예제**(전수 배분 10개, 모두 검증 통과): 충족·평균 대기·거리가 10개 모두 같다(MAE 0, 스피어만 1.0, 시뮬 1위 = 근사 1위 = 균등 `r1=1, r2=1`, 7/9명). 순위는 동률 처리만 다르다(`r1=1,r3=1`과 `r1=1`이 둘 다 5명: 근사는 평균 대기, 시뮬은 P90·거리로 끊음). 전체 표는 CLI `report.md`.
- **근사–시뮬 비교 — 구로 규모 합성**(노선 30·차량 60·승객 47,709명, 판정 아님)

  | 배분 | est 충족 | sim 충족 | sim−est | est 평균대기(s) | sim 평균대기(s) | sim `FULL` |
  | --- | --- | --- | --- | --- | --- | --- |
  | 균등(원래 노선 20×3) | 19,444 | 19,080 | −364 (−1.9%) | 683 | 400 | 11,845 |
  | 30노선×2 | 21,652 | 21,131 | −521 (−2.4%) | 731 | 463 | 19,830 |
  | 앞 10노선×6 | 17,200 | 17,087 | −113 (−0.7%) | 531 | 313 | 3,406 |
  | 앞 15노선×4 | 19,189 | 18,802 | −387 (−2.0%) | 620 | 364 | 7,029 |
  | 홀짝 1·3대 | 21,404 | 20,823 | −581 (−2.7%) | 685 | 430 | 14,075 |

  거리는 5안 모두 같고 순위도 같았다(스피어만 1.0). 노선 단독 곡선(n 0–15): `β_r` 0.989–0.999, 점별 차이 −8.3%~+2.3%(최대 −76명, R25 n=4), 시뮬 한계효용이 음수인 점 38/450.
- **차이가 큰 경우와 원인 추정**: 차이는 혼잡(`FULL`)이 많은 안에서 커진다. C는 수요를 도착 시각 순으로 trip 전 구간 좌석을 미리 잡고, 시뮬은 차가 지나가는 순서(상류 먼저)로 태운다. 시뮬에서는 하류 정류장 승객이 연달아 남겨지다 대기 한도에 걸린다 — 균등 배분 실행에서 `FULL` 비율이 차고 0.1% → 첫 정류장 8.7% → 다섯째 51% → 허브 48%로 노선을 따라 커졌다. 시뮬 평균 대기가 짧은 것은 오래 기다릴 승객이 포기로 빠지는 생존 편향으로 본다. 1인당 정차·시간대별 구간 시간 차이는 이번 입력에 없다(C에 없는 값이라 어댑터가 0·고정으로 넣음). 실데이터에서는 이 두 요인이 더해진다.
- **1회 실행 시간**(구로 규모 합성, Python 3.14, WSL2 `/mnt/c`): 어댑터 0.12–0.16초, `run_plan`(입력 파일 쓰기 + `run()`) 1.8–2.7초, 검증기 3.0–7.2초 → 전체 5–10초. C `evaluate()` 1회 0.5–0.6초. 곡선(노선 30 × n 0–15) 시뮬만 12.2초, C 근사까지 함께 내는 `curve_report` 130초(대부분 C `evaluate()` 480회).
- C와 맞추기 어려운 부분(C 코드는 바꾸지 않았다)
  - C 수요는 정류장→정류장 한 개씩이다. M05의 egress(역에 딸린 정류장 집합에서 출발)와 access/egress 구분을 표현할 수 없어 모두 `access`, 정류장 1개로 넘긴다.
  - C 구간은 `segment_id`로, M05 구간은 정류장쌍으로 찾는다. 같은 정류장쌍에 값이 다른 구간이 둘이면 어댑터가 거절한다.
  - C는 모든 후보가 차량 거점(`fleet[0].initial_stop`)에서 출발해야 받는다. 실제 노선처럼 기점이 다르면 C가 "차고지 공차 이동 자료가 필요한 후보"로 빼므로, 어댑터를 거치기 전에 걸러진다.
  - C 평균 대기는 충족 인원 기준, 시뮬 평균·P90은 탑승 인원 기준이다(`JOURNEY_EXCEEDED`가 0이면 같음). C `rerank`가 쓰는 `p90_wait_s`도 탑승 인원 기준으로 넘긴다.
  - §7.3의 보정 2단계(C 배분 탐색이 노선별 근사 대신 `fleet_curve`·`cluster_eval`을 쓰는 것)는 C `search.py`를 바꿔야 해서 하지 않았다. C가 `curve_report` 값을 받아 쓰도록 바꾸는 것은 C 담당 몫이다.
- 남은 문제 / 실데이터를 붙이려면 남은 작업
  - **수요 벡터**: 실제 입력은 시간대별 소수 유량(access·egress, 역 단위)이다. 지금 어댑터는 C의 정수 수요에서 승객을 만든다. 실데이터에서는 방향을 바꿔야 한다: A(M03) 수요 벡터 → `arrivals.py` 실현기(체계적 반올림)로 승객을 한 번 만들고, 그 승객을 `(origin, dest, arrival_s)`로 묶어 C 수요를 만든다. 그래야 두 평가가 같은 `RunInputs`를 쓴다. 역 = 정류장 집합(egress 출발, access 도착)을 C 수요로 바꾸는 규칙은 C와 정해야 한다(C 모델은 목적지 정류장 1개). egress 시간대 의미(§2.3)도 M03 확인이 남았다.
  - **구간 이동시간**: 실제는 TPSS 시간대별 속도라 `travel_times.json`을 M02/A가 직접 만들어야 한다. 어댑터가 C 구간에서 만드는 대신 외부 `travel_times.json`을 받는 경로가 필요하다. C는 구간 시간이 고정이므로 C에 줄 대표값(최댓값/평균) 규칙과, C 구간 ID ↔ 정류장쌍 ↔ A 버스 GTFS `stop_id`의 일치 검사가 남았다. 시간대별 구간 시간이 들어가면 시뮬 주기 P(최댓값 기준)와 C 주기가 달라져 §7.3의 1(일치 조건)이 깨진다.
  - 차량: 실제 노선 기점이 여럿이면 C의 "동일 거점" 제약과 M05의 "기점 사전 배치"(D03)가 다르다. D03 결정과 C 제약 완화가 필요하다.
  - 정차·회차 실제값(1인당 정차 포함)이 C 입력에 없다. Scenario 값으로 받을 경로가 필요하다.
  - 1단계의 "사용자가 확인해야 할 결정" 9개는 그대로다.
  - S(n) 반복과 탐색에서는 검증기를 표본·최종 후보에만 돌리는 편이 낫다(1회 3–7초). `curve_report`의 시간 대부분은 C `evaluate()`다.
  - 표의 숫자는 모두 합성 입력이다. 실제 운행 성능이나 C 근사의 정확도 판정 근거가 아니다.
  - Python 3.10–3.13 미실행(3.14만). 연결된 Issue 없음.
- 테스트 실행 명령과 결과
  - `PYTHONPATH=src python3 -m unittest discover -s tests -t .` → 140개 실행, 139개 통과·1개 건너뜀(4단계까지 117 + 이번 23. 건너뛴 1개는 `BUSSTRIKE_SCALE_CURVES=1`일 때만 도는 구로 규모 전체 곡선 측정. 별도로 돌려 위 숫자를 얻음). Python 3.14.4, 약 16초.
  - `PYTHONPATH=src python3 -m busstrike.sim.adapters.planning_bridge --input tests/fixtures/planning/synthetic.json --out <임시>` → 종료 코드 0, `curves.json`·`comparison.json`·`report.md` 생성.
  - 린트·타입 검사는 저장소에 명령이 없어 실행하지 않았다.

## 6단계 — M07 출력 형식 통일

- PR: (PR을 연 뒤 링크 추가) (Draft, base `main`, 브랜치 `feat/sim-m07-output`). #28·#32·#33·#34·#36을 대체한다.
- 한 일
  - 최신 `main`에서 `feat/sim-m07-output`을 따고 `pr/36`(5단계까지의 스택)을 병합했다. `evaluation/__init__.py` 충돌은 main(giho999) 버전을 그대로 썼다.
  - 3단계 검증기(`evaluation/validator.py`·`_checks.py`·`_inputs.py`, `tests/evaluation/test_validator.py`·`test_tamper.py`·`test_independence.py`·`runs.py`)를 지웠다. giho999 파일과 이름이 겹치는 것은 없었다.
  - 검증기 코드·fixture·테스트에서 형식을 거꾸로 정리해 [m07-output-contract.md](m07-output-contract.md)를 썼다. 항목마다 근거 파일과 줄을 달고, 추론한 항목 I1–I9를 따로 모았다. 검증기가 인용하는 `docs/verifier_spec.md`는 main에 없어서 PR #6 브랜치의 커밋 `ca643f6`을 참고로 읽었다.
  - 새 엔진과 M07의 의미 차이 D1–D16을 표로 정리했다(계약 §7). 기본 원칙은 "엔진을 M07에 맞춘다"였고, 공정성이 걸린 D1(정류장 집합 OD)과 검증기 규칙이 걸린 D2(두 역 연결형 노선)는 코디네이터에게 물었다. 사용자는 둘 다 A를 골랐다.
  - 엔진을 고쳤다: 고정 일정 실행기(`engine.service_schedule`·`simulate`), M07 사건 로그, `demand_bins.csv` 입력 + M07 생성기(`arrivals.generate`), M07 지표(`metrics.py`), M07 폴더 writer(`run.write_m07`, `run.json` 없앰). `planning_bridge`의 검증 단계는 `busstrike.evaluation.verify.verify`를 쓴다. `curve.py`도 새 지표를 쓴다.
  - 손계산 T1–T5를 M07 의미로 다시 했다(simulator.md §9). T2b(지연 취소)는 고정 일정이라 일어나지 않아 "차내 여정 만료" 사례로 바꿨다. 교차 검증(§9.11)과 구로 규모 실행(§9.12) 테스트를 더했다.
  - `simulator.md`의 출력(§6)·검증(§8)·테스트(§9)를 새로 쓰고, §0.3·§2·§3·§4·§7.5·§10·§11·가정 목록에서 바뀐 곳을 고쳤다. `sim/README.md`도 고쳤다.
- 설계와 달라진 점(의미를 바꾼 항목, 계약 §7)

  | # | 바꾼 것 | 이유 |
  | --- | --- | --- |
  | D1 | 승객 OD: 정류장 집합 → 정류장 1개(실현기 입력 `station_stop_id`) | M07이 단일 정류장 OD만 표현한다. 사용자 결정 A. 공정성 손실은 가정 A25 |
  | D2 | 영업 leg가 2개 이상인 노선(두 역 연결형)은 M07 실행에서 `OUT_OF_SCOPE` | M07 V16 "같은 출발 거점". 사용자 결정 A, 가정 A26 |
  | D3 | 1인당 정차 → `OUT_OF_SCOPE`, 지연·`cancel` 없음(고정 일정) | M07 V3은 사건 시각 = `network.json` 일정을 요구한다 |
  | D4 | 발생: 시간대별 인원은 체계적 반올림, 시각은 M07 생성기, ID `["행",i]` | M07 V1. SHA-256 결정 함수라 원칙 유지 |
  | D6 | 여정 한도: 사후 판정 → 결과를 정하는 기한(unserved journey) | M07 V7·V8 |
  | D7 | `drain_s`(기본 `W_e + max_journey_s`), `left_waiting` 없음 | M07 config |
  | D8 | 같은 초 우선순위: vehicle_id → `(time_s, phase, stable_id)` | M07 V2·V6 |
  | D9·D10 | 미충족 6원인 → 4사유, 만차는 `refused` | M07 V10·V11 |
  | D11–D13 | 로그·지표·실행 기록 형식 → M07(`run.json` → `measurement.json`) | M07 파일 계약 |
  | D14 | 검증기 → M07, C `independently_verified` = M07 `pass` | 팀 공식 검증기로 일원화 |
- 결정한 것(요구사항에 없던 판단)
  - M07 운행편 = 영업 leg 1개. `departures[-1]` = 마지막 도착(I3), `return_s` = 뒤따르는 공차 leg 끝(I1), `distance_m` = 영업 + 공차(I2).
  - 운행안이 준 시간표가 구간 시간·정차·회차와 맞지 않으면 `PLAN_INFEASIBLE`(고정 일정이라 실행 중에 고칠 수 없다).
  - scenario에 선택 필드 `stop_area`, policy에 `drain_s`·`max_headway_s`·`wait_threshold_s`를 더했다. 없으면 M07이 미검증으로 두는 항목(V12·V15)이 생기지만 실패는 아니다.
  - `measurement.env`에는 엔진 커밋과 `dirty`(추적 파일)만 넣는다. M07 보고서가 엔진 커밋을 인용하고, 검증기 테스트도 fixture에 커밋이 있다고 본다. 같은 체크아웃에서는 값이 같아 결정론이 유지된다.
  - `timetable.json`은 `run/`에 남겼다(GTFS 내보내기용). 검증기는 이름을 모르는 파일을 열지 않는다(`inputs.py:15-17`, `:269-272`).
  - 교차 검증에서 F2의 t3 거리 500 m(t1·t2는 1000 m)는 구간 거리만으로는 만들 수 없다. 그래서 r2 복귀 공차를 경유지 Y로 돌려 맞췄다(`tests/sim/m07_fixture_cases.py` 주석).
- 남은 문제 / 다음 단계가 알아야 할 것
  - **giho999 확인 필요**: 계약 §6의 추론 항목 I1–I9. 특히 I1·I2(`return_s`·`distance_m`에 복귀 공차 포함), I3(마지막 출발 = 마지막 도착이라 회차 시간이 M6 공차시간에 들어감), I5(drain 뒤 발생 승객은 V4 실패), I6(V16 "같은 출발 거점"이 양방향 노선을 막음).
  - **giho999 fixture 재생성 스크립트가 예전 엔진에 묶여 있다**. `tests/evaluation/fixtures/make_fixtures.py`는 `sim.arrivals`·`sim.engine.simulate`·`sim.harness`(`HashingJsonlSink`·`OutcomeWriter`·`env_manifest`·`write_scenario`)·`sim.metrics`·`sim.models.Config/Trip`·`sim.run_inputs.RunInputs`를 import하고, `make_complex.sh`는 `sim.harness generate/run`(SimPy 4.1.2, `origin/feat/5-simulation-core`, 엔진 커밋 `d18ee1e`)을 부른다. fixture README의 재생성 절차도 같은 브랜치를 쓴다. PR #6을 닫아도 커밋은 `refs/pull/6/head`로 남지만, 새 엔진으로 바꾸려면 다음이 필요하다. ① F1·F2는 `tests/sim/m07_fixture_cases.py`의 변환과 `busstrike.sim.run`을 쓴다. 새 엔진 출력은 `passenger_outcomes.csv` 바이트와 `network.json` 운행편이 지금 fixture와 같고, 사건 로그 바이트(구분자)·`measurement.json` 필드·`metrics.json` 보조 키는 다르다. ② complex는 새 합성 생성기가 필요하다(예: `tests/sim/cases.synthetic` 또는 `planning_cases.guro_scale` + 브리지). ③ 검증기 테스트 중 fixture 내용에 기대는 것(`test_verify_cli`의 엔진 커밋 형식, 변조 사례 T1–T20의 줄 위치)은 재생성 뒤 다시 확인해야 한다. 이 PR은 fixture와 스크립트를 고치지 않았다(검증기 소유).
  - **다른 PR 영향**(닫거나 고치지 않았다)
    - #13(`feat/10-seoul-demand-analysis`): `A.Part/data/prepare_arrivals.py`와 통합 검사가 예전 엔진 커밋 `d18ee1e`의 `sim.arrivals`(`check_rows`·`generate`·`write_realization`·`read_realization`·`validate_realization`)와 `sim.adapters.opt_adapter.c_demands`를 외부 체크아웃으로 고정해 import한다. 새 엔진에는 이 API가 없다(`busstrike.sim.arrivals.generate(rows, seed)`·`io.read_demand_bins`, C 방향 변환 없음). 반면 #13의 `demand_bins_included.csv` 여섯 열은 M07 `demand_bins.csv`와 같아서 새 엔진 입력으로 바로 쓸 수 있다(`build_run_inputs`). 단 aggregate_id 유일·출발≠목적 조건은 같고, 폭 3600초 bin 안의 시각은 M07 생성기가 정한다.
    - #16(`test/14-common-synthetic-fixtures`): C 형식 공통 합성 입력이다. 예전 엔진에 의존하지 않고 이 PR과 겹치는 파일도 없다. 정확 `arrival_s` 수요는 브리지(`demand_rows`, 폭 1초)로 그대로 실행된다.
    - #28·#32·#33·#34·#36: 이 PR이 대체한다. 이 PR이 병합된 뒤 사용자가 닫는다.
  - C의 `rerank`는 `independently_verified`(= M07 `pass`)를 본다. M07 `fully_verified`는 V16 때문에 늘 false라 쓰지 않았다(코디네이터 확인 대기, 아래 "멈춘 지점").
  - 1인당 정차·양방향 영업 노선·정류장 집합 OD를 다시 쓰려면 M07 형식 확장(검증기 쪽 결정)이 먼저다.
- 테스트 실행 명령과 결과
  - `PYTHONPATH=src:. python3 -m unittest discover -s tests -t .` → (최종 결과는 PR 본문 "## 확인")
