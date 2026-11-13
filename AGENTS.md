# AGENTS.md

버스 파업 대체노선 설계·시민 경로 안내 프로젝트입니다. 이 저장소에서 일하는 AI 도구는 아래만 지킵니다.

## 형식 (내용은 자유, 형식만 고정)

- 브랜치: `<type>/<이슈번호>-<짧은-설명>`
- 커밋·PR 제목: `<type>(<scope>): <한국어 제목> (#이슈번호)`
  - type: `feat` `fix` `test` `docs` `refactor` `perf` `build` `ci` `chore` `revert`
  - 이슈가 없으면 `(#번호)` 생략
- PR 본문: `## 변경 내용` / `## 테스트` / `## 관련 이슈` 세 칸

자세한 예시는 [CONTRIBUTING.md](CONTRIBUTING.md)를 봅니다.

## 금지

- `main`에 직접 push, `push --force`, 이미 올린 이력 수정
- 원시 교통카드 자료(TAIMS, `VW_KSCC_*.csv`), 카드 식별자(`TRCR_ID`), 인증키를 코드·로그·테스트·커밋에 넣기
- 데이터 파일(`raw/`, `data/processed/`, `*.zip`) 커밋
