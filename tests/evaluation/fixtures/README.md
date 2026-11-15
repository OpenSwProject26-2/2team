# tests/evaluation/fixtures — 검증기 입력 fixture

검증기(`src/busstrike/evaluation/`)는 **파일만** 읽는다. 여기 있는 것은 그 입력과,
명세 §5 손계산 기대표다.

## 디렉터리

| 경로 | 내용 | 저장소 추적 |
| --- | --- | --- |
| `handcalc/f1_expected.json`, `handcalc/f2_expected.json` | `docs/verifier_spec.md` §5 표를 **사람이 손으로 옮긴** 기대값. 검증기의 정답 출처 | 추적 |
| `f1/`, `f1-w9/`, `f1-j14/`, `f1-drain12/`, `f1-wj9/`, `f2/`, `f2-headway599/` | F1·F2 엔진 산출물(각 `scenario/` + `run/`). 작아서 커밋 | 추적 |
| `make_fixtures.py` | 위 F1·F2를 만드는 **엔진 호출 스크립트**. `sim`을 import하는 유일한 파일 | 추적 |
| `make_complex.sh` | 합성 complex 실행 결과 생성(harness CLI 호출) | 추적 |
| complex 산출물 | `events.jsonl` 14 MB(1천 명, 사건 89,703건) → **커밋하지 않는다** | 미추적 |

## 독립성 (`docs/verifier_spec.md` §1)

`make_fixtures.py`는 `sim`을 import한다. 이 파일은 **검증기 패키지 밖**(`tests/`)에 있고
검증기는 이것을 import하지 않는다. `src/busstrike/evaluation/` 의 `sim`·`opt` import 0건은
`tests/evaluation/test_import_independence.py`가 정적으로 검사한다.

## 재생성

B 엔진이 필요하다. 내 작업 브랜치에 B 코드를 섞지 않으므로 **별도 worktree**를 쓴다.

```sh
# 1) B worktree와 venv (저장소 루트에서)
git worktree add ../claude-sim-b origin/feat/5-simulation-core --detach
cd ../claude-sim-b
python3 -m venv .venv && .venv/bin/pip install -r sim/requirements.txt   # SimPy 4.1.2

# 2) F1·F2 (엔진 커밋 d18ee1e 기준)
PYTHONPATH="$PWD" .venv/bin/python ../claude/tests/evaluation/fixtures/make_fixtures.py \
    --out ../claude/tests/evaluation/fixtures

# 3) 합성 complex 1천 명 (저장소 밖으로)
SIM_WORKTREE="$PWD" PASSENGERS=1k sh ../claude/tests/evaluation/fixtures/make_complex.sh
```

`make_fixtures.py`가 `sim.harness run` 대신 엔진을 직접 호출하는 이유: 하네스의 정책은
`proposed-v0.2`(W=1200·J=3600) 하나뿐이고 drain은 `window_end_s + max_journey_s` 고정이라
F1(W=10·J=15·drain=20)·F2(W=300·J=600·drain=1100)를 CLI로 만들 수 없다. 출력 writer는
하네스와 같은 것을 쓰므로 파일 형식은 실제 실행과 같다.

## 생성 당시 기록

- 엔진 커밋 `d18ee1e` (`origin/feat/5-simulation-core`, PR #6 Draft)
- 명세 커밋 `ca643f6` (`docs/verifier_spec.md`)
- Python 3.14.3 / macOS(darwin) / SimPy 4.1.2
- complex 1천 명: 운행편 5,632 · 정류장 방문 168,960 · 수요 행 496 · 사건 89,703 · drain 36,000초
