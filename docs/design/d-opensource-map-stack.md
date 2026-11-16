# D팀 오픈소스 지도·공간 분석 기술 검토

> 2026-11-16 구현 확인: 실제 코드에서 CDN 고정 MapLibre GL JS 6.18.0과 OpenFreeMap `liberty` 스타일을 호출하고, 저장된 OSRM 자동차 경로 추정 fixture를 표시 입력으로 쓴다. 로컬 HTML/API 응답과 D 테스트는 확인했으나 브라우저 제어 도구의 URL 확인 실패로 지도 타일·상호작용을 시각적으로 수락하지 못했다. 라이선스는 [MapLibre 버전별 고지](https://github.com/maplibre/maplibre-gl-js/blob/v6.18.0/LICENSE.txt), [OpenFreeMap 권리 목록](https://github.com/hyperknot/openfreemap/blob/main/LICENSE.md), [OSRM 고지](https://github.com/Project-OSRM/osrm-backend/blob/master/LICENSE.TXT), [OSM 공식 안내](https://www.openstreetmap.org/copyright)와 대조했다.

> 2026-11-15 기준 로컬 개발 선택. 팀 공용 D08 기술 스택과 배포 정책은 ADR/리뷰 대기. 실제 브라우저의 타일 표시와 서울 지도 한국어 품질은 아직 확인하지 못했다.

## 선택

1차 대시보드는 **MapLibre GL JS 6.18.0(렌더러) + OpenFreeMap 공개 `liberty` 스타일/벡터 타일(지도 데이터 공급)**로 구현했다. MapLibre는 WebGL 기반 GeoJSON source/layer를 제공하고 수백 노선·여러 수요 원을 source 단위로 갱신할 수 있다. 성능 우위는 구조상 예상이며 서울 전체 피드로 측정한 수치는 아니다. OpenFreeMap은 현재 공식 안내상 등록·API 키 없이 공개 인스턴스를 무료로 사용할 수 있다. [MapLibre 공식 문서](https://maplibre.org/maplibre-gl-js/docs/), [OpenFreeMap Quick Start](https://openfreemap.org/quick_start/), [공개 인스턴스 안내](https://openfreemap.org/).

선택 범위는 **로컬 D 개발 화면**이다. 서버·프런트 전체 기술 스택, 인증/배포, 공용 M08 계약을 팀 대신 확정하지 않는다. 타일 서비스 주소는 `web/operator/app.js` 한 곳에 있으므로 다른 MapLibre 호환 스타일로 교체할 수 있다. 라이브러리·타일 모두 인터넷 연결이 필요하다. 공개 인스턴스에는 SLA가 없고 중단될 수 있으므로 실제 배포 전 공급자/자체 호스팅을 다시 결정한다. [OpenFreeMap 이용 약관](https://openfreemap.org/tos/).

## 비교와 근거

| 기준 | MapLibre + OpenFreeMap | Leaflet + 타일 공급자 | Kakao Maps API |
| --- | --- | --- | --- |
| 코드 라이선스 | MapLibre BSD-3, OpenFreeMap 코드 MIT | Leaflet BSD-2 | 독점 SDK/서비스 조건 |
| 지도 데이터 | OSM ODbL, OpenMapTiles·스타일 출처 별도 | 선택한 타일에 따라 다름 | Kakao 공급 |
| 무료 사용·키 | 공개 OpenFreeMap은 현재 무료·키/가입 없음 | Leaflet 무료, 타일은 별도 | 앱·JavaScript 키/도메인 등록 필요 |
| 한국 지도 표시 품질 | OSM 기반. 서울 화면 실측 필요 | 타일 공급자에 좌우 | 한국어 지도 대조 후보 |
| 버스·지하철 노선 | GeoJSON `line` layer별 색/필터 | GeoJSON/Polyline 그룹 | Polyline/Overlay |
| GeoJSON·GTFS 연결 | D API 변환 → `setData` | D API 변환 → GeoJSON layer 갱신 | D 변환 계층 필요 |
| 많은 선·수요 원 성능 | WebGL 일괄 렌더링; 실제 707노선 부하 측정 미완료 | 객체 수에 따라 Canvas/클러스터 필요 가능 | 쿼터와 렌더링 성능 실측 필요 |
| 레이어 ON/OFF | layer visibility | LayerGroup 표시 | overlay 표시 |
| 마커·툴팁 | circle layer + Popup | Marker + Tooltip/Popup | Marker/InfoWindow |
| 중복 구간 표시 | 별도 GeoJSON line layer | 별도 Polyline layer | 별도 Polyline overlay |
| 로컬 실행 | 정적 JS + Python API, 외부 CDN/타일 인터넷 | 정적 JS + Python API, 타일 별도 | 키/등록 도메인 필요 |
| 배포 비용·제한 | 공개 서버 현재 무료, 약관·중단 가능성 | 선택한 공급자에 좌우 | Kakao 정책·호출량 확인 필요 |
| 기존 Python API | GeoJSON JSON 응답으로 직접 연결 | 동일 | 별도 SDK 객체 변환 |
| 실데이터 확장 | source 갱신으로 A/B/C 교체, 지도 컴포넌트 유지 | GeoJSON layer 교체 | SDK 연동 계속 필요 |

Leaflet은 단순 구현과 마커에 강점이 있지만 공개 OpenFreeMap 벡터 스타일을 Leaflet에서 쓰려면 MapLibre bridge도 설치해야 한다. 이 프로젝트의 다수 노선/수요 레이어에는 MapLibre 단독 구성이 더 간단하다. Leaflet의 렌더링 성능이 부족하다고 실험으로 판정한 것은 아니다. [Leaflet 공식 안내](https://leafletjs.com/), [Leaflet 라이선스](https://github.com/Leaflet/Leaflet/blob/main/LICENSE), [OpenFreeMap Leaflet 연동](https://openfreemap.org/quick_start/). Kakao 지도는 키와 SDK 도메인 등록이 필요하고 한국어 지리정보 품질 비교 대상이다. [Kakao 공식 가이드](https://apis.map.kakao.com/web/guide/).

## 실제 사용 내역과 표기

| 프로젝트·공식 저장소 | 적용 버전·라이선스 | 사용 위치·기능·수정 여부 | 조건·화면 표기 |
| --- | --- | --- | --- |
| [MapLibre GL JS](https://github.com/maplibre/maplibre-gl-js) | CDN 고정 `6.18.0`, BSD-3-Clause | `web/operator/index.html` CSS, `app.js` ESM; 지도/GeoJSON/레이어/팝업. 라이브러리 수정 없음 | 배포 시 라이선스·동봉 고지 유지. [원문](https://github.com/maplibre/maplibre-gl-js/blob/main/LICENSE.txt) |
| [OpenFreeMap](https://github.com/hyperknot/openfreemap) | 공개 `liberty` 스타일, 서비스 버전은 공급자 관리, 코드 MIT | `app.js` 지도 style URL; 자체 코드/스타일 수정 없음 | 공개 서비스 약관 확인, 출처 화면 표시. [라이선스 명세](https://github.com/hyperknot/openfreemap/blob/main/LICENSE.md), [약관](https://openfreemap.org/tos/) |
| [OpenStreetMap](https://www.openstreetmap.org/copyright) | 공개 데이터 ODbL | OpenFreeMap 타일의 기초 데이터 | 지도 내 `© OpenStreetMap contributors` 링크. 파생 데이터 재배포 시 ODbL 검토 |
| [OpenMapTiles](https://openmaptiles.org/) | 타일 schema/스타일의 제3자 권리 포함 | OpenFreeMap `liberty` 스타일 경유 | 지도 내 `© OpenMapTiles` 링크. OpenFreeMap [라이선스 명세](https://github.com/hyperknot/openfreemap/blob/main/LICENSE.md) 참조 |

지도 라이브러리 BSD와 OSM 데이터 ODbL은 다른 라이선스다. OpenFreeMap의 저장소 코드 MIT가 타일·스타일·OSM 데이터의 모든 권리를 대체하지 않는다. `liberty` 스타일에는 BSD-3 코드와 CC BY 4.0 디자인 등 하위 출처가 있다. 공개 사용에는 사이트 [이용 약관](https://openfreemap.org/tos/)이 적용되며 무료·무제한 조회 안내에도 가용성 보장은 없다. 현재 화면은 MapLibre의 기본 attribution과 함께 OpenFreeMap/OpenMapTiles/OSM 링크를 지도 안에 별도로 둔다. OSM 공식 래스터 타일 서버는 사용하지 않는다. 해당 서버에는 별도 캐시·트래픽·출처 정책이 있다. [OSMF Tile Usage Policy](https://operations.osmfoundation.org/policies/tiles/).

## GTFS와 실제 선형 처리

- D API의 지도 응답은 노선 `LineString`, 정류장·수요 `Point`인 GeoJSON `FeatureCollection`이다. 좌표 배열은 `[경도, 위도]` WGS84 순서로 검사한다. 노선 `route_id`는 표시용 번호와 분리한다.
- A GTFS의 `shapes.txt`가 있는 활성 운행편에 한해 대표 shape를 정렬해 지도 선을 그린다. `shapes.txt`가 없으면 **노선 선을 그리지 않는다**. 정류장 사이 직선을 도로로 오인시키지 않는다. 현재 A 2026-11-11 배포본의 2026-11-02 지도 로딩 결과: 활성 노선 707개, 좌표 있는 정류장 13,151개, 공식 shape 노선 0개.
- 현재 A 지도 어댑터는 대표 shape 1개/route만 표시한다. 방향별 모든 운행 변형이나 반복 방문을 정밀하게 표현하지 않으므로 route detail에 이 한계를 남긴다. 서비스일은 `calendar`/`calendar_dates`로 확인한다. 파업 범위·운행편 시간표의 독립 검증은 이 지도 어댑터가 하지 않는다.
- 합성 fixture의 7개 노선 선형, 정류장 10개, 수요 셀 80개는 서울 실제 타일 위에 놓인 **가상 좌표·가상 형상**이다. DEMO 표시를 유지한다. 가상 공통 구간은 GIS 분석 수치가 아니다.

## 공간 분석 기술 도입 판단

| 기술·공식 근거 | 후보 기능 | 현재 판단 |
| --- | --- | --- |
| [Shapely](https://shapely.readthedocs.io/en/stable/manual.html), [BSD-3](https://github.com/shapely/shapely) | `LineString`, buffer/intersection/union, 중복 길이 | A 정상·B 셔틀의 검증된 도로 선형과 방향/segment ID가 없으므로 **미설치·미적용**. 제공되면 미터 단위 CRS에서 오차 설정·방향 검증·union 후 길이 계산 |
| [OSMnx](https://osmnx.readthedocs.io/en/stable/), [MIT](https://github.com/gboeing/osmnx/blob/main/LICENSE.txt) | OSM 도로/보행 그래프·연결성·최단 경로 | A의 검증 망을 우선. 부족할 때 별도 D06/출처 검증 후 도입 |
| [Valhalla](https://github.com/valhalla/valhalla) | 도로 경로 복원·지도 매칭 | GTFS 정류장 직선 대체가 필요할 때 운영 비용·정확도 별도 실험. 현재 미도입 |
| [OpenTripPlanner](https://docs.opentripplanner.org/en/latest/), [LGPL-3.0+](https://github.com/opentripplanner/OpenTripPlanner/blob/dev-2.x/LICENSE) | GTFS+OSM 보행·철도·버스 복합 경로 | 현재 M06 버스 검색을 즉시 대체하지 않음. 서버/그래프 빌드·정책·운영 비용 평가 후 ADR |

실제 중복률을 만들려면 셔틀/정상 노선의 **검증된 동일 도로 방향별 segment** 또는 지도 매칭된 선형이 필요하다. 거리 허용 오차만으로는 나란한 다른 도로를 실제 공통 운행으로 판정할 수 없다. 공통 segment를 union한 뒤 겹침 길이를 한 번만 합산하고 셔틀 전체 길이로 나눈다. 오차, 투영 CRS, 교차로/반대 방향 정책, 결과 출처를 설정·보고해야 한다. 현재 자료만으로 거리·비율은 `판정 불가`다.

## 남은 검증

1. 실제 브라우저에서 OpenFreeMap 타일 표시, 한국어 지명·도로 품질, 확대·드래그·레이어/팝업·오류 상태를 확인한다. 현재 Windows 브라우저 제어가 URL 확인 실패로 중단돼 **시각적 합격 판정은 하지 않았다**.
2. 배포 전 서비스 운영 주체가 OpenFreeMap 약관과 외부 타일 의존을 검토하고, 공개 공급자 또는 자체 호스팅을 결정한다. 자체 호스팅은 저장공간/운영 비용이 크다. [공식 안내](https://github.com/hyperknot/openfreemap/blob/main/docs/self_hosting.md).
3. A/B가 방향별 도로 선형·검증 report를 제공한 뒤 Shapely 겹침 분석과 결과 출처·오차 검사를 구현한다.
