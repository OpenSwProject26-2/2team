# M08 D 운영자 로컬 API

`d_dashboard.py`, `d_geo.py`, `d_scenario.py`, `d_network.py`, `d_transit_data.py`, `d_simulation.py`는 D팀 운영자 화면의 로컬 개발용 API·자료 어댑터다. 합성 fixture, 선택적 A GTFS ZIP, `seoul_transit_data` 및 저장소 밖 C/B/M07 결과를 읽어 지도·수요·실험 비교를 제공한다. 외부 인터페이스, 인증, M08 공용 계약으로 승인된 구현은 아니다.

저장소 루트에서 다음처럼 실행한다:

```powershell
py -3 web/operator/serve.py
```

기본 주소는 `http://127.0.0.1:8766/`이다. 화면·입력·검사 방법은 [웹 실행 안내](../../../web/README.md)에 있다. 테스트는 저장소 루트에서 실행한다:

```powershell
py -3 -B -m unittest discover -s tests/api -p "test_*.py" -v
```

A ZIP·교통 데이터셋·실행 원본은 로컬 경로로만 전달하고 저장소에 복사하지 않는다. 기본 설정에서는 합성 DEMO와 PR #41 보고서의 5일 평균을 조회한다. `--transit-data`를 연결하면 C/B 입력 번들의 셔틀 투입 전 정류장별 잔여 수요를 표시한다. `--simulation-results`와 `--simulation-inputs`에 실제 결과를 연결하면 해당 실행의 C 노선·차량 배분, B M05 지표·정류장별 미충족 수요, M07 검증을 표시한다. 파일이 없는 조건은 미연결로 남긴다. 실행 예시와 파일 구조는 [웹 실행 안내](../../../web/README.md), PR 전달 참고사항은 [D 대시보드 인수인계](../../../docs/design/d-dashboard-pr-handoff.md)를 본다.
