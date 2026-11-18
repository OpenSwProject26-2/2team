# 그림과 점수표에 사용한 자료

## 운행안을 설계할 때 제공한 입력

- [stops.csv](inputs/stops.csv), [stations.csv](inputs/stations.csv): 정류장·철도역 ID, 이름, 좌표. ID는 문자열로 읽는다.
- [demand.csv](inputs/demand.csv): 출발 정류장·목적지 역·출발 시간대별 집계 수요. 행 수가 아닌 `people` 합계가 평가 분모인 16,230명이다.
- [access.csv](inputs/access.csv): 정류장과 철도역 사이의 접근 거리(m)와 보행시간(초).

## 어떤 운행안을 비교했나?

| 점수표의 ID | 노선 원본 | 비고 |
|---|---|---|
| plan-01 | [지자체 공표안의 시뮬레이션 입력](reference-plans/plan-01.json) | 공표 노선의 정류장 방문 순서를 전사한 안 |
| plan-e3 | [우리 알고리즘의 시뮬레이션 입력](reference-plans/plan-e3.json) | 도입 차량 11대를 입력한 결과 |
| sol-run-01~10 | [Sol 제출물](../../sol_runs_0104_new/) | 각 회차의 JSON이 노선안, Markdown이 설계 메모 |
| sonnet-run-01~10 | [Sonnet 제출물](../../sonnet_runs_0110_new/) | 각 회차를 별도 운행안으로 평가 |

## 발표 숫자 확인하기

[scores.csv](../tables/scores.csv)에서 비교할 지표의 열을 선택한다. 지자체와 E3는 해당 행의 값이고, 범용 AI는 Sol 10개 행의 평균과 Sonnet 10개 행의 평균을 다시 평균한 값이다. 표시할 때만 소수점 둘째 자리로 반올림한다.

입력 CSV는 노선 설계용이다. 승객별 대기·도착 시각이나 공식 점수를 이 네 파일만으로 재현할 수는 없다. 점수는 M07의 고정 승객, 운행시간, 철도 시각표 및 시뮬레이터 산출물을 이용해 계산했다. 원시 교통카드 기록은 포함하지 않는다.
