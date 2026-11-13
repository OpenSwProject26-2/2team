# 함께 작업하는 방법

형식만 맞추고, 내용은 자유롭게 씁니다.

## 브랜치

```text
<type>/<이슈번호>-<짧은-설명>
```

예: `feat/12-demand-loader`, `fix/28-capacity-check`. 이슈가 없으면 번호를 뺍니다(`docs/readme-cleanup`).

## 커밋

```text
<type>(<scope>): <제목> (#이슈번호)
```

- **type**: `feat` `fix` `test` `docs` `refactor` `perf` `build` `ci` `chore` `revert` 중 하나
- **scope**: 바꾼 영역을 자유롭게 (예: `demand`, `simulation`, `routing`, `web`, `repo`)
- **제목**: 한국어로 자유롭게. 이슈가 없으면 `(#번호)`는 생략
- **본문**: 필요하면 자유롭게

예: `feat(simulation): 셔틀 배차 이벤트 처리 추가 (#12)`

## PR

- **제목**: 커밋과 같은 형식
- **본문**: 아래 틀을 쓰고, 내용은 자유롭게

```markdown
## 무엇을
## 확인
## 연결
Closes #12   ← 끝낸 이슈일 때만
```

- 다른 팀원 1명 이상이 확인한 뒤 **Create a merge commit**으로 병합합니다.

## 하지 않는 것

- `main`에 직접 push, `push --force`
- 원시 교통카드 자료(TAIMS, `VW_KSCC_*.csv`), 카드 식별자(`TRCR_ID`), 인증키, 데이터 파일(`raw/`, `data/processed/`, `*.zip`) 커밋
