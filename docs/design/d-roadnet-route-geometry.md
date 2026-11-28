# 운영자 버스 선형에 RoadNet 경로 사용

`experiments/routegen_lab/roadnet.py`의 `path_any(a, b)`를 대시보드용 버스 선형 생성에도 연결했다. A GTFS 대표 운행편의 `stop_times.txt`에서 정류장 순서를 가져와 연속된 정류장 쌍마다 RoadNet 경로를 구한 뒤 하나의 선으로 잇는다. 기존 버스 구간의 `seg_path`가 있으면 먼저 쓰고, 없으면 OSM 도로망 최단경로를 사용한다. `path_any`가 직선으로 대체한 구간이나 도로망에 붙지 않은 정류장이 있으면 해당 **노선 전체를 실패 처리**한다. GTFS와 RoadNet 정류장 좌표가 80m 넘게 다르거나 최종 선이 GTFS 정차 순서를 따르지 않아도 실패 처리한다.

생성된 선형은 `geometry_source=roadnet_estimate`로 표시한다. 공식 운행 선형이 아니며 버스 통행 가능 도로·회차·방향까지 검증되었다는 뜻은 아니다. 기존 `official` 선형을 덮어쓰지 않는다. 성공한 결과는 기존 `--bus-geometry-file` GeoJSON 형식으로 대시보드에 연결된다.

```powershell
python -m busstrike.api.d_bus_routing --a-gtfs <A_GTFS_ZIP> --service-date 2026-11-02 --source roadnet --roadnet-root <압축해제한_구별_입력_루트> --roadnet-osm <구별_OSM_도로망_JSON> --route-id <A_GTFS_ROUTE_ID> --out <저장소_밖_데이터_루트>/derived/d_bus_roads_seoul_v4_roadnet_guro.geojson --append
```

## 구로구 수집·매핑 결과 (2026-11-26)

사용자가 `fetch_osm.py`로 `derived/osm/guro_roads.json`을 수집하고 `bundles_v4/split/guro_0610_v4.zip`을 `work/guro_0610_v4/`에 풀었다. OSM 응답의 기준 시각은 `2026-11-26T02:50:44Z`이며 도로 way 14,369개와 node 56,397개가 있다. 도로망 SHA-256은 `287f524c28ca9b437ad511a5a1f5fe766ffc9d033da37f8e502cafc7914329ba`다. 출처는 OpenStreetMap/Overpass, 라이선스는 ODbL이며 공식 버스 경로가 아니다.

A GTFS 698개 버스 노선 중 구로 입력의 정류장 ID에 대표 정차 순서 전체가 포함되는 33개를 후보로 삼았다. 33개 모두 RoadNet 생성과 GTFS 노선·정류장 순서 검사를 통과했다. 기존 OSRM 추정 4개 중 구로01(`116900013`)은 RoadNet 추정으로 교체하고 나머지 3개는 보존했다. 새 파일 `derived/d_bus_roads_seoul_v4_roadnet_guro.geojson`에는 RoadNet 추정 33개와 OSRM 추정 3개, 총 36개가 있으며 전체 A GTFS 기준 선형 매핑률은 **36/698(5.16%)**다. 신규 파일 SHA-256은 `348e220015bc0f17f9adbf03dd2c69893aba6304076405c2fd17cf513fc6a49a`다. 지도 API에서 `matched_routes=36`, `roadnet_estimates=33`, `osrm_estimates=3`을 확인했다.

현재 RoadNet 결과는 구로구 OSM 범위 안에서 정류장 순서를 완전히 확인한 노선만 포함한다. 다른 구와 구 경계를 넘어가는 노선은 각 지역의 OSM 도로망·입력 범위를 준비해야 한다. `segpaths.json`은 필수 입력이 아니며 `RoadNet.build()`가 저장된 구간 매칭을 다시 만든다. 생성 데이터와 원시 교통 자료는 저장소에 커밋하지 않는다.

## 지도 표시 규칙 (2026-11-26)

이전의 버스 점선은 A GTFS `stop_times.txt`의 이웃 정류장을 직선으로 이은 **정차 관계 개략선**이었다. 실제 도로 경로로 오해될 수 있어 A GTFS 지도에서는 제거했다. 이제 도로 선형을 확보한 노선만 지도에 그린다. 노선 목록에는 미확보 노선도 남기고, 선택 상세에 `도로 선형 미확보 · 지도에는 표시하지 않음`을 보여 준다. 서울 전체에서는 노선을 숨기고 자치구를 선택하면 해당 구의 확보된 선형을 보인다. 시내·마을·경기 등의 지도 토글은 선의 투명도를 전환하며, 선택 노선의 정차 순번은 노선별 대표 정차 순서다. 같은 정류장을 여러 번 지나면 순번을 함께 표시한다.

상단 `시뮬레이션 결과 화면 보기`는 별도 결과 조회 화면을 연다. C/B 평가 공간 결과가 연결된 구에서만 그 결과 레이어를 표시한다. `지도·관측으로 돌아가기`는 결과 레이어를 지우고 A GTFS 지도와 관측 버스 승차 원으로 돌아간다. 이 버튼은 노선이나 수요를 다시 평가하지 않는다. 현재 실행 서버의 `/api/d/simulation/options`는 4개 구의 `artifacts_connected=false`를 반환하므로 실제 C/B 공간 결과는 이 서버에서 아직 미연결 상태다.
