# 협업 규칙 (CONTRIBUTING)

> 파업 대응 임시 셔틀 프로젝트 · 4인 팀 · 기준일 2026-11-06
>
> 목표: **작고 의미 있는 커밋을 자주, 고르게.** 커밋 수는 평가 요소지만, 커밋 하나하나가 "무엇을 왜 바꿨는지" 읽히는 것이 더 중요하다. 의미 없는 커밋(공백 수정 반복, 쪼개기용 커밋)은 오히려 감점 요인이 될 수 있다.

---

## 1. 브랜치 전략

```
main      ← 항상 실행 가능한 상태. 직접 push 금지, PR로만 병합
 └─ feat/opt-greedy-allocation     ← 기능 단위 작업 브랜치 (1~3일 안에 병합)
 └─ fix/sim-capacity-overflow
 └─ docs/readme-data-sources
```

- `main` 하나 + 짧게 사는 작업 브랜치 (GitHub Flow)
- 작업 브랜치는 **1~3일 안에 PR로 병합**한다. 오래 살수록 충돌이 커진다.
- 병합 후 작업 브랜치는 삭제한다.

### 브랜치 이름
`<type>/<scope>-<짧은-설명>` (영문 소문자, 하이픈)

| 예시 | 의미 |
| --- | --- |
| `feat/data-od-builder` | 데이터: OD 생성기 |
| `feat/sim-event-order` | 시뮬레이터: 사건 처리 순서 |
| `fix/data-coord-swap` | 데이터: 좌표 축 버그 수정 |
| `test/sim-hand-calc` | 시뮬레이터: 손계산 테스트 |
| `docs/contracts-schema` | 문서: 데이터 계약 설명 |

---

## 2. 커밋 메시지 규칙

[Conventional Commits](https://www.conventionalcommits.org/ko/) 형식을 따른다. **제목은 한국어**로 쓴다.

```
<type>(<scope>): <제목>

<본문: 무엇을, 왜 (선택, 72자 줄바꿈)>

<꼬리말: 이슈 번호, 공동 작업자 (선택)>
```

### type
| type | 언제 |
| --- | --- |
| `feat` | 새 기능 |
| `fix` | 버그 수정 |
| `test` | 테스트 추가·수정 |
| `refactor` | 동작 변화 없는 구조 개선 |
| `docs` | 문서 |
| `data` | 데이터 전처리 규칙·스키마 변경 (원본 데이터 파일은 올리지 않음) |
| `exp` | 실험 설정·결과 기록 |
| `perf` | 성능 개선 |
| `chore` | 빌드·설정·의존성 |
| `ci` | GitHub Actions 등 자동화 |

### scope (모듈 = 담당 역할과 연결)
| scope | 모듈 | 주 담당 |
| --- | --- | --- |
| `data` | 데이터 정제, OD 구축, 시나리오 생성 | A |
| `sim` | 이산사건 시뮬레이터, 운행 일정 | B |
| `verify` | 독립 검증기, 지표 계산 | B |
| `opt` | 후보 노선, 근사 평가기, 탐색, 기준안, MIP | C |
| `routing` | 시민 경로 탐색, GTFS | D |
| `api` | 백엔드 API | D |
| `web` | 프론트엔드 | D |
| `contracts` | 공통 데이터 형식 (변경 시 전원 리뷰) | 전원 |
| `docs` · `exp` · `ci` | 문서·실험·자동화 | 전원 |

### 좋은 예 / 나쁜 예
| 좋은 예 | 나쁜 예 |
| --- | --- |
| `feat(opt): 차량 1대 추가 시 충족 증가량 계산 함수 추가` | `update` |
| `fix(data): DH 좌표 위경도 축 뒤바뀜 교정` | `버그 수정` |
| `test(sim): 정원 2명 버스 3명 대기 손계산 예제 추가` | `test` |
| `refactor(sim): 승차 처리 로직을 Boarding 클래스로 분리` | `코드 정리함` |
| `exp(opt): 차량 4·8·12대 민감도 실험 설정 추가` | `실험` |

### 제목 규칙
- 50자 이내, 마침표 없음, "~추가 / ~수정 / ~교정 / ~분리"처럼 **무엇을 했는지**로 끝낸다
- 한 커밋 = 한 가지 변경. 제목에 "그리고"가 들어가면 커밋을 나눈다

### 꼬리말
- 이슈 연결: `Refs #12` (관련) / `Closes #12` (해결)
- 짝 작업: `Co-authored-by: 이름 <이메일>` → GitHub 기여 기록에 두 사람 모두 잡힌다
- AI 도구로 대부분 생성한 코드: 본문에 `AI 도움: Claude/Codex로 초안 생성 후 직접 검토` 한 줄 (투명성)

---

## 3. 커밋 크기와 빈도 (커밋 수 평가 대응)

**원칙: 커밋을 "부풀리지" 말고, 원래 나눠야 할 단위로 "제대로 나눈다."** 이것만 지켜도 커밋 수는 자연스럽게 많아진다.

### 한 커밋의 크기
- 변경 줄 수 **대략 20~200줄**을 기본으로 한다
- 아래는 **반드시 별도 커밋**으로 나눈다
  1. 기능 코드 / 그 기능의 테스트
  2. 리팩터링 / 기능 변경
  3. 형식(contracts) 변경 / 그 형식을 쓰는 코드 변경
  4. 의존성 추가(`chore`) / 그 라이브러리를 쓰는 코드
  5. 문서 / 코드

### 작업 하나를 커밋으로 나누는 예 (OD 생성기)
```
data(data): OD 스키마를 contracts에 정의
test(data): PSNG_NUM 합산 기준 OD 집계 테스트 추가
feat(data): 버스 행 필터링과 OD 집계 함수 구현
feat(data): 하차 결측 행 제외와 제외 사유 집계 추가
test(data): 하차 결측 제외 테스트 추가
docs(data): OD 생성 절차와 포함률 기준 README 추가
```
→ 하나의 기능이 6개의 읽히는 커밋이 된다.

### 빈도 목표 (1인 기준)
| 항목 | 목표 |
| --- | --- |
| 작업한 날 | 최소 1회 push (그날 작업을 로컬에만 두지 않기) |
| 주당 커밋 | **10~20개** (4주 × 4명 ≈ 160~320개) |
| 주당 PR | 2~4개 |
| 리뷰 | 주당 다른 사람 PR 2개 이상 리뷰·코멘트 |

### 금지
- 의미 없는 커밋: 공백·오타 하나씩 따로 커밋해서 개수 늘리기
- 몰아치기: 마감 전날 수천 줄 한 번에 커밋
- 다른 사람 작업을 내 이름으로 대신 커밋 (짝 작업은 `Co-authored-by` 사용)
- `main`에 직접 push, `force push`

### 기여 균형 확인
매주 금요일 점검 때 아래 명령으로 사람별 커밋 수를 확인하고, 한 사람에게 몰리면 다음 주 작업을 조정한다.
```bash
git shortlog -sn --since="1 week ago" main
```

---

## 4. Pull Request 규칙

- **PR 하나 = 기능 하나.** 변경 400줄 이내를 권장
- 리뷰어 **최소 1명 승인** 후 병합. `contracts/` 변경은 **전원 승인**
- 병합 방식: **Rebase and merge** 또는 **Create a merge commit** (Squash 금지 → 커밋 기록이 사라짐)
- 자동 테스트(CI) 통과 필수
- 리뷰는 24시간 안에. 질문·제안은 코멘트로 남긴다 (리뷰 코멘트도 협업 기록)

### PR 제목
커밋 제목과 같은 형식: `feat(opt): 탐욕 차량 배분 초기안 구현`

### PR 템플릿 (`.github/pull_request_template.md`)
```markdown
## 무엇을 바꿨나
-

## 왜
- 관련 이슈: #

## 어떻게 확인했나
- [ ] 단위 테스트 추가/통과
- [ ] 손계산 예제 또는 작은 데이터로 확인
- [ ] (해당 시) 인원 보존·정원 미초과 검증기 통과

## 리뷰어가 볼 곳
-

## AI 도구 사용
- [ ] 사용 안 함  - [ ] 초안 생성 후 직접 검토함 (검토한 부분: )
```

---

## 5. 이슈 관리

- 할 일은 모두 **GitHub Issue**로 만든다. 커밋·PR은 이슈 번호와 연결한다
- **GitHub Projects 보드**: `Todo → In Progress → Review → Done`
- 라벨

| 라벨 | 용도 |
| --- | --- |
| `role:A-data` `role:B-sim` `role:C-opt` `role:D-service` | 담당 역할 |
| `type:feat` `type:bug` `type:test` `type:docs` `type:exp` | 작업 종류 |
| `priority:high` | 이번 주 점검 항목 (계획 문서의 굵은 글씨 항목) |
| `contracts` | 데이터 형식 변경 (전원 확인 필요) |

### 이슈 템플릿 (`.github/ISSUE_TEMPLATE/task.md`)
```markdown
---
name: 작업
about: 구현할 작업
labels: ''
---
## 할 일

## 완료 조건
- [ ]

## 관련 모듈 / 담당
```

---

## 6. 코드·데이터 규칙

### 저장소 구조
```
contracts/   데이터 형식 (Pydantic)
data/        전처리 스크립트 (원본 데이터 X)
sim/  verify/  opt/  routing/  api/  web/
tests/       모듈별 테스트
experiments/ 실험 설정(yaml)과 결과 요약
docs/        문서 (strike-shuttle-overview.md 등)
```

### 데이터
- **원시 교통카드 자료(TAIMS zip, DX/DC/DD CSV)는 절대 커밋하지 않는다** (카드 식별자 포함)
- 대용량 정리 결과(OD CSV 등)도 커밋하지 않고, 공유 드라이브에 둔다. 저장소에는 **생성 스크립트 + 작은 합성 예제**만
- `.gitignore`에 아래를 넣는다
```gitignore
# 원본·대용량 데이터
raw/
*.zip
VW_KSCC_*.csv
data/processed/
*.parquet
# 환경
.env
.venv/
__pycache__/
node_modules/
# 실험 산출물 (요약만 커밋)
experiments/**/runs/
```
- API 인증키는 `.env`에만 두고 커밋하지 않는다

### 코드 품질 (자동 검사)
- Python: `ruff`로 형식·오류 검사, `pytest`로 테스트
- 커밋 전에 로컬에서 실행: `ruff check . && pytest -q`
- GitHub Actions로 PR마다 같은 검사를 자동 실행

---

## 7. 커밋 메시지 템플릿 설정 (선택)

저장소 루트에 `.gitmessage`를 두고 한 번만 설정하면, `git commit` 할 때마다 형식이 미리 채워진다.

```text
# <type>(<scope>): <제목 50자 이내>
#
# type: feat fix test refactor docs data exp perf chore ci
# scope: data sim verify opt routing api web contracts docs exp ci
#
# 본문: 무엇을, 왜 (72자 줄바꿈)
#
# Refs #
# Co-authored-by:
```
```bash
git config commit.template .gitmessage
```

---

## 8. 한 주의 리듬

| 요일 | 할 일 |
| --- | --- |
| 월 | 이번 주 이슈 배정, Projects 보드 정리 |
| 화~목 | 작업 브랜치에서 작게 커밋, 하루 1회 이상 push, PR 올리기·리뷰하기 |
| 금 | 주간 점검: 굵은 글씨 항목 완료 여부, `git shortlog`로 기여 균형 확인, 다음 주 범위 조정 |

---

## 9. 시작할 때 한 번만 할 일 (체크리스트)

- [ ] GitHub 저장소 생성, 4명 Collaborator 초대
- [ ] `main` 브랜치 보호: 직접 push 금지, PR 승인 1명 필수, CI 통과 필수, force push 금지
- [ ] 병합 옵션에서 **Squash merge 끄기**
- [ ] 라벨 생성, Projects 보드 생성
- [ ] `.gitignore`, `.gitmessage`, PR·이슈 템플릿, 이 문서(`CONTRIBUTING.md`) 커밋
- [ ] 각자 `git config user.name` / `user.email`을 **GitHub 계정 이메일과 같게** 설정 (다르면 기여 그래프에 안 잡힘)
