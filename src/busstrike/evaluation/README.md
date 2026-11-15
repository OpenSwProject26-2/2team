# M07 지표·독립 검증·지자체안 비교

## M05 독립 검증기

`docs/verifier_spec.md`(엔진 브랜치 `feat/5-simulation-core`, 명세 커밋 `ca643f6`)의 V1–V17을
구현한다. 검증 통과 전 M05 수치는 모두 "독립 검증 전 잠정"이다.

```sh
pip install -e .
python -m busstrike.evaluation.verify <실행 결과 폴더>    # 5단계에서 추가
```

### 독립성 (명세 §1)

| 규칙 | 지키는 방법 |
| --- | --- |
| 코드 분리 | 이 패키지는 `sim`·`opt`·`busstrike.sim`·`busstrike.planning`을 import하지 않는다. 엔진 함수·자료구조·상수를 복사하지 않는다 |
| 입력 경로 | 파일만 읽는다(`inputs.py`). 엔진을 호출해 얻은 값을 정답으로 쓰지 않는다 |
| 정답 출처 | 손계산 fixture(`tests/evaluation/fixtures/handcalc/`) · 검증기 자체 재계산 · 불변조건 |
| 작성자 분리 | 엔진(`sim/engine.py`·`dispatcher.py`·`metrics.py`·`arrivals.py`) 커밋 작성자가 아닌 사람이 작성 |
| 실패 처리 | 하나라도 실패하면 보고서 `pass=false`. 부분 통과를 "검증됨"으로 쓰지 않는다 |

정적 검사: `tests/evaluation/test_import_independence.py`. 깨끗한 하위 프로세스에서
검증기만 import해 엔진 모듈이 적재되지 않는 것까지 확인한다.

외부 패키지를 쓰지 않는다. 표준 라이브러리만 쓴다.

### 구성

| 파일 | 내용 |
| --- | --- |
| `inputs.py` | 산출물 파일 읽기. 일정을 운행편별 튜플이 아니라 **방문(Visit) 평면 목록**으로 둔다 |
| `checks.py` | `CheckResult`·`Violation`. 상태는 `pass`·`fail`·`not_run`·`not_verified` |
| `invariants.py` | V2 사건 순서 · V3 일정 일치 · V4 승객 생애 · V5 정원 · V8 결과와 합계 |

`not_verified`는 통과가 아니다. 자료가 없어 검증할 수 없는 항목(명세 V16 R13 등)에 쓴다.

### 구현 진행

| 검사 | 상태 |
| --- | --- |
| V2 사건 순서 | 구현 (명세와 차이 있음, 아래) |
| V3 일정 일치 | 구현 |
| V4 승객 생애 | 구현 |
| V5 정원 | 구현 |
| V8 결과와 합계 | 구현 |
| V1 R5 발생 재현 | 미구현 |
| V6 FIFO·비유휴 · V7 기한 | 미구현 |
| V9–V14 지표 · V15–V17 | 미구현 |

### 명세와 다르게 구현한 곳

- **V2**: 명세는 키 `(time_s, phase, stable_id)`가 "엄격 증가"라고 적었으나 실제 엔진 출력은
  같은 키에 사건이 여럿이다(한 방문의 하차 여러 건 + 도착 1건, 한 방문의 승차 여러 건).
  명세 §2 자신이 "같은 키에서 alighted가 먼저"라고 적어 엄격 증가와 모순한다. 여기서는
  **약한 증가 + phase 0·2에만 중복 허용 + phase 0 내부 순서(alighted 먼저, vehicle_arrived
  최대 1회) + phase 1·3·4 키 유일**로 검사한다. 엔진을 통과시키기 위한 완화가 아니라,
  명세대로는 모든 정상 출력이 실패하기 때문이다.
- **정수 검사**: 명세 V2의 "정수" 조건은 파일을 읽는 시점에 `InputError`로 걸린다. 검사
  위반이 아니라 입력 오류로 보고된다.
- **M2 사유와 기한 종류 구분**: 명세 §5 표의 "사유"는 M2 사유(`no_connection`·`service_end`·
  `journey`·`wait`)이고 일정 전체를 봐야 정해진다. 사건 로그만으로는 기한 종류(`wait`·`journey`)
  밖에 알 수 없다. 그래서 V8은 `served`·`unserved`·`censored`까지만 판정하고 사유는 V10에서
  다룬다.
