# 교통수단 제외 조건별 정류장 수요 계약

## 목적

운영자 시뮬레이션에서 시내버스·지하철·마을버스·경기버스 표시를 끄면 조건별 정류장 수요를 조회한다. 기본 C/B·M05 집계는 마을·경기버스 접근을 반영한 `affected_maeul_gg` 조건으로 고정한다. 별도 결과가 없으면 기본 수요 원을 **참고값**으로 유지하고 제외 효과가 미반영이라고 표시한다. 서울 전체 지도는 경계·구별 비율만 표시하므로 정류장 원은 자치구 선택 후 확인한다.

저장된 C/B `demand_bins.csv`에는 `affected`, `affected_maeul`, `affected_gg`, `affected_maeul_gg` 네 접근 조건이 있다. 마을·경기버스 체크 상태에 따라 해당 파일의 **셔틀 투입 전 잔여 수요**를 조회한다. 이는 접근 가능 수요를 제외한 근사이며 수단을 실제로 중단해 승객을 재배정한 결과나 M05 미충족 수요는 아니다. 시내버스·지하철 제외 효과와 두 수단을 섞은 정확한 효과에는 아래 별도 결과 파일이 필요하다.

## 연결

집계 파일은 저장소 **밖**에 두고 서버 시작 시 연결한다. 파일을 바꾸면 서버를 다시 시작한다. 원시 교통카드 행, 개인 식별자, 인증키와 ZIP을 저장소·테스트·로그에 넣지 않는다.

```powershell
py -3 web/operator/serve.py --a-gtfs "C:\path\to\a-gtfs.zip" --transport-demand "C:\outside-repo\transport-demand.json"
```

`GET /api/d/simulation/transport-demand?region=guro&fleet=11&plan=ours&period=exp&date=2026-10-26&disabled_modes=city,metro`가 정확한 조건을 조회한다. 선택 결과에 `network_snapshot_id`가 있으면 화면이 함께 전달한다. 파일의 스냅샷과 다르면 기본 원을 참고값으로 표시한다. `/api/d/sources`의 `transport_demand`에서 연결 상태와 시나리오 수를 확인한다.

## 파일 형식 `d-transport-demand/1`

아래는 **구조만 보여 주는 빈 템플릿**이다. `stops`에 계산 결과를 채우기 전에는 원이 표시되지 않는다.

```json
{
  "schema": "d-transport-demand/1",
  "scenarios": [
    {
      "region": "guro",
      "fleet": 11,
      "plan": "ours",
      "period": "exp",
      "date": "2026-10-26",
      "disabled_modes": ["city", "metro"],
      "kind": "M05_unserved",
      "source": {
        "network_snapshot_id": "REPLACE_WITH_C_NETWORK_SNAPSHOT",
        "input_bundle_id": "REPLACE_WITH_DEMAND_BUNDLE_ID",
        "run_id": "REPLACE_WITH_M05_RUN_ID"
      },
      "stops": []
    }
  ]
}
```

| 필드 | 내용 |
| --- | --- |
| `region`, `fleet`, `plan`, `period`, `date` | 현재 시뮬레이션 선택과 **모두 일치**해야 한다. `period`는 `exp` 또는 `test`다. 기본 날짜 `mean`을 지원하려면 실제 날짜별 결과에서 만든 `date: "mean"` 항목도 별도로 제공한다. 자동으로 평균을 추정하지 않는다. |
| `disabled_modes` | `city`, `village`, `gyeonggi`, `metro` 중 제외 수단의 **전체 조합**. `city`와 `metro`를 함께 끄면 `['city','metro']` 결과가 필요하다. 각 단일 결과를 더하지 않는다. |
| `kind` | `pre_shuttle_residual`은 셔틀 투입 전 잔여 수요, `M05_unserved`는 해당 조건으로 M05를 실행한 뒤 미충족 수요다. 화면은 출처를 구분해서 표시한다. |
| `source` | 사용한 C 교통망 스냅샷과 수요 입력 번들 ID를 필수로 기록한다. `M05_unserved`에는 `run_id`도 필수다. `generated_at`, `method`를 추가할 수 있다. |
| `stops` | 동일 시나리오에서 `stop_id`는 중복할 수 없다. 각 항목에 `stop_id`, 선택적인 `name`, `lon`, `lat`, `hours`를 넣는다. 좌표는 해당 C 정류장과 검증해 매핑한다. |

`M05_unserved`의 정류장 항목은 시간칸별 `total`과 `unserved`를 갖는다:

```json
{"stop_id":"C_STOP_ID","name":"정류장 이름","lon":126.88,"lat":37.49,"hours":{"07":{"total":12,"unserved":5}}}
```

`pre_shuttle_residual`은 시간칸별 잔여 인원만 갖는다:

```json
{"stop_id":"C_STOP_ID","lon":126.88,"lat":37.49,"hours":{"07":12}}
```

위의 숫자는 **형식 예시**일 뿐 실제 결과가 아니다. 서버는 `06`~`09`의 음수·무한대·미충족이 평가 수요보다 큰 값, 중복 정류장, 허용되지 않은 필드를 거부한다. 전체 시간 수치는 시간칸 합으로 계산한다. 화면은 기존 결과와 같이 5명 미만 시간칸을 숨긴다.

## 결과 생성 시 필요한 작업

1. 수단별 운행 공급을 바꾼 시나리오를 C/B 입력 생성 파이프라인에서 다시 만든다. 지하철·시내버스 제외에 따른 승객의 이동 포기, 다른 수단 선택, 시간대 이동을 반영할 수 있는 수요 입력 또는 명시한 전환 모델이 필요하다.
2. 각 **수단 조합 × 자치구 × 날짜 × 차량 × 운행안**을 별도로 계산한다. `pre_shuttle_residual`은 수요 생성 결과, `M05_unserved`는 해당 입력으로 실행한 M05의 정류장별 출발지 결과에서 집계한다. 실제 운행·승차 관측값이라고 표기하지 않는다.
3. C 정류장 ID와 좌표, 스냅샷·입력 번들·실행 ID를 검증한 뒤 위 JSON을 생성한다. 개인별 행은 내보내지 않는다.
4. 파일을 `--transport-demand`로 연결한다. 화면에서 수단을 끄고 해당 날짜를 선택하면 조건별 원으로 바뀐다. 결과가 없는 조합은 `not_configured`, `missing`, `snapshot_mismatch` 중 하나로 표시된다. 마을·경기버스 조건이 있으면 C/B 접근 조건의 잔여 수요를 대신 보여 주고, 시내버스·지하철 효과는 미반영이라고 표시한다. 그 외에는 기본 수요 원을 참고값으로 유지한다. 모두 켜면 기본 `affected_maeul_gg` 원으로 돌아간다.

상단 C/B 충족률·합계는 기본 `affected_maeul_gg` 결과로 유지한다. 수단 제외 결과의 상단 지표까지 바꾸려면 같은 조건의 검증된 M05 집계·M07 결과를 별도 계약으로 연결해야 한다.
