# 구로 5개 노선 추출본의 오전 OD 전체: B 시간대 입력 초안

첫 단계의 [OD–방문열 연결·검증](demand_linking.md) 결과를 B 형태의 bin CSV로 변환하는 두 번째 단계다. 서울 전체 분석 재실행·전체 여정 연결·B 실행 입력 완성 작업이 아니다. 현재 실제 실행 범위는 **구로 5개 노선 추출본의 오전 OD 전체**이며 일반 코드에는 실제 ID·인원을 고정하지 않는다.

## 실행

저장소 루트에서 첫 단계가 성공한 뒤 새 경로에 실행한다. B나 외부 패키지는 필요 없다.

```powershell
git check-ignore -v -- data/processed/guro_demand_linking_verified_run/bins/report.json
python -B A.Part/run.py demand_bins --links-dir data/processed/guro_demand_linking_verified_run/links --draft-dir data/processed/guro_real_input_draft --network-dir data/processed/guro_network_cleaning_run/network --output-dir data/processed/guro_demand_linking_verified_run/bins
python -B -m unittest discover -s A.Part/tests -t A.Part -p test_demand_bins.py -v
```

A.Part 기준에서는 `python -B -m data.demand_bins`로 실행하며 상대 경로에 `../data/processed/...`를 쓴다. 인자는 현재 작업 디렉터리 기준이며 input manifest는 절대 경로로 기록한다. 기존 결과와 입력 내부 경로는 덮어쓰지 않는다.

## 변환과 출처

연결 결과의 성공 표시만 신뢰하지 않는다. 원천 입력 체크섬·정제 출처·추출 보고서 전체/노선별 대사·저장 링크의 모든 행을 다시 검사하고, 실패한 연결 결과는 변환하지 않는다. 연결 출력 `source-order-demand-linking-v2`만 받는다. 이전 결합형 v1 출력은 보존하며 새 첫 단계를 실행해야 한다.

| 파일 | 내용 |
| --- | --- |
| `demand_bins_all.csv` | 전체 원천 대응용 bin. 동일 출도착도 보존하므로 B에 그대로 전달할 수 없음 |
| `demand_bins_included.csv` | 원천 순서상 유일 연결·서로 다른 출도착의 bin. 실제 방향·유효 버전·운행 가능성은 미확인 |
| `report.json` | 연결 결과·원천 입력 체크섬, 추출 보고서 대사와 저장 CSV 검증, 발생/엔진 미실행 표시 |
| `README.md` | 실제 로컬 출력의 범위·의미·제한 |

CSV에는 `aggregate_id`, `origin`, `destination`, `bin_start_s`, `bin_width_s`, `passenger_count`의 여섯 열만 있다. 처음 세 값은 문자열, 나머지는 정수다. 07시는 시작 25200초, 08시는 28800초이며 폭은 모두 3600초다. `passenger_count`는 PSNG_NUM의 정수 합산 인원 의미를 유지하며 원천 행을 추가로 합치거나 후보 수만큼 복제하지 않는다.

`aggregate_id`는 연결 단계의 값을 유지한다. 같은 ID로 연결 입력 `od_links.jsonl`에서 원래 노선·시간대·출처 행·모든 방문/구간 후보를 찾는다. 방문의 회사·원천 노선명·순번·반복 방문·원천 행 대응은 정제 JSON과 그 체크섬으로 연결된다. 이 매핑을 버리거나 임의 원래 노선을 선택하지 않는다.

시간은 Asia/Seoul 서비스일 자정 기준 초 표현이지만 근거는 **승차 태그 시간대**다. 실제 정류장 도착/발생 시각이 아니다. `arrival_s`·개별 승객·난수 seed·realization·미확인 운행 조건을 생성하지 않는다. 관측 기준일과 다음 날짜 기록의 서비스일 귀속도 구분하며 후자는 미확인이다.

저장 후 두 CSV를 다시 읽어 링크에서 변환한 모든 필드·출현 횟수·전체/포함 인원을 비교한다. 누락·중복·잘못된 포함은 인원 합계만 같아도 실패한다. B/C 실제 수송·독립 검증 완료를 뜻하지 않는다. 실제 새 결과는 이전 연결 내용과 같은지 비교했고 두 bin CSV도 기존 파일과 바이트 단위로 일치했다. 결과는 로컬 `previous_result_comparison.json`에 기록했다.

## 확인한 B·C 형식과 미실행 범위

2026-11-10 읽기 전용 대조 기준은 미병합 [B PR #6](https://github.com/OpenSwProject26-2/2team/pull/6)의 `d18ee1ee77016b4cc1b5a91daf9cad6f7b954784`, main C의 `d6d69fe19f4f7ff9b3b6137115fa8f03bb3d5db1`이다. 해당 코드는 저장소 밖 임시 복사본에서 읽었으며 현재 브랜치에 합치지 않았다. B의 `sim/arrivals.py`, `run_inputs.py`, `models.py`, `adapters/opt_adapter.py`와 C의 모델·후보 검사·README·ADR을 확인했다.

- B `sim.arrivals.BinRow`가 위 여섯 필드를 받으며 `check_rows`는 중복 ID·같은 출도착·비정수·잘못된 범위를 거절한다. **이번 새 포함 CSV를 고정 B 코드의 check_rows로 별도 형식 검사**했고 전체 CSV는 동일 출도착 때문에 거절됨을 확인했다. API·SHA·인원·bin checksum은 Git 제외 경로의 `b_bin_check.json`에 남겼다. A 변환 도구 자체는 B를 import하거나 이 API를 호출하지 않는다.
- **이번 실제 구로 실행에서는 B 시뮬레이션과 발생 시각 생성을 하지 않았다.** 과거 공통 합성 fixture의 B 실행 결과와도 별개다. 일반 A·루트 테스트는 B 설치 없이 실행된다.
- B `generate`의 bin 내 균등 SHA-256 발생 정책은 구현돼 있지만, 실제 태그 수요에 적용할 발생 분포·seed·고정 realization·서비스일 해석은 합의가 필요하다. 정책 구현이 태그 시각을 실제 도착으로 바꾸는 근거는 아니다.
- B `c_demands`는 realization을 정확 시각 C Demand로 바꾸며 `route_of_row` 매핑을 요구한다. 이후 연결 JSONL에서 원래 노선을 명시적으로 연결해야 한다. 이번에는 변환하지 않는다.
- B `Passenger.time_s`는 정확 발생 시각이고 `RunInputs.travel_times`는 양수 시간이다. 운행편·차량 정원·도착/출발·복귀 시각과 명시적 대기/여정/drain 조건이 필요하다. 현재 정책은 FIFO·고정 구간시간만 지원한다.
- C는 정확 `arrival_s`, `single_bus_leg`, 고정 후보, 양수 주행시간·음수 아닌 거리·허용 전이·회차·공차 복귀·운영 조건을 요구한다. 수송 구간 내 재방문, 이질 차량, 서로 다른 출발/복귀 거점은 미지원이다. 이를 피하려고 실제 방문이나 수요를 바꾸지 않는다.
- 검증된 방향/버전·주행시간·허용 회전/회차/복귀·정차·차량 수/정원/차고지/가용시간·운영창/운행편은 부족하다. 미확인을 0초나 추정값으로 채우지 않는다. 정상 교통 수요 제외는 D 조회와 연동할 후속 작업이다. 정상 GTFS·여정/환승 연결·최종 목적지·고유 여정 인원·충족률·독립 검증도 수행하지 않았다.
