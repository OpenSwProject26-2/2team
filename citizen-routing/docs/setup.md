# 시민 길찾기 설치와 데이터 계약

## 범위와 자료

이 서비스는 `citizen-routing/` 안에서 독립 실행됩니다. 기존 `web/operator`, `web/citizen`, `src/busstrike` 파일을 수정하지 않습니다. A 배포본 `busstrike_data_release_20261111.zip`의 내부 `gtfs_bus_rail_combined.zip`을 읽기 전용으로 추출·검증합니다. 제공본에는 버스·지하철 운행편, 정류장, 서비스 날짜, 정차 시간, 환승표가 있고 `shapes.txt`는 없습니다. OTP는 OSM 보행망과 정류장 위치를 사용합니다. 실제 주행 선형이 없는 구간의 지도를 실제 도로 경로처럼 보정해 그리지 않습니다.

B/C 운행안은 **GTFS 내보내기 결과**(폴더 또는 ZIP)로만 받습니다. 필수 파일은 `agency.txt`, `routes.txt`, `trips.txt`, `stops.txt`, `stop_times.txt`, `calendar.txt` 또는 `calendar_dates.txt`입니다. `src/busstrike/sim/gtfs.py`의 `export_gtfs(timetable, service_date, out_dir, stop_coords)`를 사용할 수 있으나, 모든 영업 정류장의 **실제 좌표**를 `stop_coords`로 제공해야 합니다. B/C의 `plan_id@revision`, 실제 배차·운행일, C 정류장과 A 정류장의 좌표 검증은 데이터 제공자가 책임져야 합니다. 이 저장소에서 확인한 B/C 자료에는 확정된 시민 서비스용 영업 시간표와 검증된 A/C 정류장 대응표가 없습니다. 합성 자료를 사용할 경우 별도 실험으로 명확히 표기하세요.

파업 노선은 A GTFS `routes.txt`의 `route_id`를 명시한 JSON으로 받습니다. 예:
```json
{"affected_route_ids":["실제_A_GTFS_노선_ID"]}
```
미지정 시 파업 노선을 추정하지 않습니다. 입력된 노선은 `routes.txt`, `trips.txt`, `stop_times.txt`에서 제거되어 **탐색 그래프 자체에서 빠집니다**. 정상 노선과 지하철은 남습니다. 미확인 ID는 오류로 처리합니다. 파업 적용 날짜와 운행안 날짜가 맞는지는 입력 자료로 확인해야 합니다.

## 실행

Python 3.10 이상과 Docker Desktop이 필요합니다. 셔틀을 포함한 실제 검색을 하려면 해당 날짜의 B/C GTFS, 서울 권역 OSM PBF가 필요합니다. OSM은 해당 지역의 OpenStreetMap 추출본을 저장소 밖에서 준비하세요. 원본은 읽기 전용이며 `runtime/` 안에만 사본이 생성됩니다. ZIP·PBF·그래프는 Git에 넣지 않습니다.

PR이 `main`에 병합되기 전에는 기능 브랜치를 받아야 합니다. 새 폴더에서 시험하려면 PowerShell에서:

```powershell
git clone --branch feat/citizen-routing --single-branch https://github.com/OpenSwProject26-2/2team.git 2team-citizen-routing
Set-Location 2team-citizen-routing/citizen-routing
```

A 배포 ZIP과 서울 권역 OSM PBF는 저장소와 GitHub 릴리스에 포함되지 않습니다. 팀 내에서 별도로 전달받아 두 파일의 실제 경로를 확인하세요. 이 자료가 없어도 저장소의 테스트용 GTFS fixture로 단위 테스트는 실행할 수 있지만, OTP 그래프와 실제 경로 검색은 준비할 수 없습니다.

이어서 `citizen-routing`에서 실제 파일 경로를 넣어 실행합니다:

```powershell
py -3 -m backend.prepare --existing "C:\path\to\busstrike_data_release_20261111.zip" --osm "C:\path\to\seoul.osm.pbf"
py -3 -m pip install --target runtime/python-libs osmium==4.3.2
py -3 -B -m backend.build_places
py -3 routing-engine/build_graph.py
docker compose -f routing-engine/docker-compose.yml up -d otp
py -3 -m backend.server
```

건물·시설·역 이름은 준비 단계에서 복사된 `runtime/otp/streets.osm.pbf`로 로컬 색인을 만듭니다. OSM에 없는 장소도 지도 클릭 후 '이 지점의 좌표'를 선택해 지정할 수 있습니다. 색인 결과는 준비한 PBF의 이름과 주소 범위에 따라 달라지며, 운영 중 외부 장소 검색 API 호출은 하지 않습니다.

위 명령은 A GTFS만 있는 경우입니다. 예시 경로를 실제 파일 경로로 바꾸세요. 확정 B/C 셔틀 GTFS가 있다면 `backend.prepare` 명령에 `--shuttle "C:\path\to\b_exported_gtfs"`를, 파업 노선 JSON이 있다면 `--strike-routes "C:\path\to\strike.json"`을 추가합니다. 셔틀 자료가 없으면 화면에서 셔틀 옵션이 비활성화됩니다. 파업 대상 목록이 없다면 파업 노선을 제외하지 않으므로 이 결과를 파업 대응 경로로 해석하면 안 됩니다. 초기 그래프 구축은 입력 크기에 따라 메모리와 시간이 필요합니다. `runtime/otp/graph.obj`가 생성되지 않으면 OTP 로그를 확인하세요.

운행안 변경 시 시민 서버와 OTP를 정지하고 변경된 GTFS로 `backend.prepare`를 다시 실행한 뒤 `py -3 routing-engine/build_graph.py`를 재실행합니다. OSM 입력도 바뀌었다면 `py -3 -B -m backend.build_places`로 장소 색인을 다시 만드세요. 이후 OTP와 시민 서버를 다시 시작합니다. 그래프 입력 버전이 준비된 GTFS·OSM과 다르면 시민 API는 `GRAPH_NOT_BUILT`을 반환합니다. `manifest.json`의 version은 준비 입력의 식별자이며 운영 승인 상태가 아닙니다. 운영자 대시보드와의 자동 동기화는 아직 없습니다.

## 화면 사용

1. [http://127.0.0.1:8775/](http://127.0.0.1:8775/)을 열고 출발지·목적지 입력란에서 건물·시설·정류장 이름을 검색해 결과를 선택합니다.
2. 지도에서 위치를 지정하려면 지도를 클릭한 뒤 표시된 장소 후보를 출발지나 목적지로 선택합니다. 임의 위치는 이 지점의 좌표로 지정합니다. 주요 시설은 확대 수준에 따라 단계적으로 나타나며, 지도에 보이지 않는 시설도 검색란에서 이름으로 찾을 수 있습니다.
3. 준비된 GTFS의 운행 날짜 안에서 출발 시각을 정하고 '경로 찾기'를 누릅니다. 셔틀 GTFS가 있을 때만 대체 셔틀 포함 옵션을 사용할 수 있습니다.
4. 추천 경로 카드의 얇은 구간 막대와 상세 목록에서 이동수단 아이콘, 구간별 소요시간, 출발지·도착지, 도보·대기 시간과 환승 횟수를 확인합니다. 카드를 선택하면 해당 경로가 지도에 강조됩니다.
5. 경로 목록 위 드롭다운에서 최적 경로순·최소 시간순·최소 환승순·최소 도보순을 선택합니다. 각 경로의 최적·최소 도보 등 분류 표시는 정렬 기준을 바꿔도 유지됩니다.

버스 구간은 준비된 A GTFS routes.txt의 route_desc를 기준으로 마을버스, 시내버스, 경기버스, 미분류로 표시합니다. 간선, 지선, 순환, 광역 노선은 시내버스로 분류하고 근거가 없으면 미분류로 둡니다. API 버스 leg의 bus_category는 village, city, gyeonggi, unclassified 중 하나이며, 다른 이동수단은 null입니다.

지도와 추천 카드의 색은 정상 시내버스 파랑, 경기버스 주황, 마을버스 초록, 지하철 보라, 대체 셔틀 남색으로 구분합니다. 파업 노선은 경로 검색에서 제외되어 시민 지도에 표시되지 않습니다.

## API

```http
GET http://127.0.0.1:8775/api/citizen-routing/plan?origin_lat=37.558&origin_lon=127.001&destination_lat=37.529&destination_lon=126.965&departure_at=2026-11-02T07%3A30%3A00%2B09%3A00&include_shuttle=false
```

응답 구조(아래 값은 필드 형식 예시이며 **검색 결과가 아닙니다**):

```json
{
  "version": "<input-hash>",
  "source_kind": "provided_unverified",
  "excluded_route_ids": [],
  "count": 1,
  "routes": [{
    "id": "<computed-id>",
    "start": "<OTP ISO time>",
    "end": "<OTP ISO time>",
    "duration_seconds": 0,
    "walk_seconds": 0,
    "wait_seconds": 0,
    "transit_seconds": 0,
    "transfers": 0,
    "uses_shuttle": false,
    "legs": [{
      "kind": "walk",
      "mode": "WALK",
      "route_id": null,
      "route_name": null,
      "bus_category": null,
      "from": {"name": "<place>", "lat": 0, "lon": 0, "stop_id": null},
      "to": {"name": "<place>", "lat": 0, "lon": 0, "stop_id": null},
      "start": "<OTP ISO time>",
      "end": "<OTP ISO time>",
      "duration_seconds": 0,
      "distance_meters": 0,
      "geometry": []
    }]
  }]
}
```

`include_shuttle=false`는 OTP GraphQL의 transit filter로 SHUTTLE 피드 노선을 **탐색 단계에서 제외**합니다. 셔틀 포함 검색은 셔틀을 우선 강제하지 않습니다. 화면의 기본 정렬은 최적 경로순(소요시간 + 환승당 10분 + 도보시간의 50%)이며 드롭다운에서 최소 시간순·최소 환승순·최소 도보순을 선택할 수 있습니다. OTP에 경로가 없으면 빈 목록 또는 명시적 `NO_PATH` 오류를 반환합니다. 서버가 OTP에 접속하지 못하면 `OTP_UNAVAILABLE`을 반환하며 가상 경로를 만들지 않습니다.

통합 장소 검색은 `GET /api/citizen-routing/places?q=...`이며 OSM 건물·시설과 GTFS 정류장을 함께 반환합니다. 기존 정류장 전용 `GET /api/citizen-routing/stops?q=...`도 유지합니다. 지도 어느 곳이든 클릭하면 출발지·목적지 선택 창이 열립니다. 지도에 표시된 시설과 `GET /api/citizen-routing/nearby?lat=...&lon=...`의 주변 OSM 장소 후보를 보여주며, 장소가 없거나 임의 지점이 필요하면 '이 지점의 좌표'를 명시적으로 선택할 수 있습니다. MapLibre GL JS와 지도 스타일 파일은 신규 서비스의 vendor 디렉터리에서 제공하며, 실제 배경 타일·글꼴·스프라이트는 OpenFreeMap 인터넷 연결이 필요합니다.

지도에 표시할 시설은 `GET /api/citizen-routing/map-places?west=...&south=...&east=...&north=...&zoom=...`로 현재 화면 영역에서 가져옵니다. 축척에 따라 장소 간격을 조정해 겹침을 줄입니다. 시민 전용 지도 스타일은 `GET /citizen-map-style.json`에서 제공하며, 저장소에 포함된 원본 Liberty 스타일 파일을 덮어쓰지 않습니다.


### 일반 시설 POI 표시

시민 지도는 원본 `frontend/vendor/openfreemap-liberty.json`을 수정하지 않고 `/citizen-map-style.json`에서 별도 스타일을 만듭니다. 교통시설 레이어 poi_transit과 정류장·역 선택 기능은 유지합니다.

| 확대 수준 | 일반 시설 표시 |
| --- | --- |
| 11.5부터 | 로컬 OSM의 병원·대학교·공공기관·박물관 등 주요 시설 |
| 12.7부터 | 학교·도서관·의원·슈퍼마켓 등 교육·생활 시설 |
| 13.5부터 | 교회·사찰 등 종교시설 |
| 14부터 | OpenFreeMap 벡터 타일의 주요 공공·문화시설. 이후 rank에 따라 학교·병원·종교시설을 추가 |
| 16부터 | 음식점·카페·편의점 등 소규모 시설을 순차적으로 추가 |
| 18부터 | 기타 상점과 세부 시설명을 확대하면서 추가 |

실제 OpenFreeMap 일반 POI 벡터 타일은 서울 중심부에서 줌 14부터 제공되어, 그 전에는 로컬 OSM 색인의 제한된 시설을 사용합니다. 로컬 표시는 14.5~15.5에서 사라지고 벡터 타일 표시로 넘어갑니다. 벡터 타일의 class·subclass는 시설 유형, rank는 타일 격자 안의 상대 순서입니다. 규모·방문자 수·인기도는 추정하지 않습니다. MapLibre의 심벌 충돌 방지와 rank 정렬로 밀집 지역에서는 일부 시설을 생략합니다. 선택한 출발지·목적지는 별도 마커로 유지됩니다.

참고: https://openmaptiles.org/docs/schema/ , https://maplibre.org/maplibre-style-spec/layers/

## 검증 절차

1. `py -3 -B -m unittest discover -s tests -v`로 장소 검색·GTFS 필터·셔틀 ID 변환·OTP 응답 정규화 테스트를 실행합니다.
2. 실제 A, B/C, OSM을 준비한 뒤 OTP 빌드 로그와 `graph.obj` 생성 여부를 확인합니다.
3. A만 있는 날짜에 정상 대중교통 검색을 확인합니다.
4. 셔틀을 추가한 뒤 셔틀 정류장 인근 출발·도착 위치에서 `uses_shuttle=true` 구간이 실제 결과에 있는지 확인합니다.
5. 동일 입력에서 `include_shuttle=false`가 셔틀을 제외하는지 확인합니다.
6. 파업 대상 route_id를 지정해 재빌드 후 해당 노선이 어느 경로에도 없는지 확인합니다.
7. 출발 시각과 셔틀 배차를 변경하고 재빌드 후 결과가 변하는지 확인합니다.
8. 기존 운영자 서버 8766, 기존 시민 미리보기 8765를 따로 실행해 동작을 확인합니다.
9. 브라우저에서 서울 전체·구·동·거리·건물 수준으로 확대·축소하며 일반 시설 POI의 순차 표시, 겹침, 깜빡임을 확인합니다. 선택한 출발지·목적지와 추천 경로도 유지되는지 확인합니다.

현재 로컬 개발 환경에서는 A GTFS, 서울 OSM PBF, OTP 그래프와 장소 색인으로 A 경로 및 장소 검색을 확인했고, 단위 테스트 12개가 통과했습니다. 확정 B/C 영업 GTFS가 없어 셔틀 포함 검색은 검증되지 않았습니다. 브라우저 화면의 줌 단계별 POI 표시와 깜빡임은 아직 직접 검증하지 못했습니다.

## 공식 근거

- [OTP 컨테이너와 그래프 구축](https://docs.opentripplanner.org/en/latest/Container-Image/)
- [OTP GTFS GraphQL API](https://docs.opentripplanner.org/en/dev-2.x/apis/GTFS-GraphQL-API/)
- [OTP `planConnection`](https://docs.opentripplanner.org/api/dev-2.x/graphql-gtfs/queries/planConnection)
- [OTP feed ID 설정](https://docs.opentripplanner.org/en/latest/features-explained/Feed-ID/)
- [OTP 2.10.0 릴리스](https://github.com/opentripplanner/OpenTripPlanner/releases/tag/v2.10.0)
