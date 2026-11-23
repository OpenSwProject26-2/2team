# 시민 길찾기 필요 데이터와 출처

`citizen-routing/`의 실제 경로 계산 입력을 정리합니다. 자료 파일은 Git에 포함하지 않습니다. 원본은 저장소 밖에서 읽기 전용으로 두고 생성물만 `runtime/`에 둡니다.

## 실제 입력

| 자료 | 용도 | 필수 |
| --- | --- | --- |
| A팀 버스·지하철 통합 GTFS 폴더 또는 ZIP | 일반 OTP 교통망, 파업 OTP 기준망 | 필수 |
| 서울 권역 `.osm.pbf` | OTP 보행망, 건물·시설 이름 색인 | 필수 |
| 파업 대상 A GTFS `route_id` JSON | 파업 그래프에서 해당 버스 노선 제외 | 필수 |
| C팀 계획 JSON, M05 시간표 JSON, M07 보고서 JSON, 정류장 좌표 CSV | 셔틀 GTFS 생성 | 셔틀 사용 시 |
| 생성된 셔틀 GTFS와 `shuttle-meta.json` | 파업 OTP에만 셔틀 추가 | 셔틀 사용 시 |

A GTFS에는 `agency.txt`, `routes.txt`, `trips.txt`, `stops.txt`, `stop_times.txt`, `calendar.txt` 또는 `calendar_dates.txt`가 필요합니다. `shapes.txt`가 없으면 차량 지도 선은 정차점을 잇는 개략선입니다. A 배포 ZIP 안에 통합 GTFS ZIP이 중첩되어 있어도 `--existing`으로 읽습니다.

파업 ID는 `--strike-scenario` 파일의 `strike_route_ids` 또는 `--strike-routes` 파일의 `affected_route_ids`로 전달합니다. 둘 중 하나만 지정할 수 있고 목록이 비거나 A `routes.txt`에 없는 ID는 오류입니다. 노선 이름으로 파업 여부를 추정하지 않습니다. 일반 모드에는 셔틀을 넣지 않습니다.

## C팀 셔틀 데이터 계약

`backend.shuttle_export`는 `--scenario`, `--plan`, `--timetable`, `--m07-report`, `--stops`, `--out`을 받습니다. 좌표 CSV에는 `stop_id,stop_lat,stop_lon` 열이 필수이고 `stop_name`을 넣으면 표시 이름으로 사용합니다. 또한 모든 영업 정차점의 서울 권역 좌표가 필요합니다. 계획과 M05의 `plan_id/revision`, 계획과 시나리오의 `scenario_id/network_snapshot_id`가 맞아야 합니다. M07은 `pass=true`, 실패 검사 0건이어야 합니다. 계획 `status=draft`는 `--demo`를 명시해야 합니다.

출력 `shuttle-meta.json`은 `source_kind`, `service_date`, `plan_id@revision`, M07 상태와 입력 해시를 기록합니다. `SYNTHETIC_DEMO`는 스모크 테스트이며 실제 운행이 아닙니다. `C_TEAM_CANDIDATE` 역시 운영 승인을 의미하지 않습니다. 셔틀의 운행일은 A GTFS 날짜 범위 안이어야 하고 GTFS `feed_version`은 `plan_id@revision`과 같아야 합니다.

## 로컬 서울 교통 집계와의 관계

`C:\Users\1004r\busstrike-data\seoul_transit_data`에서 아래 자료를 확인했습니다. **시민 OTP는 이 CSV를 직접 읽지 않습니다.** 수요 분석과 C팀 계획 생성의 참고 자료입니다.

| 경로 | 내용 | 현재 시민 경로 계산 |
| --- | --- | --- |
| `reference/stops.csv`, `routes.csv`, `route_stops.csv`, `subway_stations.csv` | 정류장·노선·역 기준표 | 직접 입력 아님. 정류장 대응 확인에 참고 |
| `seoul/<관측일>/bus_od_hourly.csv`, `subway_od_hourly.csv`, `transfer_flows.csv`, `journey_od.csv`, `stop_route_hourly.csv` | 원 해상도 날짜별 집계 | 직접 입력 아님. 로컬 분석용 |
| `share_k5/` | 5명 미만 칸 제거 공유본 | 직접 입력 아님 |
| `README.md`, `manifest.json` | 관측일·출처·검사 수치·공유 규칙 | 자료 확인용 |
| `reference/shuttle_plans_2026-01_*`, `gu/*/shuttle_plan_2026-01/` | 서울시의 2026년 1월 지자체 셔틀 자료 | C팀 생성 M05 셔틀과 별개 |

집계 **관측일**은 2026-10-26~30과 2026-11-02이며 다운로드 폴더 날짜와 다릅니다. 현재 A GTFS의 로컬 서비스 날짜 범위는 2026-11-02~06이고, 로컬 데모 셔틀 운행일은 2026-11-02입니다. 서비스 날짜와 집계 관측일을 혼동하지 마세요.

원 해상도 `seoul/` 및 `gu/<slug>/<관측일>/`는 로컬 전용입니다. `share_k5/`와 `reference/`는 팀 공유 가능 범위이지만 1월 셔틀 PDF의 재배포 조건은 별도 확인이 필요합니다. 원시 교통카드 자료, 카드 식별자, 인증키, 데이터 ZIP, `runtime/` 산출물을 Git에 올리지 않습니다. 상세 열과 공유 규칙은 로컬 `seoul_transit_data/README.md`를 따릅니다.

## 현재 상태와 팀원 준비물

로컬 일반 그래프는 A 707개 노선, 파업 그래프는 시나리오 지정 93개를 제외한 A 614개 노선과 구로 스모크 데모 셔틀 4개 노선입니다. 데모는 운행편 50개·정차시각 445개이며 M07은 `pass=true`, `fully_verified=false`입니다. 이는 확정 운행안이 아닙니다. `runtime/`은 Git에 없으므로 새 clone만으로 같은 셔틀 검색을 재현할 수 없습니다.

팀원은 A GTFS, 서울 OSM PBF, 파업 시나리오 또는 정확한 route_id 목록을 별도로 받아야 합니다. 셔틀까지 재현하려면 같은 버전의 C 계획, M05 시간표, M07 보고서, 정류장 좌표 CSV도 필요합니다. 최종 서비스에는 확정 운행안과 운영 승인 상태를 C/B팀에서 추가로 확인해야 합니다.

실행 명령은 [일반·파업 모드 통합 실행](strike-mode-integration.md)에 있습니다.
