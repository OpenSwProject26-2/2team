# seoul_transit_data 로컬 연동

## 확인한 구조와 범위

2026-11-17 데이터셋 ZIP은 1,448개 파일, 비압축 약 3.61GB이다. `.keys` 내용은 열지 않았다. `DATA_GUIDE.md`, `README.md`, `manifest.json`의 관측일·공유 규칙을 기준으로 읽는다. 실제 관측일은 2026-10-26~30 및 2026-11-02이다. 11-03~06 원본 집계는 없다.

| 폴더 | 확인 결과 | D 사용 |
| --- | --- | --- |
| `reference/` | 정류장 12,564, 노선 767, 25개 구 코드, 노선 정차 순서, 지하철역 기준표 | A GTFS ID 대조와 구 코드 확인. A GTFS와 정류장 ID 12,256개, 노선 ID 632개가 정확히 일치 |
| `share_k5/` | 5명 미만 칸 제거 집계 | 서울 정류장·노선·시간 집계에서 선택 구 경계 내부의 관측 승차·하차를 정류장 단위로 합산. 정상일 관측 수요로만 표시 |
| `seoul/`, `gu/<구>/<날짜>/` | 원 해상도 PC 전용 OD·환승·승하차 | 서버가 열지 않음. 브라우저/API 원 해상도 노출 금지 |
| `gu/<구>/stops.csv` 등 | 구별 기준표와 1월 셔틀 계획 PDF | 구조 확인. PDF는 운행 실적이 아니며 재배포 조건 미확인 |
| `bundles_v4/` | C/B 입력 번들 ZIP | 지역·변형·날짜별 `demand_bins.csv`와 C 지역망 정류장 좌표로 셔틀 투입 전 잔여 수요 원 표시. M05 `metrics.json`, M07 `verifier_report.json`은 없음 |
| `patterns/` | 실험 주간 06~09시 k5 OD·환승 패턴, manifest | 구조·정의 확인. 관측 수요나 M05 결과로 치환하지 않음 |
| `sources/` | TPSS 승차·운행횟수 ZIP, 서울 버스 경유정류소 XLSX, 지하철 시각표·혼잡도, 1월 셔틀 PDF | 출처·용도 확인. 버스 GPS 경로 또는 철도 선형은 없음 |
| `docs/`, `scripts/` | 생성 파이프라인·집계 코드 | 정의 확인. 기존 전처리 재실행 없음 |

## API와 화면

`TRANSIT_DATA_ROOT`에 데이터 폴더 또는 ZIP 경로를 지정할 수 있다. 지정하지 않으면 사용자 Downloads의 같은 이름 폴더·ZIP을 탐색한다. `--transit-data` 인자로도 지정할 수 있다. 원본은 읽기 전용이고 `.keys`와 PC 전용 집계를 읽는 코드 경로는 없다.

- `GET /api/d/transit/status`: 관측일, 기준표 규모, A GTFS ID 교집합만 반환. 로컬 절대경로와 비공개 원본은 반환하지 않음.
- `GET /api/d/transit/observed?district=guro&date=2026-10-26&hour=07`: k5 공유 집계를 정류장별로 합산한 결과. `demand_kind=observed_normal_boardings`, `k=5`를 명시한다. k5에서 제거된 인원은 복원하지 않는다.
- `GET /api/d/districts`, `/api/d/network?mode=real&district=guro&extent=inside|full`: 런타임은 `web/operator/data/districts_all.geojson`의 서울 25개 OSM 행정경계를 읽는다. 현재 선택 목록은 구로·동작·강서·강동 4개 구다. 노선·정류장을 공간 필터링하고 경계 교차 노선을 포함하며, 구 내부 구간과 노선 전체를 구분한다. 선택 노선의 전체 정류장 좌표는 별도 `route_stops` 필드로 제공한다.

`web/operator/data/districts_all.geojson`은 OpenStreetMap `admin_level=6` 경계를 약 0.0001도(약 10m) 허용 오차로 단순화한 표시용 자료이며, ODbL 1.0 및 © OpenStreetMap contributors 출처를 유지해야 한다. `web/operator/build_districts.py`는 Overpass `out geom` 응답에서 이 파일을 다시 만드는 도구다. 기존 `web/operator/data/districts.geojson`은 4개 SGIS 경계의 로컬 자료로 보존했지만 런타임은 사용하지 않는다. OSM 경계는 법정 경계 판정용이 아니다.

## 선형과 시뮬레이션 제한

A GTFS에는 `shapes.txt`가 없고 제공 데이터셋의 `sources/`에도 버스 GPS 경로·철도 중심선이 없다. [공공데이터포털 서울 버스 노선정보조회 서비스](https://www.data.go.kr/data/15000193/openapi.do)의 `getRoutePath`·`getStaionByRoute` 어댑터를 추가했지만 현재 인증키가 없어 공식 응답을 검증하지 못했다. `.keys`는 사용하지 않았다. OSM 공개 Overpass 구로 노선 조회는 시간 초과했다. 별도로 **OSRM 자동차 도로 라우팅**을 시도해 마을 3·시내 4개 노선의 추정 선형을 검증했다. 버스의 공식 운행 경로가 아니라 `osrm_estimate`이며 미확인 버스는 기존 점선 정차 관계로 표시한다. 지하철에는 자동차 도로 추정을 적용하지 않는다.

철도는 별도로 [Esri Korea의 MOIS_KR_Subway 선형 레이어](https://portal.esrikr.com/arcgis/rest/services/Hosted/MOIS_KR_Subway/FeatureServer/2)를 조회할 수 있다. `--rail-gis-live`로 서버 시작 시 읽거나 `--rail-gis-file`로 로컬 GeoJSON을 연결한다. A GTFS 노선명과 GIS `sub_nm`이 맞고 대표 운행편의 모든 역이 물리 선로에서 250m 이내일 때만 실선으로 표시한다. 실제 2026-11-02 A 배포본에서는 1·2·3·4·5·6·8·9호선 8개가 일치했고 7호선은 한 역이 약 1.1km 떨어져 점선으로 남는다. 선로는 물리적인 통로이며 급행·분기·상하행 운행 패턴의 검증된 경로는 아니다. 원본 GeoJSON은 저장소에 넣지 않는다.

버스 공식·OSM·OSRM 선형을 확보한 팀원은 검증된 GeoJSON을 저장소 밖에 두고 `--bus-geometry-file`로 연결한다. 각 LineString feature에는 `a_gtfs_route_id`, 원본 `source_route_id`, `route_short_name`, A GTFS의 `agency_id`, 대표 운행편과 동일한 순서의 `stop_ids`, `geometry_source`(`official`, `osm_route`, `osrm_estimate`), `source_namespace`, `source_version`가 필요하다. 이 교차표와 모든 정류장이 선형에서 120m 이내에 순서대로 나타나는지 서버가 검사한다. 불일치한 feature는 채택하지 않는다. 현재 OSRM 결과 7개는 OS 임시 폴더의 로컬 파일이며 Git에 포함되지 않는다. 같은 GTFS에서 전처리 명령을 재실행해야 재현된다.

ODsay의 `searchBusLane`·`loadLane`은 [API 문서](https://lab.odsay.com/guide/releaseReference?platform=web)에 있지만, [이용 약관](https://lab.odsay.com/doc/totalPolicy)은 결과의 무단 저장·가공·배포를 제한하며 [요금·문의 안내](https://lab.odsay.com/contact/contact)는 분석·연구 목적의 사전 협의를 요구한다. 현재 키·이용 승인이 없어 호출하거나 결과를 캐시하지 않았다. 공식 서울 API는 `SEOUL_BUS_API_KEY` 환경변수를 사용하며 키 없이 샘플 좌표를 만들지 않는다.

`bundles_v4`는 C/B 입력이므로 M05 성과·M07 통과·차량 배정이라고 해석하지 않는다. 실제 시뮬레이션 운행안 선택과 시민 경로 반영에는 지역별 `manifest.json`, 후보·계획 파일, 날짜별 `runs.json`/`metrics.json`/`verifier_report.json`과 선택 계획의 검증된 시간표·A/C 정류장 매핑이 추가로 필요하다.

## 수요 원 표시

일반 A 교통망에서는 `share_k5/seoul`의 정상일 관측 승차 원을 조회할 수 있다. 시뮬레이션 모드의 기본 빨간 원은 `bundles_v4/region_bundles_0610_v4.zip`의 지역·변형·날짜별 `demand_bins.csv`를 C 지역망 `network/stops.csv`의 출발 정류장 좌표에 합산한 **셔틀 투입 전 파업 잔여 수요**다. 날짜·시간·`affected*` 변형에 따라 값과 크기가 바뀐다. `mean`은 해당 5일의 정류장별 평균이다. 5명 미만 정류장·시간 칸은 API에서 숨기므로 지도 원 합계가 보고서 분모보다 작을 수 있다. 좌표가 매칭되지 않은 행 수와 인원은 API에 별도 집계한다. 이 수요는 차량 수나 ours/sp에 따라 변하지 않는다.

운영자 지도의 **지도 표시 옵션 → 잔여·미충족 수요 원**으로 원을 끄고 켤 수 있다. 시뮬레이션 화면에서는 관측 수요 옵션을 숨기며, 조건을 바꾸면 원 반지름을 약 0.5초에 걸쳐 보간한다. 지도 표시 체크는 계산 입력이 아니다. 지하철 중단은 현재 4개 입력 변형에 없으므로 지하철 표시를 꺼도 잔여 수요·충족률을 재계산하지 않는다.

실제 지역 입력 160개 `demand_bins.csv`의 `bin_start_s`는 21600·25200·28800·32400, `bin_width_s`는 3600이다. 따라서 제공된 수요는 06:00~10:00의 네 시간칸뿐이다. 시뮬레이션 시간 선택도 이 범위로 제한한다. 차량 수·ours/sp를 바꿀 때 정류장별 원이 달라지려면 같은 날짜·변형의 M05 `passenger_outcomes.csv`가 필요하다. 현재 D 어댑터는 원본이 있을 때 출발 정류장별 미충족 인원으로 원을 바꾸고 직전 비교 조건에서 달라진 정류장을 강조한다. 원본이 없으면 입력 잔여 수요 원을 유지한다.

실제 `passenger_outcomes.csv`와 같은 망 버전의 정류장 좌표가 연결되면 D는 승객 ID를 반환하지 않고 출발 정류장별 `served`, `unserved`, `censored`를 합산한다. 5명 미만 출발지는 공간 API에서 제외한다. 이때 원은 선택 운행안의 M05 미충족 수요로 바뀌며, 동일 입력의 상대안 공간 결과가 있으면 정류장별 미충족 증감을 색으로 표시한다. 원본 결과가 없을 때는 보고서의 구 단위 충족률을 정류장에 임의 배분하지 않는다.

합성 DEMO에서는 지도 표시 체크와 운행 가정을 분리한다. `시내버스·마을버스·경기버스·지하철 운행` 체크나 가상 셔틀 추가·제외만 합성 배분 API를 다시 호출하며, 지도 레이어 체크는 계산 입력을 바꾸지 않는다. 직전 조건과 같은 시간대의 정류장 거점별 미충족 인원·미충족률을 비교하고 빨간 원 반지름을 보간한다. 실제 B 결과 모드에서는 입력 번들의 잔여 수요 원과, 별도의 `passenger_outcomes.csv`가 있을 때만 셔틀 투입 후 미충족 수요 원을 표시한다. 보고서의 구 전체 충족률은 정류장 원으로 임의 배분하지 않는다.

A GTFS의 철도 정차 관계 개략도에서는 모든 운행편을 합칠 때 급행·정차 생략 패턴이 만드는 장거리 대각선이 생길 수 있다. 같은 노선의 2.5km 이하 인접 연결을 따라 양 끝역에 도달 가능한 장거리 연결만 제외한다. 이 처리는 실제 철도 선형 확보가 아니며, 남은 연결도 점선 개략도다.
