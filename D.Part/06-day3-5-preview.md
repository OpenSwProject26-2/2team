# D 파트 3~5일 차 — 합성 GTFS 검색·화면 미리보기

> 기준: 2026-11-10 GitHub `main`과 공개 Draft PR 확인. Issue [#18](https://github.com/OpenSwProject26-2/2team/issues/18) 작업. 본 산출물은 **개발용 합성 미리보기**이며 M08 API 또는 A/B/C 인수 완료가 아니다.

## 현재 다른 파트의 공개 상태

| 파트 | 확인한 GitHub 상태 | D의 연결 기준 |
| --- | --- | --- |
| A | [PR #13](https://github.com/OpenSwProject26-2/2team/pull/13) 수요·교통망 정제는 Draft. 정상 교통 GTFS는 후속 작업으로 명시 | A 실제 GTFS가 나올 때 운행일·정류장 ID/namespace·출처·누락 보고를 받아 교체 |
| A·공통 | [PR #16](https://github.com/OpenSwProject26-2/2team/pull/16) 합성 망·수요 fixture는 Draft. A/B/C의 일부 합성 연결은 확인했으나 정상 GTFS·D 연결은 미완료 | 정류장 ID A/B/C/D와 합성 좌표만 참고. GTFS 운행편은 D가 별도 작성 |
| B | [PR #6](https://github.com/OpenSwProject26-2/2team/pull/6) M05 시뮬레이터는 Draft. B 내부 `Trip`·일정 검사는 있으나 D용 GTFS/M07 독립 검증은 별도 | `compiled`를 `validated`나 시민용 시간표로 승격하지 않음 |
| C | [PR #4](https://github.com/OpenSwProject26-2/2team/pull/4), [#12](https://github.com/OpenSwProject26-2/2team/pull/12)는 `main` 병합. C 합성 `ServicePlan`은 `draft`, `trips=[]`; 근사 평가 7/9·6/9 | 후보·배분 참조만 사용. D 예제의 운행시각을 C 점수나 B 일정으로 해석하지 않음 |

## 구현한 미리보기의 의미

`examples/gtfs_preview/`에 정상 GTFS와 셔틀 GTFS를 분리했다. 정류장 ID는 A 공통 합성 fixture와 맞추었으나, 출발·도착 시각은 D가 손계산용으로 새로 만든 합성 값이다. `manifest.json`에 `(plan_id, revision)`, 서비스일, 출처, 독립 검증 전 상태를 고정한다. `normal:r1` 제외는 **이 예제의 검색 시연 조건**이며 M00 `strike_route_ids`의 최종 의미를 정하지 않는다.

| 입력 | 예상 검색 결과 |
| --- | --- |
| A→C, `2026-11-07T00:00:00+09:00`, 예제 제외 적용 | 합성 셔틀 00:02 A→B, 정상 버스 00:04 B→C, 00:05 도착 |
| 같은 요청, 예제 제외 해제(코드 테스트 전용) | 정상 버스 `normal:n1` 직행, 00:03 도착 |
| A→C, `2026-11-08T00:09:00+09:00` | 서비스일 11월 7일의 `24:10` 셔틀과 `24:12` 정상 버스, 11월 8일 00:13 도착 |
| D→C | 범위 안에 운행편이 없어 `NO_PATH` |
| 알 수 없는 정류장 | `OUT_OF_SCOPE` |
| 시간표 파일 또는 참조 누락 | `DATA_INCOMPLETE` |
| 다른 `plan_id/revision` | `DATA_VERSION_MISMATCH` |

검색은 운행편의 정류장 방문 순서와 시각을 읽고 가장 이른 도착을 고른다. 동일 정류장 1회 환승만 허용하고, 운행편별 정원·탑승 가능 여부를 계산하지 않는다. 최대 대기·최소 환승시간은 아직 확정되지 않아 적용하지 않았다. 따라서 24시간 가까운 대기가 포함될 수도 있으며 실제 시민 안내로 사용해서는 안 된다.

`preview.html`은 출발·도착·출발시각 입력과 결과·오류·경고를 표시한다. `preview_server.py`는 `127.0.0.1`에서만 이 HTML과 내부 `/__demo__/` 응답을 제공한다. 정식 M08 HTTP 상태 코드·필드명·기술 스택을 고정하지 않는다. 화면은 합성·시뮬레이션·독립 검증 전·좌석 비보장을 항상 보인다.

## 실행·검증

저장소 루트에서 Python 3.11 이상을 사용한다. 외부 라이브러리 설치는 필요 없다.

```powershell
py -3 D.Part/gtfs_preview.py A C 2026-11-07T00:00:00+09:00
py -3 D.Part/preview_server.py --port 8765
# http://127.0.0.1:8765/ 접속, 끝낼 때 Ctrl+C
py -3 -m unittest discover -s D.Part/tests -p test_*.py -v
py -3 -m ruff check D.Part
py -3 -m ruff format --check D.Part
```

테스트는 정상 직행, 제외 후 셔틀 환승, 자정 이후 서비스일, 오류 구분, 화면과 검색의 동일 버전 사용을 확인한다. 실제 A/B 데이터·공용 계약·M07 검증·브라우저 배포는 검사하지 않는다.

## 다음 팀 합의

1. **A:** 정상 교통 GTFS의 실제 제공 시점, 서비스일·노선/정류장 원천 namespace, 누락·품질 보고. PR #16의 합성 정류장과 실제 정류장 대응 여부.
2. **B:** `(plan_id, revision)`별 차량별 정차시각, GTFS 또는 동등한 산출물, `schedule_feasible`과 M07 `independently_verified`의 별도 참조·전달 시점.
3. **C:** `ServicePlan`의 후보 ID/배분을 B 일정·D 경로에 잇는 참조 방식. `draft`에서 `validated`·`selected`로 전환하는 소유자.
4. **전원:** `strike_route_ids`의 제거 의미, D06 도보·환승·최대 대기 정책, M08 외부 revision·오류 매핑, D08 웹/API 기술 스택. 결론은 ADR·공통 계약 PR로 기록.
