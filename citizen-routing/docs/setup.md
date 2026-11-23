# 시민 길찾기 설치·화면·API

실제 준비 파일과 공유 범위는 [필요 데이터 문서](data-requirements.md), 최신 실행 명령은 [일반·파업 모드 통합 실행](strike-mode-integration.md)을 기준으로 합니다. 이 서비스는 `citizen-routing/` 안에서 독립 실행되며 운영자 대시보드와 기존 시민 미리보기 파일을 수정하지 않습니다.

## 새 폴더에서 시작

기능 브랜치를 시험할 때 PowerShell에서 다음을 실행합니다. 아래 clone 명령은 **코드만** 받습니다. GTFS·OSM·파업 시나리오·C/B 셔틀 자료와 `runtime/` 그래프는 포함되지 않습니다.

```powershell
git clone --branch feat/citizen-routing --single-branch https://github.com/OpenSwProject26-2/2team.git 2team-citizen-routing
Set-Location 2team-citizen-routing/citizen-routing
py -3 -B -m unittest discover -s tests -v
```

실제 경로 검색을 하려면 Python 3.10 이상, A팀 GTFS, 서울 권역 OSM PBF, 파업 A route_id 목록과 OTP 2.10.0 실행 수단이 필요합니다. OTP는 Docker Desktop 또는 로컬 Java/JAR로 실행합니다. 건물·시설 검색 색인은 `py -3 -m pip install --target runtime/python-libs osmium==4.3.2` 후 `py -3 -B -m backend.build_places`로 만듭니다. 상세 준비·서버 명령은 통합 실행 문서를 따르세요. `py -3 -m backend.prepare`와 단일 `otp` 실행은 두 모드 동시 검증 절차가 아닙니다.

## 화면 사용

1. [http://127.0.0.1:8775/](http://127.0.0.1:8775/)에서 출발지·목적지 입력란의 건물·시설·정류장 이름을 검색해 선택하거나 지도를 클릭합니다. 지도에서 후보 시설 또는 임의 좌표를 선택할 수 있습니다.
2. GTFS 운행 날짜에 맞는 출발 시각을 정하고 일반 또는 파업 운행 모드에서 **경로 찾기**를 누릅니다. 두 장소를 선택하기 전에는 기본 예시 OD가 자동 입력되지 않습니다.
3. 한 번 검색한 뒤 모드를 바꾸면 같은 장소·시각으로 자동 재검색합니다. 파업 대상 A 노선은 파업 OTP 입력에서 제외됩니다. 파업 모드에서만 준비된 C팀 셔틀을 포함할 수 있습니다.
4. 추천 카드에서 이동수단별 시간 막대, 단계별 장소·소요시간, 도보·대기 시간과 환승 횟수를 확인합니다. 카드를 선택하면 지도 선이 강조됩니다.
5. 드롭다운에서 최적 경로순, 최소 시간순, 최소 환승순, 최소 도보순을 선택합니다. 카드의 최적·최소 등 분류 표시는 정렬을 바꿔도 유지됩니다.

버스는 A GTFS `routes.txt`의 `route_desc` 근거로 마을·시내·경기·미분류로 표시하며 근거가 없으면 미분류입니다. 지도와 카드는 시내버스 파랑, 경기버스 주황, 마을버스 초록, 지하철 보라, 대체 셔틀 청록색으로 구분합니다. 대체 셔틀의 데모·후보 상태는 별도로 표시하며 데모는 실제 운행이 아닙니다.

## API

- `GET /api/citizen-routing/status`: 일반·파업 그래프 버전, 빌드·OTP·장소 색인 준비 상태
- `GET /api/citizen-routing/plan?mode=normal|strike&origin_lat=...&origin_lon=...&destination_lat=...&destination_lon=...&departure_at=...&include_shuttle=true|false`: 지정 모드의 OTP 결과
- `GET /api/citizen-routing/compare?origin_lat=...&origin_lon=...&destination_lat=...&destination_lon=...&departure_at=...&include_shuttle=true|false`: 같은 조건의 일반·파업 결과와 차이
- `GET /api/citizen-routing/places?q=...`: 로컬 OSM 장소 색인과 GTFS 정류장 통합 검색
- `GET /api/citizen-routing/stops?q=...`: 정류장 검색
- `GET /api/citizen-routing/nearby?lat=...&lon=...`: 주변 장소 후보
- `GET /api/citizen-routing/map-places?west=...&south=...&east=...&north=...&zoom=...`: 현재 지도 영역의 일반 시설
- `GET /citizen-map-style.json`: 시민 전용 배경 지도 스타일

`include_shuttle=false`는 셔틀을 OTP 탐색 단계에서 제외합니다. 일반 모드는 셔틀을 사용하지 않습니다. 셔틀 포함을 선택해도 셔틀 탑승을 강제하지 않습니다. 경로가 없으면 `NO_PATH`, OTP가 없으면 `OTP_UNAVAILABLE`, 그래프 입력 버전이 맞지 않으면 `GRAPH_NOT_BUILT` 상태를 확인하세요. 실제 응답의 `routes[].legs[]`에는 수단, 노선, 출발·도착 장소, 시간, 선형 상태가 포함됩니다. `geometry_status=stop_schematic`은 실제 도로 선형이 없는 정차점 연결선입니다.

기본 정렬은 소요시간 + 환승당 10분 + 도보시간의 50%를 사용합니다. `/api/citizen-routing/compare`의 `delta_seconds`는 두 모드의 선택 기준 최적 경로 간 차이이며, 어느 한쪽에 경로가 없으면 값이 없습니다.

## 일반 시설 POI

시민 지도는 원본 `frontend/vendor/openfreemap-liberty.json`을 덮어쓰지 않고 `/citizen-map-style.json`에서 별도 스타일을 만듭니다. MapLibre GL JS를 사용하며, 배경 타일·글꼴·스프라이트 로딩에는 OpenFreeMap 인터넷 연결이 필요합니다. 검색할 OSM 장소 이름과 좌표는 준비 단계에서 로컬 PBF로 색인하며 운영 중 외부 장소 검색 API를 호출하지 않습니다. 지도에 없는 장소도 임의 좌표로 선택할 수 있습니다.

| 지도 확대 수준 | 일반 시설 표시 |
| --- | --- |
| 11.5부터 | 로컬 OSM의 병원·대학교·공공기관·박물관 등 주요 시설 |
| 12.7부터 | 학교·도서관·의원·슈퍼마켓 등 교육·생활 시설 |
| 13.5부터 | 교회·사찰 등 종교시설 |
| 14부터 | OpenFreeMap 벡터 타일의 주요 공공·문화시설; 이후 `rank`에 따라 학교·병원·종교시설 추가 |
| 16부터 | 음식점·카페·편의점 등 소규모 시설을 단계적으로 추가 |
| 18부터 | 기타 상점과 세부 시설명 추가 |

벡터 타일의 `class`·`subclass`는 시설 유형, `rank`는 타일 내 상대 순서입니다. 규모·방문자 수·인기도를 추정하지 않습니다. MapLibre 충돌 방지로 밀집 지역의 일부 시설명은 생략될 수 있습니다. 정류장·역 레이어와 사용자가 선택한 출발지·목적지 마커는 일반 POI 규칙과 별도입니다.

## 검증과 남은 한계

`py -3 -B -m unittest discover -s tests -v`로 코드 테스트를 실행하고, 상태 API에서 일반·파업 두 모드의 `available`을 확인하세요. 같은 OD·시각으로 양쪽을 검색해 제외된 A 노선이 파업 결과에 없는지, 셔틀 포함/제외 결과가 구분되는지 확인합니다. 브라우저에서는 지도 선택·추천 카드·모드 전환과 서울 전체부터 건물 수준까지의 일반 POI 표시를 직접 확인합니다.

현재 로컬 구로 `plan-smoke@1` 셔틀은 스모크 데모이며 M07이 완전 검증된 상태가 아닙니다. 확정 B/C 영업 시간표와 운영 승인은 별도 확보가 필요합니다. A GTFS와 데모 셔틀에 `shapes.txt`가 없어 일부 차량 선은 개략선입니다. 원자료·데이터 ZIP·PBF·`runtime/` 그래프는 저장소에 올리지 않습니다.
