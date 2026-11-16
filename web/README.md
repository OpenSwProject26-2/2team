# D 운영자 지도 개발 화면

실행 화면은 `web/operator/index.html`이다. 상단 카드·좌측 노선 목록·중앙 지도·우측 상세/레이어·하단 그래프 배치를 유지하고 가상 SVG 지도를 **MapLibre GL JS + OpenFreeMap의 실제 서울 지도 배경**으로 교체했다. 데이터 조회는 로컬 Python API를 사용한다. 원본 UI 참고 파일은 이 worktree에 포함하지 않았다.

## 실행

저장소 루트에서:

```powershell
py -3 web/operator/serve.py
```

브라우저에서 `http://127.0.0.1:8766/`을 연다. 설치 작업은 없으며 MapLibre 6.18.0 CDN과 OpenFreeMap 타일 로딩에 인터넷 연결이 필요하다. **합성 버스 선형은 미리 저장한 `road_shapes.json`을 읽으므로 화면 조회 시 OSRM API를 호출하지 않는다.** 지도 오류 시 화면에 연결 상태가 표시된다. `file://`로 HTML을 직접 열면 API가 없어서 실행되지 않는다.

A의 로컬 배포 ZIP을 지도 자료로 추가하려면, 원본 ZIP을 저장소에 복사하지 않고 다음처럼 경로만 전달한다:

```powershell
py -3 web/operator/serve.py --a-gtfs "C:\path\to\busstrike_data_release_20261111.zip" --service-date 2026-11-02
```

상단의 `합성 DEMO`/`A GTFS` 버튼으로 출처를 바꾼다. A GTFS 모드에는 실제 정류장·역 좌표와 노선 목록을 보여 준다. 해당 배포본에 `shapes.txt`는 없지만 `stop_times.txt`의 정차 순서는 있다. 지하철 9개 노선은 전체 활성 운행편의 인접 역을 점선으로 연결해 분기까지 개략적으로 보여 주고, 버스는 선택한 노선의 대표 운행편 정류장을 점선으로 연결한다. **이 점선은 정차 관계 개략도이며 실제 도로·철로 선형이나 거리 자료가 아니다.** 목록의 **시내버스·마을버스·지하철·미분류** 탭은 A의 `routes.txt` `route_desc`와 `route_type`을 기준으로 분류한다. 오른쪽 **대표 운행편 정차점**에는 `stop_times.txt` 순서대로 이름·ID가 보인다. **전체 활성 운행편 정차점**에는 상·하행/변형 운행편의 정차점을 중복 제거해 보여 준다. 전체 목록은 ID 정렬이며 운행 순서가 아니다. 항목을 누르면 지도 위치로 이동한다. 선택 노선의 전체 정차점을 노란 점으로 강조한다. A 자료를 갱신하고 `초기화` 버튼을 누르면 서버가 전체 ZIP을 검증한 뒤 새 스냅샷으로 교체한다. 검증 실패 시 이전 정상본과 오류 상태를 유지한다. 로컬 전용 서버이며 공용 M08 API나 배포 서버가 아니다.

2026-11-11 A 배포본을 서비스일 2026-11-02로 읽으면 활성 노선 707개(서울 시내 계열 374, 서울 마을 242, 도시철도 9, 분류 미확인 버스 82), 좌표가 있는 정류장·역 13,151개(버스 12,693, 철도 458)다. 707개 노선 모두 대표 순서와 전체 정차점 집합이 있고 해당 ID는 `stops.txt`에서 확인된다. 예컨대 `종로13`은 대표 16곳·전체 33곳, `1호선`은 대표 21곳·전체 102곳이다. **경기버스로 분류된 노선은 이 배포본에 0개**다. `route_desc`는 A 자료의 분류이며 D가 노선번호만 보고 추측한 값이 아니다.

합성 DEMO는 **가상 거점 10곳**만 넣은 작은 예제라 노선별 정차점이 3~5곳(가상 철도 4곳)뿐이다. A 정보를 3곳으로 줄이거나 실제 역을 임의로 대체한 것이 아니다. A GTFS 모드에서 노선을 고르면 해당 노선의 전체 정차점이 지도에 노란 점으로 강조된다. 축소 지도에서는 아이콘 충돌 방지로 일반 정류장 아이콘 일부가 숨으므로 확대하거나 오른쪽 정차점 목록을 사용한다. 이 A 배포본에는 `shapes.txt`가 없어서 정차점 사이의 실제 주행 선형은 알 수 없다.

## 조작과 의미

- 지도 확대·축소·드래그, `전체 보기`, `선택 노선 보기`, 노선 클릭/검색, 정류장·역 아이콘·팝업·호버 이름, 레이어 표시 전환을 제공한다. 합성 DEMO에서는 가상 정류장 순서를 OSRM/OSM **자동차 도로망**에 맞춘 버스 6개 노선 추정 선형을 실선으로 그린다. 합성 지하철 1개와 새로 만든 셔틀의 미확인 선형은 점선이다. A GTFS에서는 지하철 전체 정차 관계와 선택 버스의 대표 정차 관계를 점선으로, 선택 노선의 **전체 활성 운행편 정차점**을 노란 점으로 표시한다.
- A GTFS 정류장 점은 현재 지도 화면 범위 안의 좌표만 렌더링하고 이동·확대 후 갱신한다. 화면을 넓혀 지도를 보면 정류장과 역을 구별하기 쉽다.
- 시간대를 바꾸면 합성 수요 버블·합계·목록·그래프가 같은 시간대 자료를 사용한다. 계산 입력은 80개 가상 OD 셀이고 지도에서는 이를 **10개 거점별 원으로 합산**한다. 원 반경은 해당 거점 인원이 많을수록 커진다. `전체`에서는 연한 바깥 원이 전체 수요, 진한 안쪽 원이 미충족 수요다. `출발`·`도착`은 각각 그 수요만, `미충족`은 진한 원만 표시한다. 출발·도착과 충족·미충족은 B 평가나 실제 OD가 아니다.
- 오른쪽에서 셔틀을 제외·다시 추가하거나 정차점·차량 수를 넣어 새 가상 셔틀을 만들면 같은 시간대의 가상 배분을 다시 계산한다. 차량 8대 상한을 검사한다. 새 셔틀은 서버에 저장되지 않고 브라우저 세션에서만 유지된다.
- **합성 DEMO의 지도 표시 옵션**에서 시내·경기·마을·지하철을 끄면 해당 가상 정상 노선이 운행하지 않는 것으로 가정해 미충족 원과 충족률을 다시 계산한다. 예제 fixture에 노선별 정차 순서·가정 시간당 정원(시내 160명, 경기 100명, 마을 80명, 지하철 200명)을 명시했다. 이는 관측 정원·실제 운행편 수가 아니다. 전체 수요 원과 총인원은 그대로이고 진한 미충족 원이 커진다. 파업 노선·셔틀 선형·정류장·수요 원 체크는 **표시만** 바꾼다. A GTFS 모드의 모든 체크 역시 표시만 바꾸며 실제 수요 변화는 계산하지 않는다.
- DEMO 배분 순서는 정상 노선 `route_id` 정렬 후 셔틀 `route_id` 정렬이며, 이미 충족된 합성 OD 셀을 중복 계산하지 않는다. 순서별 노선 기여량은 가상 배분 결과일 뿐 최적 배차나 실제 승객 선택을 뜻하지 않는다.
- 합성 DEMO에서는 화면에 표시 중인 정상 노선과 **좌표가 정확히 같은 선분**에서 셔틀 선만 투명하게 숨긴다. 정상 노선을 체크 해제하면 해당 선분이 다시 나타난다. 다른 정상 노선도 같은 선분에 겹치면 그 노선까지 꺼야 나타난다. 이는 **지도 표시 효과**이며 실제 노선 중복 분석이 아니다. 현재 합성 철도는 검증된 궤도 선형이 없어 도로와 겹친다고 판정하지 않는다. A GTFS 모드에는 `shapes.txt`가 없어 이 효과를 적용하지 않는다.
- OSRM 결과는 자동차 도로를 따르는 **추정 표시 선형**이다. 가상 정류장 위치에 따른 큰 우회와 최대 약 80m의 스냅 오차가 있으며, 버스 통행 규제·회차·정차 가능성·실제 운행거리·탑승 가능성을 검증하지 않았다.

## 나중에 선형 자료 교체

합성 기본 노선은 `tests/fixtures/d_dashboard/demo.json`의 `routes[]`에 `route_id`, 순서대로 나열한 `[경도, 위도]` 정차점 좌표, 셔틀의 `stop_ids`를 둔다. 도로 선형은 별도 `tests/fixtures/d_dashboard/road_shapes.json`의 동일 `route_id` 항목에 `profile: "driving"`, **원본과 정확히 같은** `source_waypoints`, 도로를 따른 `coordinates`(GeoJSON `[경도, 위도]` 배열), 각 정차점의 `snap_distances_m`, 양수 `distance_m`를 둔다. 항목을 추가하면 최상위 `route_count`도 맞춘다. 시작 시 ID·좌표·스냅 거리 120m 상한과 스키마 버전을 검증하며 불일치하면 `DATA_VERSION_MISMATCH`를 낸다. 두 파일 내용은 `snapshot_id`에 반영된다.

가상 신규 셔틀을 영구 예제로 만들려면 위 두 파일에 노선을 넣고, `demo.json`에는 `kind: "shuttle"`, `stop_ids`, `assigned_vehicles`도 넣는다. 도로 선형이 없으면 `geometry_status: "synthetic"`와 정차점 연결선을 사용하되 화면에서 **점선·미확인**으로 표시한다. 실제 자료로 전환할 때는 A GTFS `shapes.txt`나 B가 제공할 검증된 방향별 선형·정류장 ID 계약을 우선 사용해야 한다. DEMO 도로 선형을 실제 피드에 섞지 않는다.

선형을 다시 **일괄 생성**하려면 `py -3 web/operator/build_demo_roads.py`를 수동 실행한다. 공개 OSRM 데모 서버의 비상업·초당 1회 제한에 맞춰 1.1초 간격으로 요청하고 기존 `road_shapes.json` 전체를 교체하므로, 수동으로 보정한 좌표가 있다면 먼저 보존해야 한다. 실제 배포용 외부 경로 서비스 채택은 별도 합의가 필요하다.

## 개발 API

`GET /api/d/dashboard`, `/api/d/routes`, `/api/d/map`, `/api/d/demand`, `/api/d/sources`는 `mode=demo|real`을 받는다. `/api/d/map`과 `/api/d/demand`는 GeoJSON `FeatureCollection`을 반환한다. `GET /api/d/routes?mode=real&kind=village`처럼 A 분류를 조회할 수 있으며 각 노선의 순서가 있는 `representative_stop_ids`와 순서 없는 `all_stop_ids`를 `/api/d/map`의 정류장 이름·좌표와 연결한다. `POST /api/d/demo/scenario/evaluate`는 합성 시나리오의 `hour`, 셔틀 `routes`, 체크된 `active_normal_route_ids`를 받아 정원 배분을 재계산한다. 이 값이 빠지면 정상 노선 운행을 가정하지 않는다. `POST /api/d/sources/refresh`는 로컬 A GTFS를 다시 읽는다. 필드와 HTTP 상태는 **D 로컬 제안**이며 M08 공용 계약으로 확정되지 않았다.

## 검사

```powershell
py -3 -B -m unittest discover -s tests/api -p test_*.py -v
py -3 -m ruff check src/busstrike/api web/operator/serve.py web/operator/build_demo_roads.py tests/api
py -3 -m ruff format --check src/busstrike/api web/operator/serve.py web/operator/build_demo_roads.py tests/api
```

한국어 Windows의 기본 `cp949`에서 전체 테스트의 UTF-8 fixture를 읽으려면 PowerShell에서 `$env:PYTHONUTF8 = '1'`을 설정한 뒤 `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`를 실행한다. 단, 현재 저장소는 `core.autocrlf=true`로 checkout하면 평가 fixture의 LF가 CRLF로 바뀌어 SHA-256 검사가 실패한다. `PYTHONUTF8`은 이 줄바꿈 문제를 해결하지 않는다. Git 원본 LF를 보존한 임시 환경에서는 `main`과 D 포함 전체 테스트가 통과했으며 재현 근거와 공용 `.gitattributes` 개선안은 [D 인수인계](../docs/design/d-operator-dashboard-handoff.md)에 기록했다. 평가 fixture와 기대 체크섬은 변경하지 않았다.

오픈소스와 출처 표기: [실제 사용 근거](../docs/design/d-opensource-usage.md), [지도 기술 검토](../docs/design/d-opensource-map-stack.md). 현재 MapLibre는 CDN에서 불러오며 지도 타일은 OpenFreeMap 공개 서버에 의존한다. OSM/OpenMapTiles/OpenFreeMap/OSRM 출처는 지도 화면 안에 표시한다.

## 시민 경로 미리보기

경로 검색 프로토타입은 합성 GTFS만 읽으며, 실제 A/B 시간표·보행·철도 환승·좌석 가능성을 판정하지 않는다. 저장소 루트에서 별도의 localhost 화면을 실행한다:

```powershell
py -3 web/citizen/serve.py
```

브라우저에서 `http://127.0.0.1:8765/`를 연다. 합성 입력은 `tests/fixtures/routing/gtfs_preview/`에 있다. 정상 버스 전용 도달 가능 조회는 다음처럼 실행할 수 있다:

```powershell
py -3 -m pip install -e .
py -3 -m busstrike.routing.normal_reachability synthetic-demand-1 A C 2026-11-07T00:00:00+09:00
```

`/__demo__/` 응답은 개발용 미리보기일 뿐 M08 공개 API 계약이 아니다. 경로 검색과 API 테스트:

```powershell
py -3 -B -m unittest discover -s tests/routing -p "test_*.py" -v
py -3 -B -m unittest discover -s tests/api -p "test_*.py" -v
```
