# M05 prototype: single_bus_leg

공용 계약 승인본을 추가하거나 변경하지 않는다. 내부 모델은 `models.py`, 고정 realization은
`run_inputs.py`, 차량·승객 상태는 `vehicles.py`/`passengers.py`, 사건 실행은
`dispatcher.py`/`engine.py`, 출력은 `events.py`/`reporting.py`다.
C 전용 변환과 일정 생성은 `adapters/opt_adapter.py`에 격리했다. M06은 `ports.py`의
ScheduleConsumer로 일정만 받을 수 있으며 M07 import는 없다.

## 실행

저장소 루트에서:

```sh
python3 -m venv .venv
.venv/bin/pip install -r sim/requirements.txt
.venv/bin/python -m unittest discover -s tests -t . -v
.venv/bin/python -m sim.benchmark --passengers 10000
```

`-t .`는 tests/opt, tests/sim이 실제 패키지를 가리지 않도록 필요하다.
SimPy 4.1.2 (MIT)는 사용자 요청에 따른 M05 전용 의존성이다. 표준 라이브러리 heapq는
명시 순서를 정하고 SimPy Environment/process/timeout은 시간 전진을 실행한다.
공식 API: https://simpy.readthedocs.io/en/stable/api_reference/simpy.core.html
루트 의존성과 lockfile은 변경하지 않았다.

## C 연결 예

```python
from sim.models import Config
from sim.adapters.opt_adapter import prepare_inputs, evaluation_port
run = prepare_inputs(inputs, seed='synthetic-v1')  # 후보 간 재사용
config = Config(max_wait_s=inputs.policy.max_wait_s,
                max_journey_s=inputs.policy.max_travel_s, drain_s=1000,
                boarding_policy='fifo', travel_policy='fixed_segments')
port = evaluation_port(run, config)
assessment = port(plan, inputs)
```

반환 Assessment는 항상 independently_verified=False다. C rerank는 이 결과로 최종 추천을
만들 수 없다. 일정 오류나 realization 불일치는 예외로 전파한다. 실제 검증된 추천이나
실제 운행 승인을 의미하지 않는다. 입력·결과 checksum은 별도로 보존한다.

## 명시적 프로토타입 정책 · 팀 검토 필요

- exact aggregate arrival_s를 그대로 개인별 발생 시각으로 확장한다. 시간 bin 내 분포를
  추정하지 않는다. seed만으로 입력 동일성을 판단하지 않는다.
- fixed_segments는 C snapshot의 모든 구간 이동시간과 완전히 일치해야 한다.
  누락·추가·변경 realization을 거절한다. 시간대별 혼잡과 확률적 realization은 미지원이다.
- 동일 조건 차량을 ID 순서로 배분하고, 노선당 cycle/count 간격으로 시작한다.
  전체 복귀가 운영·가용시간 안에 끝나는 편만 생성한다. 정차·회차는 C 입력의 명시 값이다.
- 정류장 도착 시 FIFO 승차하며 그 뒤 정차한다. 출발 직전 도착 승객의 추가 승차는
  지원하지 않는다. 경쟁 노선은 stable trip ID 순서다. 목적지 순방향 직행만 탑승한다.
- max_travel_s를 발생부터 목적지 도착까지의 max_journey_s로 연결한다. 예상 도착이
  늦어도 미리 승차를 거절하지 않으며, 기한 초과는 결과만 unserved로 바꾼다.
- 기한 시각은 inclusive다. 같은 시각 하차·승차가 만료보다 먼저다. drain은 절대 time_s다.
  drain까지 성공·실패가 미확정이면 censored이며 물리 상태는 유지한다.
- P90은 served 승객의 실제 대기시간 nearest-rank다. 0명일 때 None이다.
- 거리는 복귀까지 완료한 duty cycle의 공차 포함 거리다. 부분 drain의 거리는 부분 구간
  거리 추정치가 아니다. C port는 전체 duty를 포함하는 drain만 허용한다.

## 로그와 성능

기본 NullSink는 로그를 저장하지 않는다. JsonlSink는 호출자 소유 스트림에 즉시 쓴다.
MemorySink는 테스트용 bounded sink다. sink 실패는 전파되어 성공 결과를 만들지 않는다.
로그는 사건별 time_s/phase/stable_id와 물리 이벤트·점유를 보존한다.
결과는 plan ID/revision, source/run checksum, config hash, 분리된 물리/결과 상태를 보존한다.
현재는 승객별 상태와 예정 사건을 메모리에 유지한다. 로그 분리가 상수 메모리 실행을
의미하지 않는다. 정류장 승차 시 전체 승객을 스캔하므로 큰 운행편 수에서는 비용이 높다.
benchmark는 1만/10만/100만 명의 시간·peak memory·사건 수 측정 기반이다. 단일 대용량
차량 fixture는 처리 비용 측정용이며 현실적인 운송 능력 근거가 아니다. cohort 미구현.

## 미확인 자료와 검토

노션 M00/M05 페이지는 접근 실패했으며 이미지·첨부의 존재나 내용을 확인하지 못했다.
구현 근거는 저장소 M00 문서, C 코드, 사용자 수락 조건이다. 사람이 M00/M05 설계와
일정·기한·P90 정책, C 변환, 합성 fixture, SimPy 의존성 및 학교 AI 정책을 검토해야 한다.
