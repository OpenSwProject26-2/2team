# 2team

버스 파업 대체노선 설계·시민 경로 안내 프로젝트.

## 구조

```text
2team/
├─ README.md · AGENTS.md · CONTRIBUTING.md · .gitignore · pyproject.toml
├─ docs/
│  ├─ contracts/   M00 데이터 계약
│  ├─ adr/         ADR 모음
│  └─ design/      설계 문서
├─ src/busstrike/
│  ├─ contracts/   M00 공용 DTO
│  ├─ ingest/      M01 원천 로더
│  ├─ network/     M02 버스·철도 GTFS, 보행 링크, 도로 그래프
│  ├─ demand/      M03 수요 점·벡터·파업 잔여 수요
│  ├─ planning/    M04 노선 생성·대수 배분
│  ├─ sim/         M05 시뮬레이터
│  ├─ routing/     M06 시민 경로
│  ├─ evaluation/  M07 지표·독립 검증·지자체안 비교
│  └─ api/         M08
├─ web/            M10 화면 (기술 스택 미정이라 패키지 밖)
├─ tests/          모듈별 테스트 (tests/<모듈>/)
├─ configs/        시나리오 설정
├─ data/           데이터 인벤토리·참조
├─ experiments/    실험 기록
└─ notebooks/      EDA
```

## 설치·실행

```bash
pip install -e .
python3 -m unittest discover -s tests -t . -v
python3 -m busstrike.planning --input tests/fixtures/planning/synthetic.json
```

협업 규칙은 [AGENTS.md](AGENTS.md)와 [CONTRIBUTING.md](CONTRIBUTING.md)를, 구조 배경은 이슈 #26을 봅니다.
