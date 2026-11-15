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
