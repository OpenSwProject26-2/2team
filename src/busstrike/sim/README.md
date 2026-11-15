# M05 시뮬레이터

설계: [docs/design/simulator.md](../../../docs/design/simulator.md) · 진행 기록: [docs/design/simulator-progress.md](../../../docs/design/simulator-progress.md)

2단계(핵심 엔진)·4단계(셔틀 GTFS)까지 구현했다. 표준 라이브러리만 쓰고, 엔진 안에서 난수·시계를 쓰지 않는다.

| 파일 | 책임 |
| --- | --- |
| `models.py` | 불변 dataclass, `SimError` |
| `io.py` | 입력 검증(§0.3·§0.4), JSON/JSONL 직렬화, SHA-256 |
| `traveltime.py` | 시간대별 구간 시간 조회(§3.5) |
| `timetable.py` | 시간표 생성기·검사·`normalize_plan`(§1) |
| `arrivals.py` | 승객 발생 실현기(체계적 반올림), `run_inputs.json`(§2) |
| `engine.py` | 이벤트 엔진(§3·§4) |
| `eventlog.py` | 로그 정렬 키·직렬화(§6.1) |
| `metrics.py` | 로그에서 지표 계산(§6.3) |
| `gtfs.py` | `timetable.json` → 셔틀 GTFS 내보내기·다시 읽기(§6.4) |
| `run.py`, `__main__.py` | 디렉터리 단위 실행, `run.json`(§6.2) |

```sh
PYTHONPATH=src python3 -m busstrike.sim run --scenario scenario.json --run-inputs run_inputs.json \
    --plan plan.json --out out/
PYTHONPATH=src python3 -m busstrike.sim gtfs --scenario scenario.json --timetable out/timetable.json --out out/gtfs/
PYTHONPATH=src python3 -m unittest discover -s tests -t . -v
```

시뮬레이션 결과는 실제 운행 성능·승인 근거가 아니다.
