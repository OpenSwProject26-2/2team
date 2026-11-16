# D 운영자 지도 1차 로컬 구현 인수인계

> 후속 개발 순서와 PR 검토 범위는 [D팀 실행 계획](d-next-steps-and-pr-plan.md)을 따른다.

> **최신 갱신: 2026-11-16.** 아래 2026-11-15 기록은 당시 상태이며, 기능·검증 현황은 이 갱신을 우선한다. Git Commit/Push/PR은 수행하지 않았다.

> **최신 main 이관:** 이 문서의 D 코드가 `src/busstrike/api/`, `src/busstrike/routing/`, `web/operator/`, `web/citizen/`으로 복사되었고 테스트·합성 fixture가 `tests/`로 옮겨졌다. 원본 작업 폴더는 수정하지 않았다. 이관 결과의 테스트와 실행 경로 검증은 아래 최신 이관 검증 항목을 우선한다.

### 2026-11-16 Draft PR 직전 재검증

현재 브랜치는 `feat/d-dashboard-mvp`이며 `HEAD`는 확인 시점의 `origin/main`과 동일하다. D 테스트 디렉터리에 `__init__.py`를 넣어 루트 `unittest discover`가 D 테스트를 발견하도록 했다. HTTP 경계 테스트 3개를 추가해 실제 JSON 응답, 합성 셔틀 추가·제거에 따른 미충족 수요 변화, A 피드 미설정 오류를 확인한다.

| 검증 | 결과 |
| --- | --- |
| D API / routing | 각각 18개 / 28개 통과 |
| Windows 기본 `cp949` 전체 실행(수정 전) | 203개 실행, 실패 1·오류 225·건너뜀 6. 오류 225개는 모두 `UnicodeDecodeError`(평가 206, 시뮬레이션 19)이며 실패 1개도 검증 CLI 하위 프로세스의 동일 원인 |
| 현재 Windows checkout, `PYTHONUTF8=1` | D 테스트 발견 후 전체 271개 실행, 실패 26·오류 0·건너뜀 6. HTTP 테스트 추가 전 결과이며 최신 숫자는 최종 재실행 결과를 따른다 |
| Git 원본 LF 임시 환경, `PYTHONUTF8=1` | `main` 228개 통과·건너뜀 6; D 변경을 덧씌운 실행 271개 통과·건너뜀 6. HTTP 테스트 추가 전 결과 |
| 최종 D 변경 재실행 | API 18개·routing 28개 통과. 현재 Windows checkout 전체 274개: 실패 26·오류 0·건너뜀 6. Git 원본 LF 임시 환경의 동일 D 변경 전체 274개: 통과·건너뜀 6 |
| Ruff / 작업 트리 | D Python 22개 파일 `ruff check`·`ruff format --check` 통과, `git diff --check` 통과. 저장소 전체 Ruff는 check 87건·format 52개 파일 실패이며 원본 `main` 임시 환경에서도 같은 수치 |
| 로컬 서버/자료 | 운영자 HTML·5개 GET API가 HTTP 200, DEMO 7노선·지도 17 feature·07시 수요 80셀. 시민 HTML·합성 검색·정상 도달 가능 응답 확인. A 로컬 배포 ZIP 로더 707노선·13,151정차점·철도 9노선 |
| 브라우저 화면 | 브라우저 제어 도구가 Chrome의 현재 URL을 확인하지 못해 중단. 지도 타일, 마커, 클릭·강조, 수요 원, 신규 셔틀 UI의 시각 수락은 미실시 |

**남은 26개 실패의 traceback 분류:** `test_v1_to_v17_present_and_not_failing`, `test_every_fixture_passes_with_exit_zero`, `test_pass_is_not_the_same_as_fully_verified`에서 각각 7개 fixture(`f1`, `f1-w9`, `f1-j14`, `f1-drain12`, `f1-wj9`, `f2`, `f2-headway599`)가 실패한다(21개). `test_default_out_path_is_inside_the_run_directory`, `test_works_without_realization_file`, `test_writes_report_to_stdout_with_dash`, `test_report_has_every_required_field`, `test_above_limit_oracle_checks_are_not_run_not_pass`가 각각 1개씩 실패한다. 첫 집합의 traceback은 `V17`의 `demand_bins.csv`·`events.jsonl` SHA-256 불일치이고, 나머지는 그 검증 결과가 `pass=false`/종료 코드 1이 된 데 따른 기대값 불일치다.

Git 원본의 `f1/scenario/demand_bins.csv` SHA-256은 manifest 기대값 `f2632ed589387e67d49d7071cdea34e26b6d9687a6b4229a2f0e7d74390a7faf`와 같고, 현재 checkout은 `9ecb1812ab04f9ccccd707a5dec36958082e8033e3606fff93133f441d1aa153`이다. 7개 fixture 모두 `demand_bins.csv`와 `events.jsonl`의 기대값은 Git 원본과 같고 로컬 바이트의 CRLF만 LF로 바꾸면 Git 원본과 일치한다. 추적 중인 평가 fixture 62개 중 55개의 작업 트리 바이트가 Git 원본과 다른데 모두 CRLF 차이뿐이다. `core.autocrlf=true`가 재현 조건이며 Git 원본이나 기대 체크섬은 변경하지 않았다. `git archive`도 이 설정을 적용하므로 LF 임시 검증에는 `git -c core.autocrlf=false archive`가 필요했다. 임시 환경에만 원본 `HEAD`를 풀고 D 파일을 덧씌웠으며, Git 원본 fixture나 다른 팀 코드는 수정하지 않았다.

Windows PowerShell의 UTF-8 실행은 다음과 같다. 이 설정만으로는 현재 CRLF checkout의 체크섬 실패가 해결되지 않는다. 공용 `.gitattributes`의 `tests/evaluation/fixtures/** text eol=lf` 지정은 평가 fixture 소유자와 별도 검토가 필요하다. 기존 checkout에 적용하려면 안전한 재체크아웃 절차도 필요하므로 이번 작업에서는 수행하지 않았다.

```powershell
$env:PYTHONUTF8 = '1'
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

브라우저 수락이 남아 있으므로 지도 표시·상호작용은 API/코드 테스트 통과와 구분한다. A 집계 OD, B 셔틀 산출물, C 후보안, M07 완전 검증은 여전히 미연결이다. 합성 수요 계산과 시민 경로는 실제 운행 승인 또는 탑승 보장이 아니다.

### 2026-11-16 최신 main worktree 이관 검증

- `py -3 -B -m unittest discover -s tests/routing -p "test_*.py" -v`: **28개 통과**.
- `py -3 -B -m unittest discover -s tests/api -p "test_*.py" -v`: **15개 통과**.
- `PYTHONPATH=src`로 실행한 정상 교통 CLI가 합성 A→C 조회에서 `reachable=true`, `00:03` 도착, `transfer_count=0`을 반환했다. 미리보기·운영자 서버 진입점도 각각 localhost 포트 8778/8777에서 설정/대시보드 응답을 반환했다.
- `uv tool run --from ruff ruff check ...`: 통과. 같은 범위 `ruff format --check`: **19개 파일 포맷 통과**. OSRM 도로 fixture 생성기는 외부 API를 호출하지 않는 모듈 import만 확인했다.
- 최신 이관 후 브라우저 육안 수락은 하지 않았다. A 원본 ZIP 및 B/C/M07 자료는 이관하지 않아 실제 데이터 연동은 검사하지 않았다.

### 2026-11-16 추가: A 정차 관계 점선과 인원별 수요 원

- A GTFS에는 실제 선형 `shapes.txt`가 없지만 `stop_times.txt`에 활성 운행편의 정차 순서가 있다. `load_a_gtfs`는 지하철 9개 노선의 모든 활성 운행편에서 인접 역 쌍을 중복 제거한 `schematic_stop_links`를 제공한다. 2026-11-02 피드에서 인접 관계 509개를 얻었고 끝점이 모두 정류장 좌표와 연결됐다. 화면은 이를 보라색 점선으로 표시하고, 선택한 버스 노선은 대표 운행편 정류장 순서를 해당 종류 색의 점선으로 표시한다. 노란 점은 여전히 전체 활성 운행편의 정차점 집합이다. 점선은 실제 도로·철로 형상이 아니며 경로 거리·중복률·운행 가능 판정에 쓰지 않는다.
- 합성 DEMO의 거점별 전체·미충족 수요 원은 `passenger_count`가 증가하면 반경과 불투명도가 함께 증가한다. 작은 수요는 작고 연하게, 큰 수요는 크고 진하게 보인다. A 실제 OD는 여전히 미연결이다.

### 2026-11-16 추가: 정차점·수요 원·셔틀 선 표시

- 합성 DEMO는 거점 정류장 10곳만 둔 예제다. 노선별 3~5곳은 A 실제 정차점을 축약한 결과가 아니다. A GTFS에는 노선별 전체 활성 운행편 정차점 집합이 있으며, 노선 선택 시 이를 지도에 노란 원으로 강조한다. 일반 아이콘은 축소 지도에서 충돌 방지를 위해 일부 숨지만 오른쪽 전체 정차점 목록에는 모두 있다. A의 실제 선형 `shapes.txt`는 없다.
- 지도는 API의 80개 합성 OD 셀을 유지하면서 화면에서 정류장 거점 **10개의 원**으로 합산한다. 전체·출발·도착·미충족 표시를 구분하고, 인원이 많을수록 반경이 커진다. 노선 체크에 따른 미충족 인원 변경은 거점 단위 안쪽 원에 반영한다. A 실제 OD는 여전히 미연결이다.
- DEMO에서 보이는 정상 노선과 같은 좌표의 셔틀 선분은 화면에서만 투명 처리한다. 해당 정상 노선을 끄면 다시 나타나며, 다른 정상 노선도 겹치면 계속 숨는다. 현재 SH-01의 292개 도로 선분은 가상 시내버스 421과 모두 동일하게 저장돼 기본 화면에서 선 전체가 숨고, 시내버스를 끄면 나머지 표시 구간이 나타난다. 가상 마을버스와는 16개 선분이 정확히 일치한다. 이 숫자는 OSRM/OSM 예제 형상 비교이며 실제 중복 거리·버스 운행 공유·철도 동선 판정이 아니다. 합성 철도에는 검증된 궤도 선형이 없고 A GTFS에도 `shapes.txt`가 없으므로 실제 동선 투명 처리는 하지 않는다.

### 2026-11-16 추가: 정상 노선 체크와 A 정차점 조회

- 합성 `demo.json`에 시내·경기·마을·철도 노선의 정차 순서와 **가정 시간당 정원**, 수요 거점별 가상 OD 도착 정류장을 명시했다. `d_scenario.py`는 `active_normal_route_ids`와 기존 셔틀 입력으로 동일 수요 셀을 배분한다. 전체 수요는 변하지 않으며 미충족 인원과 안쪽 수요 원만 달라진다. 07시 기본값은 총 **984명, 가상 충족 515명, 미충족 469명**이다. 시내 노선을 끄면 충족 436명·미충족 548명, 경기 노선을 끄면 충족 415명·미충족 569명이다. 이 숫자는 A OD나 B 결과가 아니다.
- DEMO의 시내·경기·마을·지하철 체크만 **운행 가정+표시**를 함께 바꾼다. 파업·셔틀 선형, 정류장, 수요 원 체크는 표시만 바꾼다. A GTFS 모드에서는 모든 체크가 표시만 바꾸며 실제 수요 효과를 계산하지 않는다.
- A 배포 ZIP의 `routes.txt` `route_desc`를 그대로 읽어 2026-11-02 활성 노선 **707개 중 서울 시내 계열 374, 마을 242, 도시철도 9, 미확인 버스 82**로 표시한다. 경기버스로 분류된 노선은 이 배포본에 **없다**. `stops.txt` 좌표·이름을 가진 정류장·역은 13,151개(버스 12,693, 철도 458)다. 모든 활성 노선에 대표 운행편 **순서**와 전체 활성 운행편 **정차점 집합**이 있으며 해당 ID는 모두 정류장 목록과 연결된다. 오른쪽에서 두 목록의 이름·ID를 펼치고 선택하면 지도 위치로 이동한다. `종로13`의 대표 16곳과 전체 33곳, `1호선`의 대표 21곳과 전체 102곳처럼 차이가 있다. 전체 목록의 ID 정렬은 운행 순서가 아니며 실제 도로·철도 선형으로 해석하지 않는다.
- 자료 단위 테스트 15개는 정상 노선 체크에 따른 미충족 셀 변화, 잘못된 정상 노선 ID 거부, A 마을버스 분류·정차 순서 보존, 철도 분기 인접 역 추출을 확인한다. 이후 HTTP 경계 테스트 3개를 추가해 현재 API 테스트는 **18개**다.

## 2026-11-16 합성 도로 선형·시나리오 갱신

| 변경 파일 | 지금 하는 일 |
| --- | --- |
| `web/operator/build_demo_roads.py` | OSRM 공개 데모의 `driving` 경로 API를 수동·일회성 호출해 합성 버스 정차점 순서의 도로망 추정 선형을 저장. 1.1초 요청 간격, 스냅 120m 상한 적용 |
| `tests/fixtures/d_dashboard/road_shapes.json` | 버스 6개 노선의 OSM/OSRM 자동차 도로 추정 GeoJSON 좌표·원본 정차점·스냅 거리·출처를 저장. 현재 셔틀 SH-01은 293점·10,693.6m, 최대 스냅 약 80m |
| `src/busstrike/api/d_geo.py` | DEMO에서 원본 `demo.json`의 노선 좌표와 도로 fixture의 정차점 좌표가 정확히 일치할 때만 선형 적용; 스키마/ID/스냅/거리 검증; 두 파일 내용을 snapshot 지문에 반영. A GTFS에는 적용하지 않음 |
| `src/busstrike/api/d_scenario.py`, `src/busstrike/api/d_dashboard.py` | 가상 셔틀 정차 순서·시간당 차량 정원으로 합성 OD 수요를 결정론적으로 배분하는 `POST /api/d/demo/scenario/evaluate`. 차량 8대 초과·중복 정차점 등 오류 처리. B 시뮬레이션과 별개 |
| `web/operator/app.js`, `index.html` | 실제 지도에서 버스·역 자체 SVG 아이콘·호버 이름, 도로 추정 **실선**과 선형 미확인 **점선** 구별, 셔틀 제외/추가·가상 충족/미충족 갱신. 출처/OSRM/OSM 링크 표시. 중복 구간은 미분석으로 표시 |
| `tests/api/test_d_dashboard.py` | OSRM 출처·선형·스냅 상태와 합성 시나리오의 결정성·인원 보존·차량 상한 검사 |
| `web/README.md`, `docs/design/d-opensource-usage.md`, `d-operator-dashboard-plan.md` | 실행, 도로 선형 교체 형식, 실제 오픈소스 API·라이선스, 미검증 범위 설명 |

**도로 선형 추가 당시 확인 결과:** 2026-11-16 도로 선형 변경 직후 API 테스트 11개·기존 D.Part 테스트 28개·Ruff가 통과했다. 로컬 HTTP 8770에서 DEMO 지도 17개 feature 중 SH-01이 `osm_osrm_driving_estimate` 293점·10,693.6m로 반환됐다. 당시 정상 노선을 합성 수요 계산에 넣기 전 07시 가상 셔틀 단독 결과는 총 984명, 충족 157명, 미충족 827명이었다. **현재 기본 DEMO 결과는 위 최신 갱신을 따른다.** 브라우저에서 이전 버전의 DEMO/A 아이콘·시나리오 상호작용은 확인했지만 **이번 도로 선형·정상 노선 체크·정차점 상세 변경 이후의 브라우저 육안 확인은 컴퓨터 제어 연결 오류로 아직 못 했다.** JavaScript 모듈 문법은 Node REPL의 V8 파서로 확인했다. 지도 렌더링/클릭 최종 수락은 남아 있다.

**해석 제한:** 합성 정류장·OD·차량·노선번호는 실제 서울 교통 수요/노선이 아니다. 도로 선형만 공개 OSM 도로망에 대한 **자동차 경로 추정**이다. 우회가 크고 버스 통행 규제·회차·정차 가능성, A 실제 GTFS 노선과의 일치, B 운행시간표, M07 검증은 확인하지 않았다. 거리를 B 차량 운행거리 지표로 사용하지 않는다. A 배포 GTFS의 `shapes.txt` 부재로 실제 노선 선형은 표시하지 않는다. 새 셔틀은 브라우저 세션의 가상 정차점 연결선이고 점선이다. `road_shapes.json` 교체·추가 절차는 [실행 안내](../../web/README.md)에 있다.

> 2026-11-15 기준 **지도 1차 구현 진행 결과**. 전체 M06/M08/M10 완료 보고가 아니다. 로컬 현재 브랜치는 PR #27 이전 구조이며 새 `src/busstrike` 코드는 이 브랜치에 미커밋으로 추가됐다. Git 이력과 GitHub는 변경하지 않았다.

## 구현한 기능

- 원본 작업 폴더의 UI 참고 자산은 복사하지 않았다. 실행 화면은 `web/operator/index.html`이며, 레이아웃·색상·상단/좌측/우측/하단 패널을 재사용해 가상 SVG 지도를 MapLibre GL JS의 실제 서울 지도 배경으로 교체했다.
- OpenFreeMap 공개 `liberty` 스타일로 지도 팬/줌, 전체·선택 노선 보기, 노선 색/클릭·검색, 정류장·역 마커, 팝업, 시간대별 합성 수요 원, 레이어 ON/OFF, DEMO 공통 구간 레이어를 구현했다. **브라우저 육안 동작 검증은 아직 못 했다.**
- Python 표준 라이브러리의 로컬 API가 합성 fixture를 읽어 요약·노선·지도 GeoJSON·수요 GeoJSON·출처를 제공한다. 지도는 API 응답의 source만 갱신하며 파일 경로를 직접 읽지 않는다.
- A의 로컬 중첩 ZIP을 읽어 서비스일 활성 노선·정류장 좌표·`shapes.txt`가 있으면 대표 노선 선형을 GeoJSON으로 제공한다. 2026-11-11 배포본을 2026-11-02로 읽으면 **활성 노선 707개, 정류장 13,151개, GTFS shape 선형 0개**다. 선형 부재 시 실제 경로로 주장하는 직선은 만들지 않으며, 별도 지도 점선으로만 정차 관계를 표시한다.
- 실제 OD·B 시간표·C 후보안·M07 완전 검증·차량이 없는 항목은 숫자 0이나 가상 평가값 대신 `미연결`/`미계산`으로 표시한다. 실제 모드 OD API는 `DATA_INCOMPLETE`다.
- 로컬 `POST /api/d/sources/refresh`는 전체 A 피드 검증 뒤 스냅샷을 교체하고 실패 시 이전 정상본을 유지한다. 화면 `초기화`가 실제 모드에서 이를 호출한다.

**원본 UI 대비 변경:** 자체 SVG 지도와 150개 임의 소구역·임의 수요 충족 모델을 제거했다. 실제 지도 배경과 API가 공급하는 합성 80개 수요 셀을 넣었다. 운행 조건 토글과 충족률은 근거가 없어 활성화하지 않았고 `미계산`으로 바꿨다. A/B/C/M07 연결 상태, 출처 모드 선택, OSM/OpenMapTiles/OpenFreeMap 출처 링크를 추가했다. 대시보드의 큰 배치와 시각 언어는 원본을 따랐다.

## 파일별 역할

| 파일 | 역할 |
| --- | --- |
| `web/operator/index.html` | 원본 대시보드 배치를 보존한 실행 화면; 지도 컨테이너·출처 표시 |
| `web/operator/app.js` | MapLibre 지도, API 조회, 노선/정류장/수요 레이어와 목록·필터·패널 상호작용 |
| `web/operator/serve.py` | 로컬 실행 진입점 |
| `src/busstrike/api/d_geo.py` | 합성 fixture/A GTFS 지도 로더, 좌표·서비스일 검증, GeoJSON 변환 |
| `src/busstrike/api/d_dashboard.py` | localhost HTTP API, 읽기 전용 스냅샷과 새로고침 |
| `tests/fixtures/d_dashboard/demo.json` | 7개 가상 노선·10개 가상 정류장·80개 합성 수요 셀의 입력 |
| `tests/api/test_d_dashboard.py` | shape 유무·좌표 순서·서비스일·중첩 ZIP·갱신 실패 회귀 |
| `docs/design/d-opensource-map-stack.md` | 지도 기술 비교·라이선스·공간 분석 판단 |
| `docs/design/d-operator-dashboard-plan.md` | 사용자 승인 계획에 지도 기술/공간 분석 추가 |
| `web/README.md`, `README.md` | 실행·오픈소스 찾기 안내 |

기존 원본 폴더 및 별도 실 GTFS 작업은 수정·복사하지 않았다. `web/reference` UI 자산도 이 worktree에 포함하지 않았다.

## 실행·화면 사용

저장소 루트에서 `py -3 web/operator/serve.py` 실행 후 `http://127.0.0.1:8766/`로 접속한다. A ZIP 옵션과 조작법은 [web 실행 안내](../../web/README.md)에 있다. MapLibre 라이브러리와 OpenFreeMap 타일은 인터넷이 필요하다. API 키는 사용하지 않는다. 지도 배경만 실지리정보이고 합성 노선/정류장/수요는 가상 좌표다. 화면·API는 실제 운행 승인이나 승차 보장을 뜻하지 않는다.

## 로컬 API 현황

| 요청 | 현재 동작 |
| --- | --- |
| `GET /api/d/dashboard?mode=demo|real` | 출처·서비스일·노선 수·차량/평가 연결 상태 |
| `GET /api/d/routes?mode=…&q=…&kind=…` | 실제 route_id와 표시번호 분리된 목록 |
| `GET /api/d/map?mode=…` | 노선 `LineString`(shape 있을 때)·정류장 `Point` GeoJSON |
| `GET /api/d/demand?mode=demo&hour=07&demand_mode=origin` | 합성 수요 `Point` GeoJSON; 실제 OD 미연결은 오류 |
| `GET /api/d/sources` | demo/A/B/C/M07 연결 상태 |
| `POST /api/d/sources/refresh` | 로컬 A 파일 재검사 및 불변 스냅샷 교체 |

모두 **D 로컬 제안 API**다. 정식 M08 URL·HTTP 매핑·인증은 팀 합의가 필요하다.

## 검증 결과와 한계

- `py -3 -B -m unittest discover -s tests/api -p test_*.py -q`: 새 테스트 **6개 통과**. shape 유무, 좌표 순서, 서비스일, 중첩 ZIP, 갱신 실패 보존을 포함한다.
- `py -3 -B -m unittest discover -s tests/routing -p "test_*.py" -q`: 이관된 합성 경로 테스트 (아래 최신 이관 검증 결과 참고).
- `py -3 -m ruff check src/busstrike/api web/operator/serve.py tests/api/test_d_dashboard.py`: 통과. 같은 파일의 `ruff format --check`: 5개 파일 통과.
- 로컬 서버 `8766`에서 합성 요약 200, 지도 17 features(7 선·10 점), 07시 출발 수요 80 cells/506명 응답 확인.
- 별도 로컬 서버 `8767`에서 A 실제 ZIP 지도 707 routes/13,151 stops, 지정 서울 bbox 4,396 점·0 선 응답 확인. 실제 OD API는 HTTP 503 `DATA_INCOMPLETE` 확인.
- MapLibre 6.18.0 CDN과 OpenFreeMap `liberty` 스타일은 읽기 전용 HEAD 요청에 각각 HTTP 200으로 응답했다. 이는 브라우저 렌더링 검증을 대신하지 않는다.
- Windows 브라우저 제어가 현재 URL을 확인하지 못해 자동 중단됐다. **실제 브라우저에서 지도 타일·클릭·줌·레이어·툴팁을 시각적으로 확인하지 못했다.** 화면 구현 코드를 작성한 것과 브라우저 수락 테스트 통과는 구분한다.

남은 일은 브라우저 수락 검사, A OD 계약/지도 수요 연결, B 셔틀 GTFS·C 후보안·M07 평가 연결, 실제 방향별 선형/공통 도로 분석, 시민 경로·선택 상태 통합, 정식 M08/D08/D09 ADR이다. A의 기존 철도 시간 역전 3건은 이 지도 로더가 정정하지 않는다. A가 정정 피드·품질 보고를 제공해야 철도 경로 검색을 확대할 수 있다. 실제 중복률은 A/B 선형 부재로 계산하지 않았다.
