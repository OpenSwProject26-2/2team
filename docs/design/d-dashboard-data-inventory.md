# D 운영자 지도 데이터 사용 내역 (2026-11-25)

2026-11-26 버스 실제 도로 선형, 파업 등록부, C/B 셔틀 자료 점검과 매핑률은 [버스 선형·파업·셔틀 데이터 점검](d-bus-road-geometry-audit.md)에 기록했다.

## 범위와 출처

이번 화면의 실제 교통 자료는 `C:\Users\1004r\busstrike-data\seoul_transit_data` 한 폴더에서만 읽는다. 원본은 수정하지 않는다. 아래 해시는 이 폴더의 현재 파일에 대한 SHA-256이다. 지도 바탕 타일은 OpenFreeMap/OpenStreetMap, 자치구 경계는 저장소의 `web/operator/data/districts_all.geojson`이며 교통 수요 원자료와 구분한다. `manifest.json`의 `created` 값은 `2026-11-17`이다.

| 데이터명 | 상대 경로 | 사용 목적 / 사용 컬럼 | 기준 / 가공 | SHA-256 |
|---|---|---|---|---|
| 매니페스트 | `manifest.json` | 관측 가능 날짜, 데이터 버전 (`created`, `days`) | 2026-11-17. 존재하는 날짜만 조회 | `f0b172572323ab87ef0cccaeabc195635899af63cda5a0d652e9c1fa49a2d0a7` |
| 버스 정류장 | `reference/stops.csv` | 버스 정류장 좌표·이름 (`BSST_ID`, `BSST_NM`, `lat`, `lon`, `gu`) | 12,564행. 노선·관측 승차와 정류장 ID 연결 | `7c38f3fbee1decbf42eabad30f866e26366b780a87d15ec2dd1814ab93d34b12` |
| 버스 노선 | `reference/routes.csv` | 노선 목록 (`ROUTE_ID`, `ROUTE_NUM`) | 767행. 실제 운행 상태나 도로 선형은 없음 | `1b12158e1ec99e2ba5e6a086c2aab3704c4b528c02d7a4b93bf1468bf1f35b3c` |
| 노선 정차 순서 | `reference/route_stops.csv` | 버스 유형·운수사·정차 순서 (`ROUTE_ID`, `BSST_ID`, `BSST_ORDER`, `COMBINE_TYPE`, `TRCM_NM`) | 46,228행. 정류장 연결선은 개략선이며 실제 도로 경로가 아님 | `37eac73dc0dc6e46c711d8578986b0fb12a47534cfd421b427e1d7e6384faa17` |
| 지하철역 ID | `reference/subway_stations.csv` | 역 ID 확인 (`OUT_STN_NUM`, `STN_KR_NM`) | 791행. API의 지하철 관측 매핑에만 사용하며 화면 버스 수요원에서 제외 | `e7a9eb2ae2120879a5bcdbc4f30ce8c1d7ab38bbbb4e65973129d9677209a1d7` |
| 자치구 코드 | `reference/gu_codes.csv` | 25개 구 이름·slug 매핑 (`gu`, `slug`) | 25행. 공간 필터 경계는 저장소 GeoJSON 사용 | `d5b8228e67735cd3457af0c0915dcef9e762312989d1dcb57ab130fdeead630f` |
| 지하철역 좌표 | `bundles_v4/split/seoul_v4_1_network_gtfs_etc.zip` 안의 `seoul_routegen_inputs_0610_v4/network/stations.csv` | 역 마커 (`stop_id`, `stop_name`, `stop_lon`, `stop_lat`) | 압축 내부 CSV만 읽음. A GTFS 배포 파일로 취급하지 않음 | 압축 파일 `0cf8777eb2a684a7f78c5357534f3e374d4a824203d89ca71deb84047bd7bf0f` |
| 버스 관측 승하차 | `share_k5/seoul/YYYYMMDD/stop_route_hourly.csv` | 버스 수요원 (`BSST_ID`, `ROUTE_ID`, `H`, `BOARD`, `ALIGHT`) | 예: 2026-10-26 파일 438,548행. 선택 구·시각의 정류장별 승차 합계, k=5 억제 적용 | 2026-10-26 파일 `cf5359b3ab0f24daf7379cf48bb537e79086b23c82b7c0a604e1e5d9f6108bdd` |
| 지하철 관측 OD | `share_k5/seoul/YYYYMMDD/subway_od_hourly.csv` | 역 승하차 및 운행 중단 승차 노출 (`RIDE_STN_ID`, `ALGH_STN_ID`, `RIDE_H`, `PSNG`) | API의 별도 통계로 유지. 화면 버스 수요원에는 합산하지 않음 | 2026-10-26 파일 `6d2e00463f98f5e8565984115034746967d03ce2df50bf5f5ce47c868da7b0f8` |
| TPSS 승차 원본 (연결성만 점검) | `sources/tpss/tpss_sta_route_hpasngr_2026.10.26-11.01.zip` | 노선·정류장 ID 중첩 검사. 지도 렌더링·수요 계산에는 사용하지 않음 | 내부 CSV 614,762행, 노선 ID 1,370개 중 참조 노선과 672개 일치, 정류장 ID 24,288개 중 참조 정류장과 12,535개 일치 | `18a3f96ea601fcf1e90e08bd36e0bf5a3704a212c3d6143f40f2b0d08425f7d0` |

관측일은 `2026-10-26`~`2026-10-30`, `2026-11-02`의 6일만 매니페스트와 실제 폴더에서 확인했다. 테스트 기간 `2026-11-03`~`2026-11-06`의 관측 파일은 없다. 마포구 2026-10-26 07시 조회 결과 버스 승차 14,094건, 정류장 수요 피처 401개, 미매핑 버스 0행이다. 이는 **정상일 관측 승차 건수**이며 고유 인원, 파업 전환, 셔틀 투입 후 미충족 수요가 아니다. 지하철 승차는 API 별도 통계이며 버스 원에 더하지 않는다. 참조 경로의 `COMBINE_TYPE`에는 시내·마을만 확인되었으며 경기버스 관측 수요를 임의 배분하지 않는다.

지도 수요원은 버스 정류장의 정상일 관측 승차만 한 위치당 하나의 원으로 합산한다. 화면의 고정 출근 시간대는 07:00~09:00 (`H=07`과 `H=08` 합계)다. 서울 전체는 약 3.2km, 선택 구는 약 1.25km 고정 격자를 사용하며 원은 정상일 승하차 가중 중심에 놓인다. 확대·축소만으로 다시 묶지 않는다. 원 크기의 계산 입력은 격자별 `BOARD-affected_board`, 즉 현재 운행하는 노선의 정상일 승차 건수이며 420ms 동안 보간한다. 지하철 지도 체크는 역 표시만 바꾼다. 이 원은 **실제 잔여·미충족 수요가 아니다.** TPSS 원본은 원에 더하지 않는다. 참조 노선에 없는 TPSS 노선 698개와 참조 정류장에 없는 TPSS 정류장 11,753개는 신뢰할 수 있는 노선 유형·좌표 매핑 전까지 경기버스로 추정해 표시하지 않는다.

구 선택 시 해당 구 경계로 카메라를 맞추고 기존 지도 피처를 비운 뒤 해당 구의 경계·정류장·관측 원을 로드한다. A GTFS 버스의 도로 선형은 검증된 외부 GeoJSON이 있을 때만 표시하고, 미확보 구간은 정류장 간 직선으로 채우지 않는다. 강동구의 방향별 RoadNet 추정 선형과 남은 한계는 [왕복 패턴 문서](d-roadnet-other-districts.md)에 기록했다. 운행안 비교 창은 지도 영역 안에서 마우스·터치로 끌거나 제목에 초점을 맞춰 방향키로 옮길 수 있다.

버스 원자료 집계 대조: 2026-10-26 구로구 `07` 16,498건 + `08` 18,013건 = 출근 34,511건, 마포구 `07` 14,094건 + `08` 18,763건 = 출근 32,857건. 지도 원에는 지하철 승차를 더하지 않는다. 실제 원 개수는 지역과 운행 조건에 따라 달라지며 확대·축소만으로는 변하지 않는다.

## 연결되지 않은 자료와 요청

| 요청 대상 | 필요한 파일과 필수 컬럼 | 현재 처리 |
|---|---|---|
| A팀 | 정식 A GTFS feed와 버전·정류장/노선 ID 대조표; 검증된 버스 도로 선형 GeoJSON (`route_id`, `geometry`, `geometry_source`, `geometry_status`) | 참조 CSV를 별도 모드로 표시. 실제 도로 선형이라고 주장하지 않음 |
| B팀 | 후보별 평가 파일 (`region`, `period`, `date`, `fleet`, `plan_id`, `revision`, `total`, `served`, `rate_percent`, `equity_score`, `equity_definition`, `network_snapshot_id`, 검증 상태); 차량 배정·정류장 시간표; 정류장별 셔틀 투입 후 미충족 (`stop_id`, `unserved`) | 충족률·형평성 점과 최종 미충족 수요원은 미연결 |
| C팀 | 25개 구·차량 조건별 후보 목록과 GeoJSON (`region`, `period`, `fleet`, `plan_id`, `revision`, 노선별 `LineString`, `geometry_source`, `geometry_status`, `stop_ids`); 실제 파업 시나리오 ID | 후보 점과 지도 미리보기/고정은 데이터 도착 전까지 비움 |

현재 루트의 2026년 1월 지자체 셔틀 계획 PDF는 C팀 후보가 아니다. `bundles_v4`의 참조 입력 압축 파일도 후보별 B/C 결과나 공식 운행안으로 간주하지 않는다. 후보와 평가의 `plan_id`·`revision`, 동일 지역·기간·차량 조건 및 공간 ID가 함께 검증된 경우에만 비교 그래프에 점을 올린다. 현재 25개 구의 관측 지도는 제공하지만 검증된 B/C 후보는 0개다. 실제 파업 목록, 차량별 시간표, 정류장별 최종 미충족 결과도 없다.

## 공유 제한

위 원본 CSV·압축 파일은 저장소에 복사하거나 커밋하지 않는다. k=5 집계라도 재배포 허가를 확인하지 않았으므로 파일이나 원 해상도 레코드를 공유하지 않는다. 카드 식별자, TAIMS/VW_KSCC 원자료, 인증키는 코드·로그·문서에 남기지 않는다.
