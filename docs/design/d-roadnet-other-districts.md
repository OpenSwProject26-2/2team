# 다른 자치구 도로 선형과 역 번호

이 문서는 생성 단계를 시간순으로 기록한다. 아래 `123/698`, `19/26`, 부분 도로 경로 파일은 중간 결과다. **현재 강동구 서버 입력은 마지막 `암사역 왕복 운행 패턴 보완` 절의 `d_bus_roads_gangdong_patterns_verified.geojson`**이며 서울 버스 130개와 별도 GBIS 경기버스 23개를 연결한다.

아래는 구로구 선형에 강서·동작·강동을 추가할 때 실행한 PowerShell 명령이다. 저장소 루트에서 실행한다. `fetch_osm.py`는 Overpass 접속이 필요하고, 출력은 저장소 바깥 데이터 루트에 둔다. 실제 생성 결과는 문서 하단에 기록했다.

```powershell
$dataRoot = 'C:\Users\1004r\busstrike-data\seoul_transit_data'
$aGtfs = Join-Path $dataRoot 'bundles_v4\split\seoul_v4_1_network_gtfs_etc.zip'
$env:PYTHONPATH = (Resolve-Path .\src).Path
$output = Join-Path $dataRoot 'derived\d_bus_roads_seoul_v4_roadnet_4districts.geojson'
Copy-Item -LiteralPath (Join-Path $dataRoot 'derived\d_bus_roads_seoul_v4_roadnet_guro.geojson') -Destination $output
foreach ($gu in @('gangseo', 'dongjak', 'gangdong')) {
    $region = Join-Path $dataRoot "work\${gu}_0610_v4"
    Expand-Archive -LiteralPath (Join-Path $dataRoot "bundles_v4\split\${gu}_0610_v4.zip") -DestinationPath (Join-Path $dataRoot 'work') -Force
    $osm = Join-Path $dataRoot "derived\osm\${gu}_roads.json"
    python experiments/routegen_lab/fetch_osm.py --root $region --out $osm
    python -m busstrike.api.d_bus_routing --a-gtfs $aGtfs --service-date 2026-11-02 --source roadnet --roadnet-root $region --roadnet-osm $osm --all-matching --out $output --append
}
```

변환 뒤 서버를 새 결과로 다시 시작할 때 `--bus-geometry-file $output`과 `--transit-data $dataRoot`를 함께 지정한다. `--all-matching`은 해당 구 입력에 대표 정류장이 모두 있는 A GTFS 버스 노선만 시도한다. 정류장 좌표·순서·도로 연결 검증에 실패한 노선은 결과 선형에 넣지 않고 `metadata.failures`에 기록한다. 나머지 21개 구는 구별 입력과 OSM 도로망 또는 공식 선형을 추가로 확보해야 한다.

A GTFS ZIP에 철도 운행편이 없더라도 `--transit-data`에 로컬 데이터 루트를 지정하면 `bundles_v4/split/seoul_v4_1_network_gtfs_etc.zip`의 `network/stations.csv`에서 역 좌표와 역 코드를 읽어 역 마커를 표시한다. 이것은 역 위치 참고 자료이며 철도 노선·운행 순서를 뜻하지 않는다. 마커에 마우스를 올리면 역 코드 또는 버스 정류장 ID가 보이고, 버스 노선을 선택한 경우에는 해당 노선의 정차 순서도 보인다. 서울 전체 보기에서는 기존 요구대로 역·정류장 마커를 숨기고, 자치구를 고르면 그 구의 마커가 보인다.

## 2026-11-26 생성 결과

사용자가 위 명령으로 강서·동작·강동의 OSM 도로망을 수집하고 `derived/d_bus_roads_seoul_v4_roadnet_4districts.geojson`을 생성했다. OSM 원본은 `derived/osm/{gangseo,dongjak,gangdong}_roads.json`에 있으며 출처는 OpenStreetMap/Overpass(ODbL)다. 변환 방법은 GTFS 대표 정류장 순서를 따라 RoadNet의 도로 경로를 이어 붙이고, 직선 대체·좌표 불일치·순서 불일치 노선을 제외하는 방식이다. 결과 선형은 공식 버스 운행 선형이 아닌 추정치다.

- A GTFS 스냅샷: `a-gtfs-0cf8777eb2a684a7`
- 결과: 110/698개 노선(15.76%); RoadNet 추정 109개, 기존 OSRM 추정 1개, 공식 선형 0개
- 실패: `120900002` 1개. `120900120 → 120900125` 구간에서 RoadNet이 직선 대체를 반환해 결과에서 제외했다.
- 구별 지도 API에서 보이는 도로 선형: 구로 32개, 강서 25개, 동작 32개, 강동 6개. 경계 통과 노선은 여러 구에서 중복 집계될 수 있다.
- 결과 SHA-256: `435b74cf34b6107aceff1169f667cf25af322c4dc6a68df38f41c4e5f43f7f9c`
- OSM SHA-256: 강서 `061e354f57c82cd0d4cd62ee432ab3df542105b35206cafb8f6f1731e8dcfa2`, 동작 `ac68f41b7e58714c1aa2fecf1e9ffbd853cc54b1343a531c19510487419c9ff2`, 강동 `42f1c06c83ced39559ae16e86a4ef3c0b9bf239743c14d99089392f06904c6ab`

로컬 서버 `127.0.0.1:8878`을 새 선형 파일로 다시 시작하고 `/api/d/sources`에서 `matched_routes=110`, `last_error=null`을 확인했다. 구별 `/api/d/network`에서도 위 선형 개수와 역 마커가 반환됐다. 지도 화면의 시각적 표시는 별도로 확인하지 않았다.

## 강동구 미확보 노선 보완 중간 결과 (2026-11-26)

강동구에 표시되는 A GTFS 서울 버스 26개 중 위 네 구의 RoadNet 입력에 전체 정류장이 들어 있는 것은 6개였다. 나머지 20개는 서울 전체 정류장 자료에는 모두 있지만 구별 도로망 범위를 벗어났다. `d_bus_routing.py`에 긴 정류장 순서를 25개 이하 구간으로 나눠 공용 OSRM 도로 경로를 조회하고 합친 뒤 전체 순서를 검증하는 처리를 추가했다. 공용 OSRM의 요청 간격은 최소 1.1초로 유지했다. 결과는 공식 운행 선형이 아니라 `osrm_estimate`로 표시한다.

20개 중 **13개 통과, 7개 제외**다. 제외된 노선 `3412`, `3413`, `3318`, `342`, `강동01`은 정류장 쌍의 도로 우회가 검증 상한을 넘었고, `6300`, `6200`은 정류장 좌표가 OSRM 도로에서 80m 이상 떨어져 있었다. 실패한 구간을 직선으로 채우지 않았다. 기존 110개를 포함한 `derived/d_bus_roads_seoul_v4_gangdong_completed.geojson`에는 서울 버스 선형 **123/698개(17.62%)**가 있다. 강동구 API 기준 서울 버스 선형은 19/26개다. 별도로 연결한 GBIS 공식 경기버스 23개는 이 수치에 넣지 않는다.

새 결과 SHA-256은 `174861c4a3dc35e76ce01de1ee170835162fae2c4b307c35a7404f61bf36ae02`이다. 서버 `127.0.0.1:8878`을 이 파일과 GBIS 파일로 다시 시작했고 `/api/d/sources`에서 서울 버스 123개, 경기 공식 23개, 두 연결 모두 `last_error=null`을 확인했다. 지도 화면 자체의 시각적 표시는 별도로 확인하지 않았다. 공용 OSRM 사용 조건은 [프로젝트 안내](https://github.com/Project-OSRM/osrm-backend/wiki/Demo-server)를 따른다.

## 정류장 색과 선형 미확보 표시

지도 정류장 아이콘은 해당 정류장을 경유하는 버스 유형으로 정한다. 파랑=시내버스, 주황=경기버스, 초록=마을버스, 보라=지하철역, 회색=미분류 버스다. 한 정류장에 경기버스와 마을버스가 함께 서면 유형별 아이콘을 각각 표시하고, 가까운 서로 다른 유형의 아이콘은 화면에서만 조금 비켜 놓는다. 같은 유형의 노선 번호가 여럿이어도 아이콘을 번호별로 늘리지 않는다. 마우스를 올리면 해당 유형의 경유 노선명과 도로 선형 확보 수가 표시된다. 같은 도로를 공유하는 노선 선도 **버스 유형별로 한 줄**로 정리하고, 유형이 다른 선만 나란히 표시한다. 노선 선택 시 원래 전체 경로를 강조한다. 별도 정류장 색 범례는 두지 않는다. 지도 표시 옵션에서 유형을 끄면 해당 유형의 아이콘과 선을 숨긴다. 이 시각적 간격은 실제 정류장 위치나 도로 폭, 수요 크기, 승객 수를 의미하지 않는다.

## 강동구 부분 도로 경로와 정차 순번 (2026-11-26)

기존 선형 파일에 경로가 없던 서울 노선 `3412`, `3413`, `3318`, `6300`, `6200`, `342`, `강동01`은 A GTFS의 대표 운행편 정류장 순서와 `derived/osm/gangdong_roads.json`, `work/gangdong_0610_v4`를 대조했다. RoadNet의 경로가 있고 두 정류장이 순서대로 120m 이내에 있는 구간만 `roadnet_partial_estimate`로 저장했다. 정류장 쌍 75, 67, 83, 13, 10, 71, 73개가 각각 연결됐으며, 나머지는 빈 구간으로 둔다. 결과 파일은 저장소 밖 `derived/d_bus_roads_gangdong_partial_verified.geojson`이다. 이 경로는 공식 버스 운행 선형이 아니며 도로별 버스 통행과 회차는 검증되지 않았다. 7개 노선의 전체 경로를 확정하려면 서울시 공식 노선경로 자료가 더 필요하다.

PowerShell에서 재현하려면 기존 `d_bus_roads_seoul_v4_gangdong_completed.geojson`을 새 출력 파일로 복사하고 `$env:PYTHONPATH=(Resolve-Path .\src).Path`를 설정한 뒤 아래를 실행한다. 서버는 출력 파일을 `--bus-geometry-file`로 읽는다. API 키는 필요하지 않다.

```powershell
python -m busstrike.api.d_bus_routing --a-gtfs <A_GTFS_ZIP> --service-date 2026-11-02 --source roadnet-partial --roadnet-root <데이터루트>/work/gangdong_0610_v4 --roadnet-osm <데이터루트>/derived/osm/gangdong_roads.json --route-id 100100224 --route-id 100100225 --route-id 100100498 --route-id 100100508 --route-id 124000006 --route-id 124000038 --route-id 124900002 --out <데이터루트>/derived/d_bus_roads_gangdong_partial_verified.geojson --append
```

노선을 선택하면 A GTFS `trips.txt`의 해당 날짜 운행편 중 정차 횟수가 가장 많은 하나와 `stop_times.txt`의 `stop_sequence`를 사용한다. 같은 정류장을 왕복 중 다시 방문하면 정류장 ID가 같아도 새 순번을 매긴다. 예를 들어 강동05는 2026-11-02 GTFS에서 59회 정차하고, 지도에서 선택 시 1~59번을 표시한다. 이 순서는 선택된 운행편 하나의 순서이므로 다른 시간대의 변형 운행편 전체를 뜻하지 않는다.

## 암사역 왕복 운행 패턴 보완

위의 `d_bus_roads_gangdong_partial_verified.geojson`은 중간 시험본이다. 현재 서버는 저장소 밖 `derived/d_bus_roads_gangdong_patterns_verified.geojson`을 사용한다. 2026-11-26에 A GTFS의 2026-11-02 활성 운행편을 `p0`/`p1` 패턴별로 나누어 각 패턴의 가장 긴 정차 순서를 RoadNet/OSM 도로망에 맞췄다. 강동02의 첫 패턴 20곳은 암사역을 지나지 않지만, 다른 패턴 66곳은 암사역 정류장을 포함한다. 이전 자료에서 암사역 마을버스 정류장과 표시 선이 약 2.8km 떨어졌고, 새 자료에서는 약 17m로 확인됐다. 강동01·05와 이전에 선형이 없던 6개 서울 노선에도 같은 처리를 적용하여 **총 9개 노선**이 `roadnet_pattern_estimate`가 됐다. 서버 `/api/d/sources`는 서울 버스 선형 130개 중 이 9개를 따로 집계한다. 결과 SHA-256은 `108fed78db12c700d78570d3bf36f61503c7ebbb92efff2446224ac1bcc9bf17`이다.

지도는 같은 버스 유형과 같은 도로를 지나는 중복 선을 합치고, 시내·마을·경기처럼 유형이 다른 선은 몇 픽셀씩 비켜 그린다. 같은 노선의 왕복 패턴이 같은 길을 쓰면 한 선을 남기고, 방향별로 다른 도로를 지나는 구간은 같은 색을 연하게 그린다. 도로망이 없는 구간은 여전히 빈 구간이며 임의의 직선으로 이어 붙이지 않는다. 도로별 버스 통행 가능 여부, 공식 운행 선형, 패턴별 실시간 운행 여부는 검증되지 않았다.

새 자료를 재생성하려면 위 명령의 `--source`를 `roadnet-patterns`로, `--out`을 `derived/d_bus_roads_gangdong_patterns_verified.geojson`으로 바꾸고 `--route-id 124900001 --route-id 124900003`을 추가한다. 먼저 이전 파일을 새 출력 파일로 복사해 `--append`로 기존 선형을 보존한다. 데이터 파일은 저장소 밖에 두며 Git에 추가하지 않는다.
