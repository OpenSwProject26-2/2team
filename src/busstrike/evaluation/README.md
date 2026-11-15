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
| `arrivals_ref.py` | V1 R5 발생 생성기 독립 재구현(명세 §3 V1만 근거) + V1 검사 |
| `invariants.py` | V2 사건 순서 · V3 일정 일치 · V4 승객 생애 · V5 정원 · V6 FIFO·비유휴 · V7 기한 · V8 결과와 합계 |
| `recompute.py` | 명세 §4 brute-force oracle. 대기열 없이 사건 키를 전부 만들어 한 번 정렬하고 선형 탐색으로 승차자를 고른다 |
| `metrics_ref.py` | `summarize()`로 M1·M2·M3·M5·M6를 재계산하고 V9–V14로 metrics.json과 비교 |
| `guards.py` | V15 R12 배차간격 · V16 G 기타 · V17 체크섬 |

`not_verified`는 통과가 아니다. 자료가 없어 검증할 수 없는 항목(명세 V16 R13 등)에 쓴다.

### 구현 진행

| 검사 | 상태 |
| --- | --- |
| V1 R5 발생 재현 | 구현 (명세 문장 중의성 있음, 아래) |
| V2 사건 순서 | 구현 (명세와 차이 있음, 아래) |
| V3 일정 일치 · V4 승객 생애 · V5 정원 | 구현 |
| V6 FIFO·비유휴 · V7 기한 | 구현 (재계산 oracle 대조) |
| V8 결과와 합계 | 구현 |
| V9 M1 · V10 M2 사유 · V11 만차 거절 | 구현 |
| V12 M3 · V13 M5 · V14 M6 | 구현 |
| V15 R12 배차간격 · V17 체크섬 | 구현 |
| V16 G 기타 | 부분 구현 — **통과 불가**(R10 가용 fleet·R13 방향별 구간 자료 없음) |
| T1–T20 변조 검출 전체 | 구현 (`tests/evaluation/test_tamper_t1_t20.py`) |
| `verify.py` CLI · `verifier_report.json` | 미구현 (5단계) |

### 재계산 oracle

명세 §4대로 엔진과 다른 구조로 만든다. 엔진은 정류장·목적지별 FIFO 대기열의 머리를
k-way 병합하고 자격을 잃은 항목을 지연 제거한다. 검증기는 **대기열을 쓰지 않는다**.
사건 키를 전부 만들어 한 번 정렬하고, 승차마다 자격 승객을 선형 탐색으로 고른다.

`scan='full'`(기본)이 명세 문장 그대로의 전체 선형 탐색이다. `scan='index'`는 출발
정류장으로 후보를 먼저 가르며, 두 방식의 결과가 같은지 테스트로 고정한다.

### 변조 검출 (명세 §6)

T1–T20을 표로 두고 각 사례가 명세가 적은 검사에 걸리는지 확인한다. 같은 사례를 두 번
돌린다.

1. 그대로: 파일을 건드리면 V17(체크섬)이 무조건 걸린다.
2. `tamper.repair_digests`로 manifest·measurement의 digest를 다시 맞춘 뒤: V17의 도움
   없이도 해당 검사가 잡아야 한다. 이쪽이 실제 검출력이다.

명세 §6의 "기대 검출" 중 실제로 걸리지 않는 것은 `Case.not_caught`에 사유와 함께 남긴다.
조용히 지우지 않고 테스트로 고정해 둔다(T5의 V9, T6의 V2).

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

- **V1 생성기**: 명세의 `base = SHA-256(json.dumps([generator, seed, aggregate_id]) ‖ \0)`가
  중의적이다. base를 32바이트 digest로 읽으면(A) 합성 complex 1천 명 중 3명만 맞고, 해시
  입력의 앞 바이트열로 읽으면(B) 1000명이 맞는다. (B)로 구현했다. 명세에 "`SHA-256(base ‖
  index ‖ c)`의 base는 digest가 아니라 앞 바이트열"임을 명시해야 한다.
- **V15 운행편 1개 노선**: 연속 출발이 없는 노선의 "최대 간격"을 명세가 정의하지 않았다.
  엔진은 그 노선을 `max_gap_by_route_s`에서 생략한다. 검증기는 간격이 정의되는 노선만
  비교하고 생략된 노선을 note에 남긴다. 어느 쪽으로도 맞추지 않았다.
- **V16은 통과하지 않는다**: R10(가용 fleet 대수)은 `Scenario.fleet`이 산출물에 없고,
  R13(방향별 구간)은 명세 §3이 직접 "미검증(통과 아님)"으로 정했다. 검사 가능한 R11 정원·
  R14 운영창·차량 duty 비중첩·출발 거점 일치에 위반이 없어도 상태는 `not_verified`다.
- **V14 하차 순서**: 한 방문에서 여러 명이 내릴 때의 순서를 명세가 정하지 않았다. 재계산은
  승차 순서로 내린다. 지표에는 영향이 없으나 로그 바이트 비교에는 영향이 있다.
