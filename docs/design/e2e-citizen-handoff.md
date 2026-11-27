# 운영자 확정 → 시민 길찾기 반영: 시민 담당 팀원 인수인계 (2026-11-27)

운영자 대시보드(이 브랜치)는 **확정 묶음을 쓰고, 시민 서버에 반영을 요청하고, 반영 상태를 읽어 화면에 보이는 것**까지 한다.
셔틀 GTFS 만들기·파업 그래프 다시 빌드·세 결과 비교 화면은 시민 길찾기(`citizen-routing/`) 쪽 일이고, 이 브랜치는 `citizen-routing/` 아래를 **한 줄도 고치지 않는다**.
경계는 두 가지다: (1) 확정 묶음 계약, (2) `POST /api/citizen-routing/apply` 호출 규약과 상태 형식.

## 1. 확정 묶음 계약

```
<confirm_root>/<gu>/<confirm_id>/
  confirm.json          메타 + files{scenario, plan, timetable, m07_report, stops} 각각 {path(묶음 폴더 기준 상대 경로), sha256}
  scenario.json         M05 시나리오(그 안을 평가한 실행의 것, service_date = 시뮬레이션 날짜 10/26~30 중 하루)
  plan.json             운행안(파이프라인 형식, 아래 주의) + scenario_id·status "candidate"·source{plan_ref, run_id, plan_sha256}
  timetable.json        M05가 쓴 시간표(trips[].leg_index·in_service·stop_times)
  verifier_report.json  M07 보고서(pass=true, failed_checks 0. fully_verified는 false일 수 있음)
  stops.csv             stop_id, stop_lat, stop_lon, stop_name — 이 안의 모든 영업 정차점
<confirm_root>/<gu>/CURRENT   지금 적용할 confirm_id 한 줄(확정할 때마다 바뀜, 예전 묶음은 지우지 않고 쌓임)
```

- 스키마: `docs/schemas/confirm.schema.json`(JSON Schema). `confirm_id` = `YYYYMMDDTHHMMSS-<plan_sha256 앞 8자>`(KST).
- 확정 전에 대시보드가 `citizen-routing/backend/shuttle_export.export_checked(..., demo=False)`를 **같은 코드로** 임시 폴더에 돌려 통과한 것만 쓴다. 순환 검사(노선마다 영업 구간 1개 + 고리 닫는 빈 차 ≤ 300초)도 확정 전에 한다.
- 예시 묶음(이 PC): `guro_exp_work/e2e_1127/confirm_root/guro/`(실제 확정, 예: `20261127T135559-aad5fc7d` 피더 순회 탑승 우선, `20261127T145312-5368a87e` 편도 후 순회 형평 우선). 시민 쪽 작업자가 만든 스키마 형식 예시는 `guro_exp_work/e2e_1127/citizen/example_confirm_root/guro/`.

### 꼭 알아 둘 것
1. **plan_id가 안끼리 같다.** 파이프라인 안들은 대수별로 `plan-eqmc07-f11`, revision 1이 모두 같다. `plan_id@revision`만으로는 어떤 안이 적용됐는지 알 수 없으니 **confirm_id와 plan_sha256으로 구별**해야 한다(그래프 폴더 이름·활성 표시·"적용됨" 판정 모두). M05 시간표의 plan_id를 바꾸면 M05 결과를 고치는 셈이라 운영자 쪽에서 바꾸지 않는다.
2. **`plan_sha256`은 곡선(pareto.json) 위 안의 식별자**다(파이프라인 운행안 파일 해시). 묶음 `plan.json` 파일 해시와 다르다. 파일 무결성은 `files.plan.sha256`으로 본다.
3. **plan.json에 `routes[].legs`가 없다.** 파이프라인 형식(`routes[].stops`, `vehicles`)이다. `shuttle_export`는 이대로 통과하지만, 순환 여부를 plan의 legs로 보려 하면 실패한다 → 시간표 `trips`의 leg_index별 대표 운행으로 고리를 확인하면 된다(참고 구현 0d29e0f).
4. **운행일**: 시나리오 `service_date`는 시뮬레이션 날짜(10/26~30)다. A GTFS 운행일(11/02~06)과 다르므로 같은 요일로 옮겨 쓰고 원래 날짜는 따로 표시한다(참고 구현 D2). GTFS 달력 날짜만 쓰는 것이고 테스트 주간 수요·평가 자료를 여는 것이 아니다.
5. 파업 노선은 시나리오 `strike_route_ids`(서울 버스 route_id)를 A GTFS `route_id`와 **정확히 같은 것만** 쓴다(이름 추정 금지). 구로는 93/93 일치.
6. 카드 ID·승객 단위 정보는 묶음에 없고, 시민 쪽도 만들지 않는다.

## 2. 대시보드 → 시민 서버 호출 규약

설정: 대시보드 `config/local_paths.toml`(커밋 안 함)
```toml
[citizen]
api = "http://127.0.0.1:<시민 서버 포트>"   # 비우면 호출하지 않고 "시민 경로 반영 대기"
ui  = "http://127.0.0.1:<시민 서버 포트>/" # "시민 경로 화면 열기" 단추
```

### 2.1 반영 요청 (확정 직후 한 번)
```
POST {api}/api/citizen-routing/apply
Content-Type: application/json
{"gu": "guro", "confirm_id": "20261127T145312-5368a87e"}
```
- 시간 제한 5초. 반영(빌드)은 수 분 걸리므로 **바로 2xx를 돌려주고 별도 프로세스로** 진행해 주면 된다(예: `202 {"state":"started","pid":…}`).
- 거절할 때는 4xx/5xx + `{"code": "...", "message": "..."}`. 대시보드는 `message`를 "시민 경로 반영 대기(시민 서버 409: …)"처럼 그대로 보인다. 예: `409 APPLY_BUSY`, `503 APPLY_NOT_CONFIGURED`.
- 주소가 없거나 연결이 안 되면 대시보드는 오류 없이 "시민 경로 반영 대기"로 표시하고 확정은 그대로 둔다.

### 2.2 반영 상태 (대시보드가 5초마다 읽음)
```
GET {api}/api/citizen-routing/apply/status?confirm_id=<id>
```
기대 응답(필요한 키만, 더 있어도 됨):
```json
{
  "state": "idle | running | done | failed | interrupted | cancelled",
  "step": "verify | export | route_map | prepare | build | swap | presets | done",
  "requested_confirm_id": "요청받은 확정 ID(묶음을 읽기 전 실패도 알 수 있게)",
  "confirm_id": "묶음을 읽은 뒤의 확정 ID",
  "error": {"code": "...", "message": "..."},
  "active": {"confirm_id": "...", "plan_sha256": "...", "plan_key": "plan_id@revision#sha8",
             "graph_version": "...", "build_id": "...", "activated_at": "..."}
}
```
대시보드 판정(`src/busstrike/api/d_confirm.py: ConfirmStore.citizen_status`):
| 조건 | 화면 |
|---|---|
| `active.confirm_id == 이 확정` 이고 `active.plan_sha256 == confirm.json plan_sha256` 이고 state ≠ running | **시민 경로 반영 완료(파업 그래프 …)** |
| state = running 이고 (confirm_id·requested_confirm_id가 없거나 이 확정) | 시민 경로 반영 중(step) |
| state ∈ failed·interrupted·cancelled 이고 confirm_id 또는 requested_confirm_id = 이 확정 | 시민 경로 반영 실패: message (이전 그래프로 안내 중) |
| active가 다른 확정 | 시민 경로엔 다른 확정 적용 중 |
| 그 밖·응답 없음·주소 없음 | 시민 경로 반영 대기 |

## 3. 규칙
- 시뮬레이터(M05)·검증기(M07)·eval_rules는 고치지 않는다.
- 테스트 주간(11/02~06) 수요·평가 자료와 `*_test.zip`은 열지 않는다(GTFS 달력 날짜 사용은 괜찮음).
- 노선은 모두 순환(편도 구간 없음), 버스 통행 도로망(road.json + turns)만.
- 일반 그래프는 건드리지 않고 파업 그래프만 다시 만든다. 빌드 중·실패 시 이전 그래프가 계속 응답해야 한다.
- GTFS·OSM·OTP 그래프·jar·runtime/·카드 ID는 커밋하지 않는다.

## 4. 참고 구현(머지 범위 밖, 로컬 브랜치)
- `feat/e2e-citizen`(7커밋, 0b09c3e…2df3d77): `backend/apply_confirm.py`(묶음 검증 → shuttle_export → 파업 입력 → 새 폴더 빌드 → blue/green 교체), apply·status·cancel·switch·compare3·presets API, 세 결과 화면, 파업 노선 정확 ID 대응 표 도구, 문서 6절. 결과·결정: `guro_exp_work/e2e_1127/citizen/`(FINAL_REPORT.md, DECISIONS.md).
- `feat/e2e-full`의 시민 쪽 수정 3개(위 브랜치 + 운영자 브랜치 병합 83ec61a 위):
  - 32f2515 안 구별: 그래프 교체 전 셔틀 입력 해시 = 묶음 파일 해시, 상태·활성 표시에 `plan_key`·`applied_confirm_id`, `status?confirm_id=` → `applied`.
  - 0d29e0f legs 없는 운영자 plan의 순환을 M05 시간표로 검사, 상태에 `requested_confirm_id`.
- **#64(feat/e2e-citizen)만 머지하면 실제 운영자 확정 묶음은 반영 verify 단계에서 "노선 r01에 legs가 없습니다"로 거절된다**(위 주의 3). 32f2515·0d29e0f를 #64 위에 cherry-pick하면 충돌 없이 얹히고 citizen-routing unittest 56개가 통과한다(2026-11-27, 임시 분리 worktree에서 확인, 브랜치는 만들지 않음): `git cherry-pick -x 32f2515 0d29e0f`.
- 이 조합으로 한 번 끝까지 확인했다(참고): 실제 확정 `20261127T145312-5368a87e` → 반영 382초(빌드 215초, 교체 36초, 프리셋 79초) → 대시보드 "반영 완료" → 시민 화면 세 결과(프리셋 1곳: 평소 15분 / 파업·셔틀 없음 39분 / 파업+셔틀 18분, 셔틀 r05). 기록 `guro_exp_work/e2e_1127/integrate/captures/precheck/`.
- 남은 빈칸: 지하철 포함 A팀 통합 GTFS가 이 PC에 없어 버스 전용 GTFS(11/02 하루)로만 확인했다.
