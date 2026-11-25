# 노선 생성기 실행에 필요한 데이터

> 이 데이터를 읽어 2~8단계를 한 번에 돌리는 진입점은 [pipeline.md](pipeline.md)에 있다.

노선 생성기(`experiments/routegen_lab/walk_eval.py`)와 평가(`eval_rules.py`, `strike_eval.py`, `smoke_integrated.py`)가 읽는 데이터를 정리한다. 설계와 결정은 [routegen-design.md](routegen-design.md)에 있다.

**데이터는 Git에 올리지 않는다.** 모두 저장소 밖 작업 폴더(아래 `W`)에 둔다. 경로는 `W` 기준이다(구로 예시이고, 다른 구는 `guro`를 그 구 이름으로 바꾼다). `strike_eval.py`·`smoke_integrated.py`는 환경 변수 `ROUTEGEN_WORK`를 `W`로 쓴다.

## 1. 한눈에

| # | 데이터 | 경로(`W` 기준) | 구하는 법 | 생성 | 평가 | 크기(구로) |
|---|---|---|---|---|---|---|
| 1 | 구별 시뮬레이터 입력 묶음 v4, 실험 주간 | `region_all/ref/<구>_0610_v4/` | `region_bundles_0610_v4.zip` 풀기 | 필수 | 필수 | 19MB |
| 2 | 같은 묶음, 테스트 주간 | `region_all/ref/<구>_0610_v4_test/` | 같은 zip | 선택(평가 규칙 역 색인 보강) | 테스트 주간 평가 때만 | 19MB |
| 3 | OSM 도로·구 경계 | `osm/<구>_roads.json`, `osm/<구>_boundaries.json` | `fetch_osm.py`(한 번) | 필수 | 필수 | 9MB |
| 4 | 버스 통행 가능 도로망 | `review1119/stage1c/nets/<구>/road.json`, `turns.json` | `build_nets_all.py` | 필수 | 필수 | 3MB |
| 5 | 생성 입력 폴더(sweep) | `review1119/stage1c/sweep/<구>/` | `hub_sweep.py` | 필수 | 필수 | 87MB |
| 6 | 필수 시설 CSV | `routegen_v2/facilities/facilities_seoul.csv` | `facilities_seoul_v1.zip` 풀기 | 필수 정류장을 쓸 때 | — | 43KB |
| 7 | 지자체안 | `municipal/<구>/plan.json` → `review1119/stage2/muni/muni07/` | 전사(#40) → `eval_municipal.py` | 형평 하한(`--equity-floor muni`) | 지자체안 비교 | 7.5MB |
| 8 | 도보 이웃 캐시 | `review1119/stage2/eval/nb400.json`, `review1119/stage3/equity/nb800_home.json` | `eval_rules.py`가 OSM으로 만든다 | — | 선택(없으면 다시 잼) | 0.5MB |
| 9 | AI안 6개 | `routegen_v2/strike_curve/ai/ai05`~`ai10/` | sol 제출물을 `eval_municipal.py`와 같은 규칙으로 이은 것 | — | 파업 비율 평가(`strike_eval.py`)만 | |

저장소 안에 있는 입력:
- `experiments/routegen_lab/gu_params.json`: 구별 설정. 입력만 구별로 덮어쓰고, 규칙·정책 상수는 고정한다(설계 15절).
- `experiments/routegen_lab/station_lines.csv`: 역 → 호선·지선 표. 평가 규칙의 같은 호선 대체 역에 쓴다.

## 2. 항목별

### 1·2. 구별 시뮬레이터 입력 묶음 v4 (`<구>_0610_v4`, `_test`)

M05가 바로 돌리는 구 입력이다.
- 수요 날짜: 실험 10/26~30, 테스트 11/02~06.
- 운영 06:00~10:00, 차량 가용 11:00까지.
- `D.check_root`가 README 주간 문구, 망 ID(`<구>-buf2km-0610-v2`), 묶음 날짜를 확인하고, 다르면 멈춘다.

생성기가 읽는 파일:

| 파일 | 쓰는 곳 |
|---|---|
| `README.md` | 주간·구 이름·대수 확인(`data.check_root`) |
| `network/stops.csv`, `network/stations.csv`, `network/station_stops.csv` | 정류장·역·역 지정 정류장(`data.load_network`, 흡수의 역 주변 정류장) |
| `network/travel_times.json` | 망 ID 확인, 도로망 만들 때 정류장 기준 |
| `network/absorbing_routes.csv` | 마을·경기버스 정차 순서·운행 수(정밀 흡수, `absorb.py`) |
| `demand/demand_flows.csv.gz` | 셔틀 대상 수요 모집단(도보 불가 + 역 지정 정류장 있음, `data.load_flows`) |
| `demand/stop_route_boardings.csv.gz` | 마을버스 남는 정원(이미 탄 인원, `absorb.py`) |
| `registry/strike_route_registry.csv`, `gtfs/routes.txt`, `trips.txt`, `stop_times.txt` | 파업 노선 집합·부분 파업 뽑기(`strike.py`), 필수 정류장의 계속 운행 노선 판정(`must_stops.py`) |
| `bundles/affected_<날짜>/scenario_f*.json`, `run_inputs_f*.json` | 정책·운행 창·차량 본, 수요 실현 seed(평가 묶음 `strike_eval.scenario_bundles`) |

### 3. OSM 도로·구 경계
`python3 experiments/routegen_lab/fetch_osm.py --root W/region_all/ref/<구>_0610_v4 --out W/osm/<구>_roads.json --boundaries W/osm/<구>_boundaries.json`

쓰는 곳:
- 버스 통행 가능 도로망 만들기(`roadnet.py`)
- 동네 쪽 도보 이웃(400m, 800m)
- 지자체안 이음

### 4. 버스 통행 가능 도로망 (절대 조건)
`python3 experiments/routegen_lab/build_nets_all.py --work W --out W/review1119/stage1c --parallel 4`

- 결과는 `nets/<구>/{road.json, turns.json, roadnet_stats.json, snap.json}`이다.
- **노선 생성과 시뮬레이션은 이 망(road.json + turns.json)만 쓴다.** 묶음 원래 구간망(`network/travel_times.json`)으로 생성하지 않는다(설계 1절).

### 5. 생성 입력 폴더 (`sweep/<구>`)
`python3 experiments/routegen_lab/hub_sweep.py --region W/region_all/ref --osm-dir W/osm --gu <구> --out W/review1119/stage1c/sweep/<구> --configs hub135_s12`

`walk_eval.py --sweep`가 읽는 것:
- `hub_sweep.json`: 대수 목록(입력), 흡수 기록
- `net/road.json`, `net/turns.json`: 4와 같은 망
- `exp/bundles/<묶음>/`: 정책·운행 창만 쓴다
- (있으면) `muni/`

`smoke_integrated.py`는 4의 망과 1의 묶음으로 이 폴더를 직접 만든다(`sweep_dir`). 그래서 지자체안이 없는 구는 `hub_sweep.py` 없이도 돌릴 수 있다.

### 6. 필수 시설 CSV
`facility_id,kind,type,name,gu,lat,lon`(병원급 이상, 보건소·보건지소, 전통시장).
- `--facilities`로 준다. 반경 200m 안의 정류장 중 계속 운행 노선이 없는 곳이 필수 정류장이다(`must_stops.py`).
- 주지 않으면 필수 정류장 제약 없이 생성한다.

### 7. 지자체안
- 전사한 `municipal/<구>/plan.json`을 `eval_municipal.py --links osm --service-start 07:00`로 잇는다. 결과 폴더(`plan.json`, `road_muni.json`, `muni.json`)를 `walk_eval.py --muni muni07=<폴더>`로 준다.
- 지자체안이 없는 구는 `--equity-floor none`으로 형평 하한을 끈다(현재 구로만 있음).

### 8. 도보 이웃 캐시
- `eval_rules.py` 명세의 `nb`(400m), `nb_far`(800m, 동네 정류장)이다.
- 없으면 OSM으로 다시 재서 결과 폴더에 저장한다. 결과는 같고 시간만 든다.

### 9. AI안
`strike_eval.py --ai-root`의 하위 폴더 `aiXX/{plan.json, road_muni.json}`이다. 파업 비율 평가에서 비교용으로만 쓴다.

## 3. 실행 예 (구로)

```bash
W=<작업 폴더>
# 생성(전면 파업, 07:00, 11대, 필수 시설, 지자체안 형평 하한)
python3 experiments/routegen_lab/walk_eval.py --sweep $W/review1119/stage1c/sweep/guro --region $W/region_all/ref \
  --osm $W/osm/guro_roads.json --gu guro --multi --loop demand --service-start 07:00 --fleets 11 \
  --facilities $W/routegen_v2/facilities/facilities_seoul.csv --muni muni07=$W/review1119/stage2/muni/muni07 \
  --only muni07,eqmc07 --no-run --out <결과 폴더>
# 3개 구 스모크(10/28 하루)
python3 experiments/routegen_lab/smoke_integrated.py --out <결과 폴더> --gus guro,gangseo,gangdong
# 파업 비율 평가(실험 주간, 10~100% × 뽑기 5)
python3 experiments/routegen_lab/strike_eval.py --out <결과 폴더> --gu guro
python3 experiments/routegen_lab/strike_summary.py --dir <결과 폴더>
```

부분 파업은 `--strike-rate 0.5 --strike-draw 0`이다. 이때 파업 노선 집합·셔틀 대상 수요는 1의 `registry`·`gtfs`·`demand`에서 다시 만든다.

## 4. 주의
- 설정을 고정하기 전에는 테스트 주간(`_test`)으로 판정하지 않는다.
- 결과물(`plan.json`, `plan_report.json`, 시뮬레이션 실행 폴더)도 커밋하지 않는다. `events.jsonl`, `passenger_outcomes.csv`는 크므로, 지표를 뽑은 뒤 지운다(`strike_eval.py`가 자동으로 지움).
- 흡수 정원·혼잡 값(`AbsorbParams`: 마을 35석·0.6, 경기 60석·0.6)은 자료가 없어 둔 가정이다. 셔틀 대상 수요는 이 값에 달려 있다.
