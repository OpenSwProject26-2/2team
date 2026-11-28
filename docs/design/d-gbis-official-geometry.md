# 강동구 경유 경기버스 공식 선형 연결

2026-11-26에 공공데이터포털 [경기도 버스노선 조회 API](https://www.data.go.kr/data/15080662/openapi.do)의 `getBusRouteInfoItemv2`, `getBusRouteStationListv2`, `getBusRouteLineListv2` 응답을 조회했다. 키는 실행 중 PowerShell의 보안 입력으로만 전달했으며 저장소·출력 파일·로그에 기록하지 않았다. 원본 응답과 가공 GeoJSON은 저장소 밖 `C:\Users\1004r\busstrike-data\seoul_transit_data\derived\`에 있다.

`scripts/build_gbis_official.py`는 로컬 `derived/gyeonggi_2xx/gyeonggi_candidate_route_stops.csv`에서 강동구 안에 좌표가 있는 2xx 후보 24개를 추렸다. 각 후보의 GBIS 노선 ID, 공식 정류장 순서, 공식 경로의 좌표 순서를 대조했다. 정류장 순서 일치율이 90% 이상이고 공식 경로가 그 정류장을 순서대로 통과한 경우에만 채택했다.

결과 `derived/gbis_gangdong_official.geojson`에는 **23개 공식 노선 형상**이 있다. `241409006`은 TPSS/GBIS 정류장 순서 일치율 84.4%로 제외했다. A GTFS에는 경기버스 노선이 없으므로 `--gyeonggi-geometry-file`로 별도 연결하며, ID는 `gbis:<공식노선ID>` 이름공간을 쓴다. 지도에서는 경기버스 유형으로 표시하고 노선 선택 시 전체 경로와 공식 정류장 순서를 볼 수 있다. 기존 A GTFS의 서울버스 노선과 혼합하지 않는다.

출력 SHA-256: `4e4f4d953e674059ea766c8b7aa682f9620f25aa6d607458182db4174ee18006`. 로컬 서버 `/api/d/sources`에서 `gyeonggi_geometry.matched_routes=23`, `last_error=null`을 확인했다.

이 검증은 **형상과 정류장 순서**에 관한 것이다. 현재 GBIS 응답은 조회 시점의 자료이며, TPSS 후보의 최신 정류장 순서는 2026-11-08이다. 2026-10-26~11-02 관측일 당시의 동일 운행 여부, 승객 수, 여유 정원은 검증하지 않았다. 따라서 이 파일만으로 관측일의 경기버스 흡수량을 재계산하지 않는다. 화면의 합성/관측 수요와 공식 형상 출처를 구분한다.

재현 시에는 저장소 루트에서 `DATA_GO_KR_SERVICE_KEY`를 현재 셸의 환경변수로 설정하고 아래 명령을 실행한다. 출력은 저장소 바깥에 둔다.

```powershell
python scripts/build_gbis_official.py --data-root 'C:\Users\1004r\busstrike-data\seoul_transit_data' --district gangdong --out 'C:\Users\1004r\busstrike-data\seoul_transit_data\derived\gbis_gangdong_official.geojson'
```

수집을 마친 뒤 서버를 띄울 때는 키가 필요하지 않다. 현재 로컬 8878 서버는 아래 두 선형 파일을 읽는다.

```powershell
python web/operator/serve.py --port 8878 --a-gtfs 'C:\Users\1004r\busstrike-data\seoul_transit_data\bundles_v4\split\seoul_v4_1_network_gtfs_etc.zip' --transit-data 'C:\Users\1004r\busstrike-data\seoul_transit_data' --bus-geometry-file 'C:\Users\1004r\busstrike-data\seoul_transit_data\derived\d_bus_roads_gangdong_patterns_verified.geojson' --gyeonggi-geometry-file 'C:\Users\1004r\busstrike-data\seoul_transit_data\derived\gbis_gangdong_official.geojson'
```
