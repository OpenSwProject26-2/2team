# 검증된 포함 bin → 고정 B 생성기: 개발용 발생 입력

PR #13의 `fdc42bf558bcc2216f1663548ca6ab08956c8390` 기반 후속 A 도구다. B 생성 알고리즘은 구현·복사하지 않고 저장소 밖의 고정 Git 체크아웃을 직접 import한다. 공용 계약·B/C 코드를 수정하거나 B PR을 A 브랜치에 병합하지 않는다.

**이번 검증에는 합성 자료만 사용했다. 실제 구로 자료의 승객 발생 입력은 생성하지 않았다.** 현재 B의 균등 발생은 개발용 실험 가정이며 팀의 확정 정책·실제 승객 도착 관측이 아니다. 차량 운행·C 계획 생성·정상 GTFS·환승·시나리오/harness 확장은 포함하지 않는다.

## 입력과 사전 검사

입력은 기존 [연결·검증](demand_linking.md)과 [시간 bin 변환](demand_bins.md)의 출력이다.

- `--bins-dir`: `demand_bins_included.csv`, `demand_bins_all.csv`, `report.json`이 있는 디렉터리.
- `--links-dir`, `--draft-dir`, `--network-dir`: bin의 원천 연결 결과·추출본·정제 망. 원래 연결/변환 검증 함수를 재사용해 정제 출처 SHA·추출 보고서 전체/노선별 인원·모든 저장 링크·bin의 포함 범위를 다시 확인한다. 성공 표시만 신뢰하지 않는다.
- 포함 CSV는 `aggregate_id`, `origin`, `destination`, `bin_start_s`, `bin_width_s`, `passenger_count` 여섯 열이다. 세 정수 필드는 ASCII 정수 문자열을 명시적으로 int로 변환한다. ID 문자열은 자르거나 선행 0을 제거하지 않는다. 잘못된 정수·추가/누락 열·원천 대응 불일치는 중단한다.
- 이후 실제 B `BinRow`를 만들고 `check_rows`로 검사한다. 중복 ID·동일 출도착·음수/잘못된 정수는 조용히 보정하지 않는다. 포함 밖의 수요를 새로 선택하거나 같은 인원을 후보 수만큼 복제하지 않는다.

## B 고정 코드와 필수 실험 인자

`--b-root`는 A 작업 트리 밖의 **실제 Git 체크아웃 루트**, `--b-commit`은 전체 40자리 SHA다. HEAD 일치와 clean 상태를 실행 전후 확인한다. 모듈이 지정한 체크아웃에서 로드됐는지, Git 추적 파일인지 확인하고 API 파일 SHA-256도 기록한다. 다른 위치의 `sim` 패키지가 이미 import돼 있으면 거절한다. B 소스를 복사·수정·설치하지 않으며, 함수 호출 후 import 경로를 복구한다.

정상 로드한 `sim` 모듈과 DTO 클래스는 프로세스가 끝날 때까지 유지한다. 따라서 바깥 `pinned_b()` 없이 `read_prepared()`가 반환한 객체도 같은 B를 다시 열어 검증할 수 있다. 재진입할 때도 SHA·clean 상태·API 체크섬을 검사하고 모듈 제거/교체·클래스 재로딩을 거절한다. 한 프로세스에서 다른 체크아웃/버전으로 바꾸려면 새 프로세스를 사용한다. 실패한 최초 import만 정리하며, 정상 로드한 클래스를 지워 재사용 객체의 타입을 깨뜨리지 않는다. B의 타입 검사를 우회하거나 DTO를 임의 재구성하지 않는다.

2026-11-10 확인한 [B PR #6](https://github.com/OpenSwProject26-2/2team/pull/6)은 미병합이고 최신 SHA는 `d18ee1ee77016b4cc1b5a91daf9cad6f7b954784`다. 이전에 확인한 `d18ee1e`와 같았다. 사용한 API는 `sim/arrivals.py`의 `check_rows`, `generate`, `write_realization`, `read_realization`, `validate_realization`이다. 이 경로는 표준 라이브러리만으로 import할 수 있어 이번 검증에 새 패키지를 설치하지 않았다.

2026-11-11 재사용 회귀의 `c_demands` 합성 호환성 검사는 어댑터 import 의존성 때문에 외부 임시 가상환경에만 B requirements의 `simpy==4.1.2`를 설치해 수행한다. 일반 Python과 B/C 파일은 변경하지 않는다. 어댑터를 import하고 수요를 변환할 뿐 `simulate()`나 C 계획 생성은 호출하지 않는다.

| 필수 인자 | 의미 |
| --- | --- |
| `--policy` | 현재 지원하는 정확한 이름 `uniform_bin_sha256_v1`. 다른 값은 거절하며 자동 대체하지 않음 |
| `--seed` | 비어 있지 않은 문자열. 기본값 없음 |
| `--service-date` | 명시적인 ISO 날짜 `YYYY-MM-DD`. 기본값·관측일 자동 복사 없음 |
| `--output-dir` | 기존에 없는 새 디렉터리. 입력/B 체크아웃 내부 출력도 거절 |

서비스일은 Asia/Seoul 자정 기준 시간의 기준일이다. 인자를 받았다는 것은 실제 카드 기록의 날짜 귀속을 검증했다는 뜻이 아니다. bin 시작을 도착 시각으로 사용하지 않으며, B가 선택한 정수 초는 실험 가정이다. `generate`가 만드는 전체 승객 tuple은 메모리를 사용한다. 큰 실제 입력에 자동 실행하지 않는다.

## 합성 연동 검사와 실제 실행 명령

저장소 루트에서 실행한다. 아래 `$arrivalBRoot`는 사용자가 준비한 외부 clean 체크아웃이며 A 저장소에 B를 넣지 않는다. 출력 경로가 이미 있으면 새로운 이름을 사용한다.

```powershell
$arrivalBRoot = '<외부 B Git 체크아웃의 절대 경로>'
$arrivalBSha = 'd18ee1ee77016b4cc1b5a91daf9cad6f7b954784'

# 일반 검사: B 없이 실행하며 실제 B 연동 검사를 포함했다고 주장하지 않음
python -B -m unittest discover -s A.Part/tests -t A.Part -v
python -B -m unittest discover -s tests -t . -v

# 실제 B 필요. c_demands 회귀까지 실행하려면 B requirements를 설치한 외부 임시 Python 사용
$arrivalIntegrationPython = '<B requirements가 설치된 외부 임시 가상환경의 Python>'
git check-ignore -v -- data/processed/prepare_arrivals_synthetic_check/arrivals/realization.jsonl
& $arrivalIntegrationPython -B A.Part/integration_tests/prepare_arrivals_check.py --b-root $arrivalBRoot --b-commit $arrivalBSha --output-dir data/processed/prepare_arrivals_synthetic_check

# 위 합성 입력으로 실행 도구 자체를 호출. 실제 구로 CSV 경로로 바꾸지 않음
python -B A.Part/run.py prepare_arrivals --bins-dir data/processed/prepare_arrivals_synthetic_check/bins --links-dir data/processed/prepare_arrivals_synthetic_check/links --draft-dir data/processed/prepare_arrivals_synthetic_check/synthetic_inputs/draft --network-dir data/processed/prepare_arrivals_synthetic_check/synthetic_inputs/network --output-dir data/processed/prepare_arrivals_synthetic_check/arrivals_cli --b-root $arrivalBRoot --b-commit $arrivalBSha --policy uniform_bin_sha256_v1 --seed synthetic-arrivals-v1 --service-date 2026-01-01
```

A.Part 기준에서는 `python -B -m data.prepare_arrivals`와 `../data/processed/...` 상대 경로를 사용한다. 모든 인자는 현재 작업 디렉터리 기준이다. 실제 결과도 반드시 Git 제외 경로에 보관한다.

일반 A 테스트에는 B 생성기의 대체 구현이나 성공을 흉내 내는 mock이 없다. 단위 테스트는 CSV·인자·출처·버전 검사와 실패 처리를 확인한다. 별도 연동 스크립트는 B가 없으면 실패하며 skip을 통과로 계산하지 않는다. 실제 B로 인원·집계별 OD·반열린 bin 범위·저장/재독해·동일 seed 재현성·입력 불변·덮어쓰기 방지·잘못된 ID/정수/출도착·출처 불일치·SHA/정책/서비스일 오류·저장 파일 변조를 검사한다. read 재사용 검사만 B `generate`를 실패하도록 일시 감시해 호출이 없음을 확인하며 B 파일은 수정하지 않는다.

새 프로세스 회귀는 `arrival_reuse_check.py`가 수행한다. `sim`이 import되지 않은 상태에서 저장 입력을 먼저 읽고, 컨텍스트를 종료한 뒤 같은 B를 열어 실제 `validate_realization` 및 합성 `c_demands` 변환을 검사한다. 함수 호출 감시로 `generate()`·`simulate()`가 호출되면 실패한다. 이 스크립트를 별도로 실행했다면 같은 `c_demands` 검사를 중복 실행할 필요가 없다.

```powershell
& $arrivalIntegrationPython -B A.Part/integration_tests/arrival_reuse_check.py --prepared-dir data/processed/prepare_arrivals_synthetic_check/arrivals --b-root $arrivalBRoot --b-commit $arrivalBSha
```

## 출력과 재사용

| 파일 | 내용 |
| --- | --- |
| `realization.jsonl` | B `write_realization` 원래 형식. 첫 줄 메타데이터·집계행, 이후 생성 승객 |
| `aggregate_routes.jsonl` | aggregate_id → 원래 ROUTE_ID, 원천 OD 열·추출 행 번호·방문/구간/원천 행 후보 |
| `report.json` | 모든 A 원천 파일 SHA, B 위치·HEAD·clean·API 파일 hash 전후, 생성기·정책·seed·서비스일·인원·bin 및 realization checksum, 저장 파일 hash·검사 결과 |
| `README.md` | 해당 결과의 의미·한계·재사용 안내 |

저장 후 B `read_realization`과 `validate_realization(regenerate=False)`로 다시 읽고 체크섬·집계별 OD/인원·bin 범위를 확인한다. 원천 대응표도 저장 파일에서 재검증한다. 기존 경로에는 재실행하지 않는다. 오류는 값·원본 행 대신 파일/문제 필드 또는 오류 종류만 표시한다. 실패 시 열린 스트림은 닫으며 성공 보고서 없는 부분 출력은 완성 입력으로 사용하지 않는다.

Python에서 재생성 없이 재사용할 수 있다. `A.Part`가 import 경로에 있는 환경에서:

```python
from data.prepare_arrivals import read_prepared

realization, report = read_prepared(
    '<저장된 준비 결과 디렉터리>',
    b_root='<동일 SHA의 외부 clean B 체크아웃>',
    b_commit='d18ee1ee77016b4cc1b5a91daf9cad6f7b954784',
    expected_report_sha256='<미리 보관한 report.json SHA-256>',
)
```

`expected_report_sha256`는 외부에 고정한 보고서와도 비교하려는 경우에 사용한다. B realization checksum 자체는 서비스일을 포함하지 않으므로 **서비스일·정책을 담은 report.json과 함께** 고정·전달해야 한다. 재사용 함수는 저장 산출물과 고정 B 코드를 확인하며 과거 원천 파일을 다시 생성하지 않는다. 원천 파일의 현재 상태를 재검증하는 동작과는 구분한다.

## 실제 구로 적용 전 확인

아직 실제 구로 발생 입력을 생성하지 않았다. B와 실제 승차 태그 bin에 적용할 발생 가정(균등 채택 여부 포함), seed·불변 realization, 관측일/다음 날짜의 서비스일 귀속, 포함 수요/동일 출도착/보류 처리와 원래 노선 매핑 의미를 확인해야 한다. 기존의 방향·유효 버전 미확인 상태도 유지된다. 이 작업은 주행시간·정차·회차·복귀·차량 조건을 만들거나 C Demand로 변환하지 않으며, 실제 운행·수송 효과·독립 검증·전체 팀 통합 완료를 주장하지 않는다.
