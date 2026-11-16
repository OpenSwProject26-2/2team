# M06 시민 경로 검색

현재 구현은 D팀이 만든 **합성 GTFS 개발 미리보기**다. 직행 및 동일 정류장 기준 최대 1회 환승을 검색하고, 정상 GTFS 예제만 읽는 도달 가능 조회도 제공한다. A/B의 실제 시간표·실제 파업 영향·보행·철도 환승·좌석 가능성을 판정하지 않으며, M08 공용 API 계약이나 시민용 실시간 안내가 아니다.

## 실행

저장소 루트에서 시민 미리보기 서버를 실행한다:

```powershell
py -3 web/citizen/serve.py
```

브라우저에서 `http://127.0.0.1:8765/`를 연다. 합성 입력은 [GTFS fixture](../../../tests/fixtures/routing/gtfs_preview/)에 있으며, 정상 버스 전용 CLI 조회는 다음과 같다:

```powershell
py -3 -m pip install -e .
py -3 -m busstrike.routing.normal_reachability synthetic-demand-1 A C 2026-11-07T00:00:00+09:00
```

서버 wrapper는 `src`를 자동으로 import 경로에 추가하므로 editable install 없이 실행할 수 있다. CLI를 위해 editable install을 하지 않으려면 PowerShell에서 `$env:PYTHONPATH = (Resolve-Path .\src).Path`를 먼저 설정한다.

## 검증

```powershell
py -3 -B -m unittest discover -s tests/routing -p "test_*.py" -v
py -3 -m ruff check src/busstrike/routing tests/routing web/citizen/serve.py
py -3 -m ruff format --check src/busstrike/routing tests/routing web/citizen/serve.py
```

검색기는 합성 입력과 그 제한을 응답에 표시한다. 실제 자료 연동 전에는 A의 정류장 ID namespace·정정된 시간표·서비스일, B의 셔틀 일정 및 검증 참조, 환승/보행 정책과 선택된 `(plan_id, revision)` 계약이 필요하다. 자료가 없으면 실제 도달 가능성이나 미충족 수요를 추정하지 않는다.
