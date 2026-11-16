# 2team — 버스 파업 대체노선 설계·시민 경로 안내

파업으로 영향을 받은 이동 수요를, 제한된 차량과 기존 버스 운행 구간으로 최대한 충족하는
**대체 운행안을 설계·비교**하고, 선택한 안을 **시민 경로 안내**로 연결하는 의사결정 지원
서비스입니다.

> **진행 중인 수업 프로젝트입니다.** 여기의 수치와 운행안은 시뮬레이션 결과이며
> 실제 운행 확정·현장 성능·안전성 검증이 아닙니다. 자세한 것은 [결과를 읽을 때](#결과를-읽을-때)를 봅니다.

## 지금 `main`에 있는 것

모듈 번호는 [M00 데이터 계약](docs/contracts/M00_DATA_CONTRACT.md)을 따릅니다.

| 모듈 | 내용 | `main` 상태 |
| --- | --- | --- |
| M04 `planning` | 노선 후보 생성·대수 배분·평가 포트 | **구현** |
| M07 `evaluation` | 지표·독립 검증·운행안 비교 | **구현** (M05 독립 검증기) |
| M00 `contracts` · M01 `ingest` · M02 `network` · M03 `demand` · M05 `sim` · M06 `routing` · M08 `api` | — | 자리만 있음 |

나머지는 **열린 PR에서 작업 중**입니다. [Pull requests](../../pulls)에서 진행 상황을 봅니다.
`main`에 없다고 해서 작업이 없는 것이 아닙니다.

## 빠른 시작

Python 3.10 이상이 필요합니다. 외부 의존성은 없습니다(표준 라이브러리만).

```bash
pip install -e .
python3 -m unittest discover -s tests -t . -v      # 105건
```

운행안 생성 (M04):

```bash
python3 -m busstrike.planning --input tests/fixtures/planning/synthetic.json
```

시뮬레이터 산출물 독립 검증 (M07):

```bash
python3 -m busstrike.evaluation.verify <실행 결과 폴더>
```

## 시뮬레이션 결과 독립 검증기 (M07)

시뮬레이터가 낸 사건 로그·승객 결과·지표를 **엔진과 따로 다시 계산해서** 맞는지 봅니다.
엔진 코드를 쓰지 않고 출력 파일만 읽습니다. 같은 코드를 쓰면 같은 버그를 그대로
통과시키기 때문입니다.

```bash
python3 -m busstrike.evaluation.verify <실행 결과 폴더>
# 종료 코드: 0 통과 · 1 검사 실패 · 2 입력 오류
```

`verifier_report.json`에 검사별 결과와 입력 파일 sha256, 검증기·엔진 커밋이 남습니다.
**검사 하나라도 실패하면 전체 `pass=false`** 입니다. 보고서는 `pass`(실패 없음)와
`fully_verified`(미실행·미검증까지 없음)를 따로 둡니다 — 자료가 없어 검증할 수 없는 항목을
통과로 적지 않기 위해서입니다.

**어떻게 검증하는가**는 세 방향입니다.

| 방법 | 묻는 것 | 정답의 출처 |
| --- | --- | --- |
| 불변조건 | 로그가 스스로 모순되지 않는가 | 물리적으로 불가능한 일 |
| 재계산 | 같은 입력에서 같은 결과가 나오는가 | 검증기가 구조가 다른 방식으로 다시 돌린 결과 |
| 손계산 대조 | 사람이 손으로 푼 답과 맞는가 | 명세의 손계산 표를 사람이 옮긴 파일 |

엔진 출력과 엔진의 golden 로그는 **정답으로 쓰지 않습니다.** 검증 대상이기 때문입니다.
검사가 공허하지 않다는 것은 정상 산출물을 한 군데씩 고친 사본 20종으로 확인합니다.

실행법·결과 읽는 법·검사 항목은
[`src/busstrike/evaluation/README.md`](src/busstrike/evaluation/README.md)에,
재계산 알고리즘과 명세와의 차이는
[`docs/design/evaluation-verifier.md`](docs/design/evaluation-verifier.md)에 있습니다.
검사 정의 명세 `docs/verifier_spec.md`는 아직 `main`에 없고
[`feat/5-simulation-core` 브랜치(PR #6)](https://github.com/OpenSwProject26-2/2team/blob/ca643f63028908e6a18827abb89c4360bfb1a345/docs/verifier_spec.md)에
있습니다.

## 구조

```text
2team/
├─ README.md · AGENTS.md · CONTRIBUTING.md · pyproject.toml
├─ docs/
│  ├─ contracts/   M00 데이터 계약 (모듈 간 공용)
│  ├─ adr/         설계 결정 기록
│  └─ design/      모듈 설계·평가 프로토콜
├─ src/busstrike/
│  ├─ contracts/   M00 공용 DTO
│  ├─ ingest/      M01 원천 로더
│  ├─ network/     M02 버스·철도 GTFS, 보행 링크, 도로 그래프
│  ├─ demand/      M03 수요 점·벡터·파업 잔여 수요
│  ├─ planning/    M04 노선 생성·대수 배분
│  ├─ sim/         M05 시뮬레이터
│  ├─ routing/     M06 시민 경로
│  ├─ evaluation/  M07 지표·독립 검증·운행안 비교
│  └─ api/         M08
├─ web/            M10 화면 (기술 스택 미정이라 패키지 밖)
├─ tests/          모듈별 테스트 (tests/<모듈>/)
├─ configs/        시나리오 설정
├─ data/           데이터 인벤토리·참조 (원본은 커밋하지 않음)
├─ experiments/    실험 기록·운행안 보존
└─ notebooks/      EDA
```

모듈 폴더와 그 테스트 폴더는 짝입니다. `src/busstrike/<모듈>/` 안의 코드는
`tests/<모듈>/` 에서 검증하고, 테스트가 쓰는 입력은 `tests/<모듈>/fixtures/` 에 둡니다.
각 폴더의 파일별 역할은 해당 모듈의 `README.md`에 적습니다.

## 문서

| 문서 | 내용 |
| --- | --- |
| [M00 데이터 계약](docs/contracts/M00_DATA_CONTRACT.md) | 모듈 간 주고받는 데이터의 공통 규칙. **먼저 읽습니다** |
| [ADR-001](docs/adr/ADR-001.md) | 설계 결정 기록 |
| [AGENTS.md](AGENTS.md) · [CONTRIBUTING.md](CONTRIBUTING.md) | 브랜치·커밋·PR 형식 |
| [시뮬레이션 결과 독립 검증기](src/busstrike/evaluation/README.md) | 검증기 실행법·결과 읽는 법·검사 항목 |
| [검증기 구현 기록](docs/design/evaluation-verifier.md) | 재계산 알고리즘, 변조 검출, 명세와 구현의 차이 |
| [운행안 비교 평가 프로토콜](docs/design/evaluation-protocol.md) | 여러 운행안을 같은 조건에서 비교하는 절차 |
| [비교 평가 자료 인벤토리](docs/design/evaluation-data-inventory.md) | 필요 자료의 출처·확보 상태·라이선스 |

## 데이터 취급

원시 교통카드 자료를 저장소에 넣지 않습니다. 공개 저장소이고, 개인 이동 내역이기 때문입니다.

| 넣지 않는 것 | 넣는 것 |
| --- | --- |
| 원시 이용 내역 `VW_KSCC_*.csv` | 이용 조건을 충족한 **집계** |
| 카드 식별자 `TRCR_ID` | 출처 URL·수집일·라이선스 표시 |
| 인증키 | 재취득·재생성 방법 |
| `raw/`, `data/processed/`, `*.zip` | 합성 fixture |

`.gitignore`가 위 경로를 막고 있지만, **커밋 전에 직접 확인**합니다.

## 결과를 읽을 때

- 여기의 수치는 **시뮬레이션 결과**입니다. 실제 운행 확정·현장 성능·안전성 검증이 아닙니다.
- 운행안의 `selected`는 **실험상의 선택**이며 실제 배차나 운행 승인이 아닙니다.
- M05 시뮬레이터 수치는 **독립 검증기를 통과하기 전까지 "독립 검증 전 잠정"** 입니다.
  검증 전 수치로 비교 결론을 쓰지 않습니다.
- 합성 데이터로 낸 결과와 실제 자료로 낸 결과를 같은 표에 섞지 않습니다.
- 기능 구현 완료, 실험에서 개선 확인, 실제 운행 승인은 **서로 다른 상태**입니다.

## 함께 작업하기

1. 바꿀 것이 있으면 **Issue**를 먼저 엽니다.
2. `main`에서 브랜치를 만듭니다 — `<type>/<이슈번호>-<짧은-설명>`
3. PR을 열고 **다른 팀원 1명 이상의 확인**을 받은 뒤 `Create a merge commit`으로 병합합니다.

`main`에 직접 push하거나 이미 올린 이력을 고치지 않습니다.
형식은 [AGENTS.md](AGENTS.md), 예시는 [CONTRIBUTING.md](CONTRIBUTING.md)에 있습니다.
