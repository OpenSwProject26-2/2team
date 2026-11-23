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

## 화면과 한계

파업 모드에서는 지도 위에 시나리오 기준 안내가 표시되고, 대표 일반 경로에서 제외된 A 노선 구간은 빨간 점선으로 표시됩니다. 선택한 경로는 교통수단별 색으로 강조됩니다. 새 OTP 응답이 올 때까지 이전 선을 유지한 후 교체합니다. 데모 셔틀은 스모크 데모임을 표시합니다. A GTFS와 데모 셔틀에 `shapes.txt`가 없어 차량 선 일부는 정차점 연결 개략선이며 실제 도로 선형이 아닙니다.

자료가 바뀌면 시민 서버와 OTP를 정지하고 두 입력을 다시 준비·빌드한 뒤 재시작합니다. OSM을 바꾸면 `backend.build_places`도 다시 실행합니다. 로컬 `runtime/`, 데이터 ZIP/PBF, 원시 교통카드 자료와 인증키는 커밋하지 않습니다.
