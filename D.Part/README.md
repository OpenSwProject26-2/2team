# D.Part — 시민 경로 안내 및 운영자 서비스

## 담당 역할

버스 파업 대응 임시 셔틀 프로젝트의 서비스 영역을 담당한다.

- `routing/`: 정상·파업·셔틀 운행 조건을 반영한 시민 이동 경로 검색
- `api/`: 시민 경로·운영자 평가 결과 API 제공 (구현 스택은 팀 결정 대기)
- `web/`: 시민 경로 조회·운영자 대시보드 (구현 스택은 팀 결정 대기)

## 현재 일정과 산출물

| 시점 | D 파트 목표 | 상태 |
| --- | --- | --- |
| 1~2일 차 | GTFS 입력 요구사항 및 API 응답 형식 정의 | 명세 작성 단계 |
| 3~5일 차 | 예제 GTFS 경로 검색 및 화면 골격 | Issue #18 개발용 합성 미리보기 구현·팀 검토 대기 |
| 1주 차 통합 | 평가 결과 웹 화면 표시 | 예정 |
| 2주 차 | 정상 교통 데이터·실제 피드 연결 | 예정 |

## 1~2일 차 설계 문서

1. [GTFS 입력 요구사항](01-gtfs-input-requirements.md) — 경로 탐색 입력 파일과 검증 기준
2. [API 요청·응답 형식](02-api-response-format.md) — 검색·평가 API 필드와 JSON 예제
3. [인수인계 및 팀 합의 체크리스트](03-day1-2-handoff-checklist.md) — A/B/C에 확인할 입력·미결정 사항
4. [M00·A/B/C 정합성 검토](04-m00-abc-integration-review.md) — 공개 구현 상태와 D 입력 게이트
5. [C→D 수락 사례](05-c-to-d-acceptance-cases.md) — 후속 합성 contract test 사례
6. [3~5일차 합성 미리보기 현황](06-day3-5-preview.md) — 실행 방법·테스트·A/B/C 연동 전제
7. [정상 교통 도달 가능 첫 전달물](07-normal-reachability-handoff.md) — A에 보낼 합성 조회 함수·응답과 남은 입력
8. [ADR-002 제안](ADR-002-normal-reachability-preview.md) — 정상 조회 응답 의미와 팀 결정 대기 항목

이 문서의 ID·API 경로·JSON 필드 중 확정되지 않은 값은 **제안 형식**이며, 팀 공통 `contracts/` 모델이 존재하면 그 모델을 우선한다.

## 예정된 기능 흐름

1. A가 제공하는 정상 교통 GTFS를 경로 탐색 엔진이 읽는다.
2. 파업 시나리오의 운행 중단 노선을 제외한다.
3. B가 생성한 임시 셔틀 GTFS를 적용한다.
4. 팀이 확정한 API 서버가 경로 결과를 JSON으로 반환한다.
5. 팀이 확정한 웹 기술로 예상 이동시간·환승·셔틀 이용 여부를 표시한다.
6. 운영자 화면은 B/C에서 제공된 운행안 평가 요약을 표시한다.

## 작업 시 주의

- 원시 교통카드 파일과 개인 식별자를 GitHub에 올리지 않는다.
- 다른 담당자 모듈 및 공통 `contracts/`는 임의로 변경하지 않는다.
- `main`에 직접 push하지 않고 기능 브랜치에서 PR을 제출한다.
- 코드 작성·검증을 하지 않았다면 완료로 표시하지 않는다.
- AI가 초안을 작성했을 경우 실제 검토 후 Commit 설명에 사용 내역을 기록한다.

## 첫 번째 실행 가능한 실습: GTFS 입력 기본 검사

`gtfs_header_check.py`는 GTFS 폴더 또는 ZIP 안에 필수 파일과 CSV 헤더가 있는지 확인한다.
**실제 파업 노선·시간표의 유효성이나 경로 검색 가능 여부까지 검증하지는 않는다.** 원시 교통카드 데이터는 사용하지 않는다.

```bash
# 저장소 최상위에서 실행 (GTFS 파일은 로컬에만 보관)
python D.Part/gtfs_header_check.py /로컬/GTFS_폴더
python D.Part/gtfs_header_check.py /로컬/gtfs.zip

# 합성 데이터만 사용하는 자동 테스트
python -m unittest discover -s D.Part/tests -p "test_*.py" -v
```

- **통과 시:** `[통과] GTFS 필수 파일·헤더 기본 검사 완료`
- **실패 시:** 누락된 파일 또는 열만 표시하고 종료 코드 `1` 반환
- **제약:** 기본 파일·헤더만 검사하므로 날짜별 운행, 참조 ID, 정류장 좌표, 시간값 등은 후속 검증이 필요함
- **다음 단계:** A·B가 제공하는 입력과 형식을 합의한 후 실제 경로 탐색기를 구현. `contracts/`를 임의로 만들거나 수정하지 않음

## 3~5일 차 개발용 합성 경로 미리보기

이 미리보기는 A Draft PR #16과 같은 합성 정류장 ID A/B/C/D를 사용하지만, **GTFS 운행편·시각은 D가 별도로 만든 가상 예제**다. B의 실제 일정 또는 M07 독립 검증 결과가 아니다.

```powershell
# 저장소 루트에서: 프레임워크·패키지 설치 없음
py -3 D.Part/gtfs_preview.py A C 2026-11-07T00:00:00+09:00
py -3 D.Part/preview_server.py
# 브라우저에서 http://127.0.0.1:8765/ 열기

py -3 -m unittest discover -s D.Part/tests -p test_*.py -v
py -3 -m ruff check D.Part
py -3 -m ruff format --check D.Part
```

기본 예제는 `normal:r1`을 **미리보기에서만 제외**하고 합성 셔틀 A→B, 정상 버스 B→C의 환승을 보여 준다. 화면과 CLI 결과에는 `plan_id`, `plan_revision`, `plan_mode=simulation`, `not_independently_verified`를 함께 보인다. `preview_server.py`의 `/__demo__/` 경로·JSON은 **M08 API 계약이 아니다**.

현재 지원 범위는 `calendar_dates.txt`에 기록된 단일 서비스일, 정확한 정차 시각, 동일 정류장 최대 1회 환승이다. 도보·환승 최소시간·최대 대기·좌석·실제 파업 영향 판정은 팀 합의와 A/B 입력을 기다린다. 자세한 현재 상태는 [3~5일차 문서](06-day3-5-preview.md)에 적었다.

## A 전달용 정상 교통 조회 초안

`normal_reachability.py`는 위 합성 예제의 **정상 버스 GTFS만** 읽어 정류장 간 도달 가능성을 요약한다. 셔틀 파일이 없어도 작동한다. 조회 ID·도착시각·환승 횟수·정상 시간표 버전을 반환하고, 보행시간은 미계산 `null`로 둔다.

```powershell
py -3 D.Part/normal_reachability.py synthetic-demand-1 A C 2026-11-07T00:00:00+09:00
```

실제 교통카드↔GTFS ID 매핑, 지하철·보행 연결, 잔여 수요 판정 정책은 [A 전달 문서](07-normal-reachability-handoff.md)에 필요한 입력으로 정리했다.
