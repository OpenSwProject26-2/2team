# 운영자 대시보드 인수인계: 충족률–형평성 경계(파레토 곡선)

2026-11-26 기준. 파이프라인([pipeline.md](pipeline.md))이 내는 `pareto.json`을 운영자 대시보드에 그리려는 사람을 위한 문서다. **대시보드에 어떻게 연결할지(읽는 위치, 화면 구성, 기존 `routegen-results/1` manifest와의 관계)는 대시보드 담당이 정한다.** 이 문서는 파일이 무엇을 담고, 무엇을 그리면 되고, 무엇을 주의해야 하는지만 적는다.

## 1. 한 줄 요약

한 구·한 조건(파업 범위, 가용 대수, 평가 날짜)에서 우리 알고리즘 12종 × 형평 기준 여러 단계로 만든 운행안마다 **충족률**과 **형평성**을 잰 점들이 있다. 그중 다른 안에 지배당하지 않는 점(`hull: true`)을 이으면 경계가 된다. 지자체안은 비교용 점 하나다.

- 이 경계는 우리 안끼리의 **경험적 비지배 경계**다. 이론적 파레토 곡선이 아니다. 화면 제목·설명에서 "최적 곡선"이라고 쓰지 않는다.

## 2. 파일 위치

파이프라인 실행 하나(`--out <폴더>`)마다 다음이 생긴다. 데이터·결과는 저장소 밖에 있고 Git에 없다.

```
<out>/
  run_config.json        입력(구, 파업 범위, 대수, 알고리즘, 형평 기준, 평가 날짜, 데이터 경로, 코드 커밋)
  pareto.json            ← 대시보드가 읽을 파일
  export/<plan id>.json  안마다 지도용 운행안(노선·정류장·도로 모양), pareto.json의 export_path가 가리킴
  gen/…, eval/…, metrics/…  중간 산출물(대시보드는 읽지 않아도 된다)
```

- 저장소 안 예시: [`tests/fixtures/pipeline/pareto_guro_smoke.json`](../../tests/fixtures/pipeline/pareto_guro_smoke.json)(안 3개만 남긴 것), [`tests/fixtures/pipeline/export_guro_smoke.json`](../../tests/fixtures/pipeline/export_guro_smoke.json)(노선 1개, 도로 모양 점을 줄인 것). 화면 개발은 이 두 파일로 시작하면 된다. 이 예시는 하루(10/28)·형평 기준 없음으로 돌린 스모크라 **숫자로 성능을 말하면 안 된다**.

## 3. 그리는 법

| 요소 | 값 | 비고 |
|---|---|---|
| 가로축 | `equity_far` (형평성, 역 800m 밖) | 0~1. 화면에는 %로 |
| 세로축 | `served_rate` (충족률) | 0~1. 화면에는 %로 |
| 우리 안 점 | `plans[]` 한 항목 = 점 하나 | 색 = `base`(섹터 순회·피더 순회·편도 노선 생성 후 순회), 모양 = 옵션 조합(`line_group`, `fill`) 권장 |
| 경계 | `hull: true`인 점을 `equity_far` 오름차순으로 이음 | 점 사이를 선으로 이어도 되지만, **점 사이 값의 안은 없다**(보간한 안이 있는 것처럼 보이지 않게, 계단선이나 점선 권장) |
| 지자체안 | `muni` | 별도 기호(예: 별). hull 계산에 넣지 않았다 |
| 점을 눌렀을 때 | 아래 4절 지표 표 + `export_path`의 운행안 지도 | `export_path`는 `<out>` 기준 상대 경로 |

지배 규칙(코드 `src/busstrike/pipeline/pareto.py`): 다른 우리 안이 충족률 ≥ 그리고 형평성 ≥ 이고 둘 중 하나는 엄격히 크면 지배당한다(`hull: false`). 값이 완전히 같은 두 점은 둘 다 `hull: true`다. 지표가 없는 안(M07 미통과 등)은 `hull: false`다. **대시보드에서 hull을 다시 계산할 필요는 없다.** 필터(예: 한 후보 종류만 보기)를 걸어 그 안에서 경계를 다시 그리고 싶다면 같은 규칙을 쓴다.

## 4. pareto.json 필드

맨 위:
| 필드 | 뜻 |
|---|---|
| `gu`, `fleet`, `strike`, `eval_days` | 구(영문 ID), 가용 대수, 파업 범위(1.0 = 전면), 평가 날짜(YYYYMMDD 목록) |
| `demand_total` | 셔틀 대상 수요(명/일, 평가 날짜 평균) |
| `plans` | 우리 안 목록 |
| `muni` | 지자체안 지표(같은 필드, `hull` 없음). 평가 실패면 `null`이고 `muni_excluded`에 이유 |
| `excluded` | 지표를 못 낸 안과 이유(예: "M07 통과 3/4"). 화면에 "제외된 안 N개"로 보여 주면 좋다 |

`plans[]` 한 항목:
| 필드 | 뜻 | 화면 표기 예 |
|---|---|---|
| `id` | `<알고리즘 ID>__floor_<기준>`(같은 plan이 여러 기준에서 나오면 기준을 `+`로 이음) | 내부 키 |
| `algo_name` | 알고리즘 한국어 이름(문서·코드·출력에서 같음) | "피더 순회 + 호선" |
| `base` | sector \| feeder \| oneway_loop | 섹터 순회 / 피더 순회 / 편도 노선 생성 후 순회 |
| `line_group`, `fill` | 호선 묶기, 소외 우선 삽입 켬 여부 | 옵션 표시 |
| `floors` | 이 plan을 낸 형평 기준들: `none`(하한 없음), `muni`(지자체안 값), 숫자(역 밖 형평 하한 절대값, 예 `0.58`), `+x` | "하한 0.58" |
| `served_rate` | 충족률 = 도보 반영 탑승 ÷ 셔틀 대상 수요 | 37.5% |
| `equity_far` | 형평성(역 800m 밖 동네 수요 중 탈 수 있게 된 몫) | 49.0% |
| `isolation` | 소외율 = 1 − equity_far | 51.0% |
| `time_ratio_rode` | 원래 버스 대비 시간, 탄 승객 중앙값((도보 + 대기 + 차내) ÷ 원래 버스 대기 + 차내) | 1.26배 |
| `time_ratio_common` | 같은 승객 기준(지자체안에서도 같은 날 탄 승객) 중앙값 | 1.64배 |
| `refused_unserved` | 만차로 끝내 못 탄 승객(명/일) | 555명 |
| `reasons` | 못 탄 사유(명/일): `noconn` 근처에 셔틀 정류장이나 역 쪽 노선 없음, `order` 정류장은 있으나 같은 노선·방향 순서가 안 맞음, `wait` 대기 한도 초과, `refused` 만차 | 막대 그래프 |
| `routes`, `cycle_sum_min` | 운행 노선 수, 순환 시간 합(분) | 6개, 169.9분 |
| `floor_met`, `short_far` | 배분기 형평 하한을 맞췄는지, 역 밖 부족분(명). 못 맞춘 조합도 최선안이 들어 있다 | "하한 못 맞춤(부족 120명)" |
| `floor_check` | 기준별 `floor_met`·`short_far`(같은 plan이 여러 기준에서 나왔을 때) | |
| `plan_sha256` | 운행안 파일 해시. 같은 값이면 같은 운행안 | |
| `hull` | 경계 위 점인지 | |
| `export_path` | 지도용 운행안 파일(지표가 없으면 `null`) | |
| `m07` | 평가 날짜 중 M07 통과 수 | "4/4" |

비율은 0~1 소수(4자리 반올림), 사람 수는 하루 평균(소수일 수 있음)이다.

## 5. export/<plan id>.json (점을 눌렀을 때 지도)

```json
{"plan_id": "feeder_lg0_fill0__floor_none",
 "routes": [{"route_id": "r01", "name": "r01 대림역3번출구 순환(남구로역 경유)", "vehicles": 2,
             "cycle_min": 24.92, "headway_min": 12.46,
             "stops": [{"seq": 1, "stop_id": "…", "name": "…", "lat": 37.49, "lon": 126.89, "kind": "station"}, …],
             "shape": [[37.49, 126.89], …], "shape_straight": 0}]}
```
- 모든 노선은 순환이다. `shape`는 정류장 사이 실제 버스 도로 좌표이고, 마지막 정류장 → 첫 정류장 구간까지 들어 있다. 좌표 순서는 **[위도, 경도]**다(GeoJSON은 [경도, 위도]이니 뒤집어 쓴다).
- `kind: station` = 역에 붙은 정류장. `shape_straight` = 도로 경로를 못 찾아 직선으로 이은 구간 수(지금 구로 결과는 모두 0).
- 카드 ID는 어떤 출력에도 없다.

## 6. 주의할 것

1. **한 pareto.json = 한 조건.** 구·대수·파업 범위·평가 날짜가 다르면 점끼리 비교하지 않는다(파일을 나눠 고르게 한다).
2. 알고리즘이 달라도 같은 운행안이 나올 수 있다(`plan_sha256`이 같음. 예: 스모크에서 "피더 순회 + 소외" = "피더 순회"). 둘 다 점으로 남겼으니 겹쳐 그려질 수 있다.
3. 형평 기준 단계를 올려도 점이 같은 자리에 머물 수 있다(같은 plan이면 `floors`에 기준이 모인다).
4. 지자체안이 경계 아래에 있다고 화면에서 "우리 안이 낫다"고 단정하지 않는다. 같은 날짜·같은 규칙으로 잰 비교일 뿐이고, 판단 문구는 실험 보고서를 따른다.
5. 지표는 파이프라인이 M05·M07 결과로 이미 계산했다. 대시보드에서 다시 계산하지 않는다.

## 7. 다시 만들기

```bash
PYTHONPATH=src python3 -m busstrike.pipeline --gu guro --strike 1.0 --fleet 11 --algos all \
  --floors none,0.50,0.52,0.54,0.56,0.58,0.60,0.62,0.64 --eval-days 2026-10-27..30 \
  --data-root <W> --sweep <W>/routegen_integrated/improve_1122/final/all25/guro/sweep \
  --muni <W>/routegen_integrated/improve_1122/final/all25/guro/muni07 --out <결과 폴더>
```
`--stages pareto,export`만 주면 이미 끝난 평가로 pareto.json과 export만 다시 만든다. 형식이 바뀌면 [pipeline.md](pipeline.md) 5절과 이 문서를 같이 고친다. 문의: 파이프라인 담당(routegen).
