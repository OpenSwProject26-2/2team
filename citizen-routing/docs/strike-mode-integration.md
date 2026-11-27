# 일반·파업 모드 준비와 실행

PowerShell에서 저장소의 `citizen-routing/` 디렉터리로 이동해 실행합니다. 입력 파일의 출처·필수 열·공유 범위는 [필요 데이터 문서](data-requirements.md)를 확인하세요. 예시의 `C:\path\...`는 실제 로컬 경로로 바꿉니다. A GTFS와 OSM 원본은 읽기 전용이며 준비·빌드 결과는 Git에서 제외된 `runtime/`에 생성됩니다.

## 1. 입력 준비

- A팀 통합 GTFS ZIP(중첩 배포 ZIP도 가능)과 서울 권역 OSM PBF를 준비합니다.
- 파업 시나리오 JSON의 `strike_route_ids`가 A GTFS `routes.txt`의 정확한 ID인지 확인합니다. 별도 `affected_route_ids` JSON을 쓰는 경우 아래 명령의 `--strike-scenario`를 `--strike-routes`로 바꿉니다.
- 셔틀을 연결할 경우 같은 `plan_id@revision`의 C 계획·M05 시간표·M07 보고서와 정류장 좌표 CSV를 준비합니다. 자료가 없다면 셔틀 내보내기 단계와 `--shuttle`/`--shuttle-meta` 인자를 생략합니다.
- 로컬 구로 `plan-smoke@1`은 데모입니다. 계획 상태가 `draft`이므로 아래 데모 예시에는 반드시 `--demo`를 붙입니다. 확정되지 않은 자료를 실제 운행으로 표기하지 않습니다.

## 2. 셔틀 GTFS 내보내기(자료가 있을 때)

```powershell
py -3 -B -m backend.shuttle_export --scenario "C:\path\scenario.json" --plan "C:\path\plan.json" --timetable "C:\path\timetable.json" --m07-report "C:\path\verifier_report.json" --stops "C:\path\stops.csv" --out runtime\shuttle-export --demo
```

`--demo`는 초안·스모크 자료에서만 사용합니다. 실제 C팀 후보를 내보낼 때는 자료 상태를 확인하고 `--demo`를 제거합니다. 내보내기는 M07 통과와 실패 검사 0건, 버전·시나리오 일치, 모든 정차점 좌표, GTFS 유효성을 검사합니다. 출력의 `shuttle-meta.json`에 출처와 검증 상태가 남습니다.

## 3. 두 OTP 입력과 장소 색인 준비

```powershell
py -3 -B -m backend.prepare_modes --existing "C:\path\gtfs_bus_rail_combined.zip" --osm "C:\path\seoul.osm.pbf" --strike-scenario "C:\path\scenario.json" --shuttle runtime\shuttle-export --shuttle-meta runtime\shuttle-export\shuttle-meta.json
py -3 -m pip install --target runtime/python-libs osmium==4.3.2
py -3 -B -m backend.build_places
py -3 -B routing-engine/build_graph.py --mode normal
py -3 -B routing-engine/build_graph.py --mode strike
```

셔틀 자료가 없다면 첫 명령에서 `--shuttle`과 `--shuttle-meta`를 함께 빼세요. `prepare_modes`는 일반 `runtime/otp/`와 파업 `runtime/otp-strike/`를 각각 준비합니다. A GTFS에 2026-11-02~06 운행 서비스가 기록된 현재 로컬 자료에서는 데모 셔틀의 2026-11-02에 맞춰 검색하세요. 다른 자료를 쓰면 해당 GTFS와 시간표의 운행 날짜를 확인해야 합니다.

`build_graph.py`는 `runtime/java/*/bin/java.exe`와 `runtime/downloads/otp-shaded-2.10.0.jar`가 있으면 로컬 Java를 사용하고, 없으면 Docker Compose의 OTP 2.10.0 이미지로 빌드합니다. 로컬 Java/JAR를 쓰려면 둘 다 별도로 준비해야 합니다. Docker를 쓰려면 Docker Desktop을 실행하세요. 그래프 재빌드 뒤 `runtime/otp/graph-version.txt`와 `runtime/otp-strike/graph-version.txt`가 각 manifest 버전과 일치해야 합니다.

## 4. 서버 실행

로컬 Java/JAR를 준비했다면 서로 다른 PowerShell 창 세 개에서 실행합니다.

```powershell
py -3 -B routing-engine/serve_otp.py --mode normal
```

```powershell
py -3 -B routing-engine/serve_otp.py --mode strike
```

```powershell
py -3 -B -m backend.server
```

Docker를 쓰는 경우 두 OTP 창 대신 아래 명령을 실행하고 시민 서버는 별도 창에서 실행합니다.

```powershell
docker compose -f routing-engine/docker-compose.yml --profile strike up -d otp otp-strike
py -3 -B -m backend.server
```

시민 화면은 [http://127.0.0.1:8775/](http://127.0.0.1:8775/)입니다. 일반 OTP는 8080, 파업 OTP는 8081을 사용합니다. 두 OTP가 모두 필요합니다. 종료할 때는 각 콘솔에서 Ctrl+C를 누르거나 Docker라면 `docker compose -f routing-engine/docker-compose.yml --profile strike down`을 실행합니다.

## 5. 확인

```powershell
py -3 -B -m unittest discover -s tests -v
Invoke-RestMethod http://127.0.0.1:8775/api/citizen-routing/status
```

상태 응답의 `modes.normal.available`과 `modes.strike.available`이 모두 참인지, 각 `graph_ready`와 `otp_running`을 확인합니다. `places_ready`가 거짓이면 장소 색인을 다시 만듭니다. `GRAPH_NOT_BUILT`는 입력 버전의 그래프를 재빌드해야 한다는 뜻이고, `OTP_UNAVAILABLE`은 해당 서버가 실행되지 않았다는 뜻입니다. 파업 목록 누락은 `STRIKE_ROUTES_MISSING`, 일반·파업 기준 버전 불일치는 `DATA_VERSION_MISMATCH`로 표시됩니다.

같은 출발지·목적지·2026-11-02 시각으로 검색하고 일반/파업 버튼을 전환해 결과를 비교하세요. 장소를 선택하기 전에는 자동 예시 출발지·목적지가 채워지지 않습니다. 한 번 검색한 뒤 전환하면 같은 OD·시각으로 자동 재검색합니다. `/api/citizen-routing/plan?mode=normal`과 `mode=strike`는 서로 다른 OTP 그래프를 사용합니다. `/api/citizen-routing/compare`는 같은 OD·시각의 두 결과를 반환합니다. 공통 인자는 `origin_lat`, `origin_lon`, `destination_lat`, `destination_lon`, `departure_at`, `include_shuttle`입니다. 파업 모드에서 `include_shuttle=false`는 셔틀을 탐색 단계에서 제외합니다.

현재 로컬 검증에서는 일반 A 707개 노선, 파업 A 614개 노선(지정 93개 제외), 구로 데모 셔틀 4개 노선·50 운행편·445 정차시각을 준비했습니다. M07은 `pass=true`이나 `fully_verified=false`이며 확정 B/C 영업 운행안은 연결되지 않았습니다. **이 수치는 로컬 데모 입력의 결과이지 새 clone의 기본 제공 데이터가 아닙니다.**

## 6. 운영자 확정 반영 (자동)

운영자 대시보드에서 파레토 안을 확정하면 확정 묶음이 생깁니다(작업자 A가 쓰고 시민 쪽이 읽는 계약).

```
<confirm_root>/<gu>/CURRENT                    지금 적용할 confirm_id 한 줄
<confirm_root>/<gu>/<confirm_id>/confirm.json  files: {scenario|plan|timetable|m07_report|stops: {path, sha256}}
  scenario.json  plan.json  timetable.json  verifier_report.json  stops.csv
```

`runtime/apply-config.json`(커밋하지 않음)에 경로를 적습니다.

```json
{"confirm_root": "C:/path/confirm_root", "gu": "guro",
 "a_gtfs": "C:/path/gtfs_bus_rail_combined.zip", "osm": "C:/path/seoul.osm.pbf",
 "normal_dir": "runtime/otp", "runtime": "runtime", "ports": [8811, 8812], "keep": 2,
 "container_prefix": "citizen-e2e", "build_heap": "6g", "serve_heap": "4g",
 "presets": {"normal_url": "http://127.0.0.1:8080/otp/gtfs/v1", "stations_csv": "C:/path/network/stations.csv",
             "origins_csv": "C:/path/factorial_1126/guro/demo/origins.csv"}}
```

반영은 `POST /api/citizen-routing/apply`(본문 `{}` 또는 `{"confirm_id": "..."}`)나 `py -3 -B -m backend.apply_confirm --config runtime/apply-config.json`으로 시작합니다. 별도 프로세스로 돌고 단계별 상태는 `GET /api/citizen-routing/apply/status`(`runtime/apply/status.json`)에 남습니다.

1. **verify**: CURRENT의 묶음을 읽어 파일 sha256을 confirm.json과 대조하고, M07 pass·실패 0건, 모든 셔틀 노선이 순환(출발 정류장으로 돌아오고 닫는 공차 ≤ 5분)인지 봅니다. 편도가 섞이면 `ONE_WAY_ROUTE`로 거부합니다.
2. **export**: `shuttle_export`를 `--demo` 없이 확정 모드로 실행합니다. 파이프라인 계획은 `status=draft`이지만 확정 묶음일 때만 받고 상태는 `operator_confirmed_not_approved`로 적습니다(운행 승인 아님). 셔틀 운행일은 A GTFS 운행일 중 같은 날짜 → 같은 요일 → 첫 운행일 순으로 옮기고 원래 시뮬레이션 날짜를 메타에 남깁니다.
3. **route_map**: 시나리오 `strike_route_ids`를 A GTFS `route_id`와 **정확히 같은 것만** 맞춥니다(`runtime/apply/route_map_<confirm_id>.csv`, 같은 표는 `py -3 -B -m backend.strike_route_map`으로도 만듭니다).
4. **prepare**: 일반 그래프는 그대로 두고 파업 입력만 새 폴더에 준비합니다(OSM은 하드링크).
5. **build**: 새 폴더에서 OTP 그래프를 빌드합니다(Docker `--network host`, 힙 6GB). 같은 입력(파업 입력 버전 + plan_sha256)의 그래프가 보관돼 있으면 빌드를 생략합니다.
6. **swap**: 다른 포트(8811/8812)에 새 파업 OTP를 띄워 응답을 확인한 뒤 `runtime/strike-active.json`을 바꾸고 이전 OTP를 내립니다. 빌드·적재 중이나 실패하면 이전 그래프가 계속 응답합니다.
7. **presets**(설정 시): 시연 출발지를 고릅니다(아래).

완료 조건은 graph-version.txt = manifest 버전, 그래프의 셔틀 출처 `plan_id@revision`·plan_sha256 = 확정 값입니다. 실제 안들은 `plan_id@revision`이 같을 수 있어(`plan-eqmc07-f11@1`) plan_sha256으로 구별합니다. 그래프 폴더는 최신 2개만 남깁니다. 빌드가 5분을 넘거나 시연 중 빌드할 수 없으면 보관된 그래프로 `POST /api/citizen-routing/apply/switch {"build_id": "g-..."}`, 같은 입력만 허용하려면 `POST /apply {"prebuilt_only": true}`를 씁니다. 취소는 `POST /api/citizen-routing/apply/cancel`.

시민 서버는 `--runtime runtime`(기본)에서 `strike-active.json`을 요청마다 읽어 파업 OTP 주소를 정합니다. 활성 표시가 없으면 기존 `--strike-otp-dir`/`--strike-otp-url`을 씁니다.

### 세 결과 비교와 시연 출발지

- `GET /api/citizen-routing/compare3?origin_lat=..&origin_lon=..&destination_lat=..&destination_lon=..&departure_at=..` → `results.{normal, strike_no_shuttle, strike_shuttle}`(각각 기존 plan 응답 형식), `best`, `delta_seconds`(평소 대비), `shuttle_gain_seconds`(셔틀 없음 대비), `confirm`(지금 적용된 확정). 기존 `/plan`·`/compare`는 그대로입니다.
- 화면의 "평소 · 파업 · 파업+셔틀 나란히 보기" 버튼이 세 카드(총 소요시간, 도보 거리, 환승, 구간 수단)를 보여 주고, 카드를 누르면 그 결과를 지도에 그립니다. 셔틀 구간은 굵은 청록 선, 파업으로 빠진 노선은 기존처럼 빨간 점선입니다. 화면 위쪽에 적용된 확정(안 이름, 확정 시각, plan_id@revision, M07 상태)이 뜹니다.
- `GET /api/citizen-routing/presets`: 반영 때 고른 시연 출발지. 후보는 역 800m 밖 셔틀 정류장(요인 실험 `demo/origins.csv`의 정류장 우선), 목적지는 그 노선이 닿는 역과 순환 반대편 정류장. "파업+셔틀"이 셔틀을 쓰고 "셔틀 없음"보다 15분 이상 빠른 곳을 최대 5곳 고르고, 모자라면 기준을 낮추지 않고 `near_misses`(화면에 "기준 미달")로 따로 줍니다. 셔틀 구간 OTP 시간과 M05 시간표 같은 구간의 차이(`m05_checks`)도 함께 남깁니다.
- 주소 `/?preset=0`은 첫 프리셋으로 세 결과를 바로 엽니다(헤드리스 캡처용).

한계: 셔틀 시간은 시뮬레이터 도로 소요시간 기준입니다. 셔틀 만차·지하철 혼잡은 OTP가 모릅니다. A GTFS·셔틀 GTFS에 shapes가 없으면 버스·셔틀 선은 정차점을 잇는 개략선입니다. 지금 이 PC의 A GTFS는 버스 전용(지하철 없음, 11/02 하루)이어서 지하철 환승 경로는 나오지 않습니다.

## 화면과 한계

파업 모드에서는 지도 위에 시나리오 기준 안내가 표시되고, 대표 일반 경로에서 제외된 A 노선 구간은 빨간 점선으로 표시됩니다. 선택한 경로는 교통수단별 색으로 강조됩니다. 새 OTP 응답이 올 때까지 이전 선을 유지한 후 교체합니다. 데모 셔틀은 스모크 데모임을 표시합니다. A GTFS와 데모 셔틀에 `shapes.txt`가 없어 차량 선 일부는 정차점 연결 개략선이며 실제 도로 선형이 아닙니다.

자료가 바뀌면 시민 서버와 OTP를 정지하고 두 입력을 다시 준비·빌드한 뒤 재시작합니다. OSM을 바꾸면 `backend.build_places`도 다시 실행합니다. 로컬 `runtime/`, 데이터 ZIP/PBF, 원시 교통카드 자료와 인증키는 커밋하지 않습니다.
