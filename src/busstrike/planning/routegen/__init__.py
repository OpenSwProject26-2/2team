"""후보 노선 생성 + 구 단독(per_gu) 차량 배분.

설계 v1 「후보 노선 생성」(4절)·「구별 예산 제약 동시 배분」(5절) 중 구 하나, per_gu 방식만 구현한다.

- `data`: 구 실험 묶음(network/, demand/, bundles/) 읽기, 변형별 잔여 수요 r(s→k, t), 묶음 대조
- `graph`: 망(travel_times) 최단경로와 노선 지표(편도·우회율·정차 수)
- `generate`: 허브 → 권역 → 겹침 병합 → 섹터 분할·최선 삽입·2-opt → 최단경로 피더 → 덮개·주기·중복 제거
- `allocate`: 충족 근사 v2, CELF 탐욕 개설, 국소 탐색, 차량 합 = 가용 대수

주기와 공차 복귀는 시뮬레이터(`busstrike.sim.timetable.route_period`)와 비교 입력 변환기
(`busstrike.sim.adapters.comparison_plan.to_engine_plan`)를 불러서 구한다. 같은 식을 여기서 다시 짜지 않는다.
외부 의존성은 없다(표준 라이브러리만).
"""
