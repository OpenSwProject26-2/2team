# D 지도 버스 선형·파업·셔틀 데이터 점검 (2026-11-26)

## 입력과 출처

데이터 루트는 `C:\Users\1004r\busstrike-data\seoul_transit_data`이다. 원본과 파생 GeoJSON은 저장소 밖에 두며 커밋하지 않는다. 지도 바탕과 자치구 경계는 별도 표시 자료다.

| 자료 | 데이터 루트 안의 경로 | 확인한 필드와 용도 |
|---|---|---|
| A GTFS 네트워크 | `bundles_v4/split/seoul_v4_1_network_gtfs_etc.zip` | `route_id`, 노선명, 정류장 ID·순서·좌표. 2026-11-02 서비스 날짜의 D 스냅샷 `a-gtfs-0cf8777eb2a684a7` |
| C 파업 등록부 | `bundles_v4/region_bundles_0610_v4.zip` 안의 `<구>_0610_v4/registry/strike_route_registry.csv` | `route_id`, `route_name`, `operator_id`, `operator_name`, `route_type`, `strike_state`, `valid_from`, `valid_to`, `match_status`. 방향은 없음 |
| C smoke 운행안 | 같은 ZIP의 `<구>_0610_v4/smoke/plan_smoke_m05.json` | 구로·동작·강서·강동의 시험 운행안. 각 4노선. 정식 후보·승인 운행안 아님 |
| 도로 추정 선형 | `derived/d_bus_roads_seoul_v4.geojson` | `a_gtfs_route_id`, `source_route_id`, `route_short_name`, `agency_id`, `stop_ids`, `geometry_source`, `retrieved_at`, 도로 길이·최대 정류장 스냅 거리 |

## 수집·검증 결과

2026-11-26에 지정된 A GTFS ZIP의 698개 버스 노선 중 4개를 OSRM `driving` 경로로 시험 수집했다. `116900013`(구로01), `100100038`(201), `100100090`(605), `100100098`(651)은 모두 노선 ID·이름·A의 agency ID·대표 정류장 순서·좌표, 최대 80m 스냅 및 우회 거리 검사를 통과했다. 수집 결과는 공식 노선 경로가 아닌 `osrm_estimate`로 명시한다. **공식 0, 추정 4, 미확보 694, 전체 매핑률 4/698(0.57%)**이다. A 분류는 시내 374, 마을 242, 미분류 82, 경기 0이므로 경기버스 실제 선형 매핑률은 산정할 수 없다. 이 4개가 버스 전용차로·회차·운행 방향까지 검증되었다는 뜻은 아니다. 선형의 `retrieved_at`은 각 GeoJSON feature에 UTC로 기록되어 있다.

구로·동작·강서·강동 등록부의 `affected` 행은 각각 93·121·63·31개다. A GTFS에서 **ID와 노선명은 각각 전부 일치**하지만, 중복 제거하면 189개다. A의 agency는 `SEOUL_BUS`라는 범주 값이므로 등록부의 실제 `operator_id`·`operator_name` 검증은 아직 불가하다. 등록부에 방향도 없으며 `match_status=rule_only`, 적용 기간은 **2026-01-13~14**다. 따라서 이 등록부를 2026년 11월 현재 확정 파업 목록으로 자동 표시하지 않는다. `/api/d/strike-registry`가 매핑 상태를 반환하고, 화면의 **C팀 1월 모델 가정 적용** 버튼으로 명시적으로 선택할 수 있다. 이 버튼은 가정 노선을 지정하므로 관측 승차 영향 계산 입력도 바뀐다. **파업 표시 체크박스는 강조선의 표시만** 바꾼다. 실제 선형이 없는 파업 노선을 정류장 직선으로 붉게 덧칠하지 않는다.

지정 루트에는 C팀의 지역별 smoke 운행안과 강서구 차량 10·20·40·60대 예제가 있으나, 25개 구의 정식 `routegen-results/1` manifest, 후보 ID와 B 평가 ID의 검증된 결합, 날짜·차량·운행안별 정류장 미충족 결과 및 셔틀 공식 선형은 없다. smoke 결과를 승인된 C/B 운행안이나 산점도 평가점으로 승격하지 않는다. 후보·평가가 연결되면 기존 `SimulationStore`와 산점도 선택/미리보기 흐름을 사용할 수 있다. 현재 시험 데이터만으로 해당 브라우저 동작을 실제 후보 대상으로 검증할 수는 없다.

## 도로 선형 연결 방식

`python -m busstrike.api.d_bus_routing --a-gtfs <위 ZIP> --service-date 2026-11-02 --route-id <ID> --out <데이터 루트>/derived/d_bus_roads_seoul_v4.geojson --source official`은 서울 공식 `getRoutePath`를 우선 시도한다. 실행 환경에 `SEOUL_BUS_API_KEY`가 필요하다. 공식 결과가 없을 때만 같은 명령에 `--source osrm --append`로 선택 노선을 보완한다. 현재 수집 4건은 키가 없어 OSRM으로 얻었다. 대량 일괄 보완은 정류장 대표 패턴의 누락, 역방향·지선, 50정류장 제한, 공용 OSRM 사용량을 검토한 뒤 진행해야 한다.

서버 실행 예:

```powershell
python web/operator/serve.py --a-gtfs 'C:\Users\1004r\busstrike-data\seoul_transit_data\bundles_v4\split\seoul_v4_1_network_gtfs_etc.zip' --transit-data 'C:\Users\1004r\busstrike-data\seoul_transit_data' --bus-geometry-file 'C:\Users\1004r\busstrike-data\seoul_transit_data\derived\d_bus_roads_seoul_v4.geojson'
```

서버는 버전과 노선 ID·이름·agency·정류장 순서·좌표를 다시 검사한다. 자치구에서는 실제 선형이 경계와 교차하는 노선의 구 내부 구간을 보여 준다. 노선 선택 시 `/api/d/route-geometry`가 해당 노선 전체 선형을 반환한다. 미확보 노선의 짧은 점선은 정류장 연결 참고선일 뿐 도로 선형이 아니다. 공식/추정 여부는 `geometry_status`로 구분한다.

서울 공식 API는 `getRoutePath`의 순서·WGS84 좌표 및 `getStaionByRoute`의 정류장·방향·운수사 정보를 제공한다: <https://www.data.go.kr/data/15000193/openapi.do>. 경기 GBIS API는 노선 형상·경유 정류소 조회 기능을 제공한다: <https://www.data.go.kr/data/15080662/openapi.do>. 경기 API 키와 서울·경기 노선 ID 교차표가 없으므로 이번 데이터에서 경기버스 선형을 검증해 연결하지 못했다.

## 수요 계산과 필요한 후속 데이터

### 관측 수요 원과 정차 순서 표시 (2026-11-26)

A GTFS/서울 참조 지도에서 관측 수요 원은 선택한 **하루**의 07·08시 `share_k5/seoul/YYYYMMDD/stop_route_hourly.csv` 승차를 정류장별로 합친 뒤 고정 격자(서울 전체 약 3.2km, 자치구 약 1.25km)에 재집계한다. 화면의 원 반경만 키웠으며 `passenger_count`, 격자 중심, 날짜별 합계는 변경하지 않았다. 예를 들어 2026-10-26 구로구 정상일 버스 승차 합계는 API와 원 계산 입력이 모두 34,503건이다. 이 값은 카드로 식별한 고유 인원이나 실제 미충족 수요가 아니며, k=5 미만 칸은 원본 공유본에서 빠져 있다. 원의 위치는 정상일 승하차 가중 중심으로 고정된다. 빈 격자나 제외된 노선 때문에 원이 사라질 수 있으나 날짜들을 임의로 합산하지 않는다.

선택 노선의 정류장 마우스오버는 해당 서비스 날짜의 **활성 운행편 중 정차 횟수가 가장 많은 한 편**의 `stop_sequence` 순서를 1부터 보여 준다. 한 정류장을 왕복으로 두 번 지나면 `정차 순서 1 · 59`처럼 두 순서를 모두 표시하고 지도 번호 표식도 각각 둔다. `/api/d/route-geometry`는 자치구 경계 밖을 포함한 선택 노선의 정차 목록을 순서대로 반환한다. 노선이 선택되지 않은 정류장에는 공통 순번이 없으므로 정류장 ID만 보여 준다. 다른 시간대의 변형 운행편 순서는 이 표시와 다를 수 있다.

### 경기버스 후보 일괄 추출 (2026-11-26)

`scripts/extract_gyeonggi_candidates.py`로 지정 데이터 루트의 TPSS `hpasngr`·`hturn` 두 주간 ZIP을 읽어 `derived/gyeonggi_2xx/`에 노선 목록, 경유 정류장 순서, 매니페스트를 생성했다. **2xx ID 후보 618개** 모두 두 주간 자료에서 양수 운행횟수가 확인된다. 18개는 양수 정류장 순서가 없고, 나머지의 최신 날짜 경유 호출은 43,451개다. `reference/stops.csv`와 정류장 ID가 정확히 일치하여 좌표를 붙인 호출은 11,357개(26.1%)다. 정류장별 중복 호출을 포함한 비율이다. 2개 이상 좌표가 있는 후보는 526개지만, 모든 정류장 호출에 좌표가 있는 노선은 1개뿐이다.

**2xx 접두어는 경기 관할의 공식 분류가 아니다.** 노선명·운수사·방향·2026년 공식 GBIS 선형 검증은 각각 0개다. 따라서 이 CSV는 지도에 실노선으로 올리지 않고, A GTFS 경기버스 탭에는 후보 수와 미검증 사유를 표시한다. TPSS의 2xx `hpasngr` 승객 칸은 비어 있어 관측 경기버스 승차량으로도 사용하지 않는다. 2023년 경기도 [경로이력 파일](https://www.data.go.kr/data/15124065/fileData.do)은 오래된 일회성 자료이므로 현재 운행선과 ID·정류장 순서가 일치하는지 재검증해야 한다. 최신 공식 형상은 [GBIS 노선형상 API](https://www.data.go.kr/data/15080662/openapi.do)의 키와 응답으로 확보한다.

`routegen/strike.py`는 파업 후에도 운행하는 시내버스가 같은 OD를 연결하면 `absorbed_running_city`로 차감한다. 이는 **접근 가능 노선의 존재에 따른 흡수 가정**이며 여유 좌석·환승 배분까지 검증한 용량 계산이 아니다. 마을·경기는 `routegen/absorb.py`에서 정차 순서와 남은 정원으로 추가 흡수한다. D가 읽는 v4 저장 변형은 마을·경기 포함 여부의 네 조합뿐이므로 D 화면에서 시내버스를 끌 때 즉시 새 M05 미충족원을 계산할 저장 결과는 없다. 시내버스 제외 조건을 실측 미충족으로 보이려면 C의 해당 조건 재생성, B의 동일 날짜·운행안·차량별 M05 실행, 정류장별 `unserved` 결과, 공통 `network_snapshot_id`가 필요하다.

필요한 납품: C팀 `routegen-results/1` manifest, `plan_id`·`revision`·`candidate_id`·노선별 `stop_ids`·방향·차량 배정·GeoJSON/출처; B팀 동일 키의 `runs.json`, `metrics.json`, `passenger_outcomes.csv` 또는 정류장별 미충족 집계, 형평성 정의·점수; A팀 실제 운수사 ID·노선 방향·서울/경기 ID 교차표와 최신 shape. 인증키와 원시 교통카드 자료는 문서·로그·저장소에 넣지 않는다.
