# M08 D 운영자 로컬 API

`d_dashboard.py`, `d_geo.py`, `d_scenario.py`는 D팀 운영자 화면의 로컬 개발용 API·자료 어댑터다. 합성 fixture와 선택적 A GTFS ZIP을 읽어 요약·노선·지도·수요 조회 및 합성 시나리오 재계산을 제공한다. 외부 인터페이스, 인증, M08 공용 계약으로 승인된 구현은 아니다.

저장소 루트에서 다음처럼 실행한다:

```powershell
py -3 web/operator/serve.py
```

기본 주소는 `http://127.0.0.1:8766/`이다. 화면·입력·검사 방법은 [웹 실행 안내](../../../web/README.md)에 있다. 테스트는 저장소 루트에서 실행한다:

```powershell
py -3 -B -m unittest discover -s tests/api -p "test_*.py" -v
```

A ZIP은 로컬 경로로만 전달하고 저장소에 복사하지 않는다. A의 실제 OD, B 셔틀 일정/시뮬레이션, C 후보안, M07 독립 검증은 미연결 상태다. 합성 숫자는 실제 수요·운행·평가 결과로 해석하지 않는다.
