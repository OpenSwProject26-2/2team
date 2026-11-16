# M05 시뮬레이터

설계: [docs/design/simulator.md](../../../docs/design/simulator.md) · 출력 계약: [docs/design/m07-output-contract.md](../../../docs/design/m07-output-contract.md) · 진행 기록: [docs/design/simulator-progress.md](../../../docs/design/simulator-progress.md)

2단계(핵심 엔진)·4단계(셔틀 GTFS)·5단계(C 연결, S(n) 곡선)·6단계(M07 출력 형식 통일)까지 구현했다. 표준 라이브러리만 쓰고, 엔진 안에서 난수·시계를 쓰지 않는다. 독립 검증은 팀 공식 M07 검증기(`busstrike.evaluation.verify`)로 한다. 공식 출력이 그 검증기가 읽는 폴더 형식이다.

| 파일 | 책임 |
| --- | --- |
| `models.py` | 불변 dataclass(`Passenger`·`BinRow`·`ServiceTrip`·M07 `Event` …), `SimError` |
| `io.py` | 입력 검증(§0.3·§0.4), JSON/JSONL/CSV 직렬화, `demand_bins.csv`, SHA-256 |
| `traveltime.py` | 시간대별 구간 시간 조회(§3.5) |
| `timetable.py` | 시간표 생성기·검사·`normalize_plan`(§1) |
| `arrivals.py` | 시간대별 인원(체계적 반올림) + M07 생성기 `uniform_bin_sha256_v1`, `run_inputs.json`(§2) |
| `engine.py` | 시간표 → M07 운행편(`service_schedule`), 고정 일정 이벤트 엔진(§3·§4) |
| `eventlog.py` | M07 사건 로그·승객 결과 직렬화(§6.2) |
| `metrics.py` | 사건 로그에서 승객 결과·M07 지표 계산(§6.3) |
| `gtfs.py` | `timetable.json` → 셔틀 GTFS 내보내기·다시 읽기(§6.4) |
| `run.py`, `__main__.py` | 디렉터리 단위 실행 → M07 폴더(`scenario/`·`run/`), `measurement.json`(§6) |
| `curve.py` | 노선별 대수 곡선 S(n)·한계효용, 정류장 공유 클러스터(§7.1·§7.2) |
| `adapters/planning_bridge.py` | C `Inputs`·`Plan` → 시뮬 입력, `EvaluationPort`(M07 검증 포함), 근사–시뮬 비교표(§7.3·§7.5). `busstrike.planning`을 import하는 유일한 곳 |
| `adapters/comparison_plan.py` | 비교 입력 `plan.json`(`experiments/plans/schema.md`) → S1–S6 검사 + M05 운행안(§1.8) |

```sh
PYTHONPATH=src python3 -m busstrike.sim.adapters.comparison_plan --plan experiments/plans/plan-01/plan.json \
    --scenario scenario.json --travel-times travel_times.json --out plan.json   # 비교 입력 → M05 운행안(0 성공·1 검사 실패)
PYTHONPATH=src python3 -m busstrike.sim run --scenario scenario.json --run-inputs run_inputs.json \
    --plan plan.json --out out/                     # → out/scenario/, out/run/
PYTHONPATH=src python3 -m busstrike.evaluation.verify out/run          # M07 독립 검증(종료 코드 0 = 통과)
PYTHONPATH=src python3 -m busstrike.sim gtfs --scenario scenario.json --timetable out/run/timetable.json --out out/gtfs/
PYTHONPATH=src python3 -m busstrike.sim.adapters.planning_bridge --input tests/fixtures/planning/synthetic.json --out out/bridge/
PYTHONPATH=src:. python3 -m unittest discover -s tests -t . -v
```

M07 출력이 받지 않는 입력(`OUT_OF_SCOPE`): 1인당 정차가 0이 아닌 정책, 영업 leg가 2개 이상인 노선(두 역 연결형), 환승. 승객 OD는 정류장 1개 → 1개다.

시뮬레이션 결과는 실제 운행 성능·승인 근거가 아니다.
