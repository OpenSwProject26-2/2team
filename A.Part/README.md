# A 담당: 공유 정리본 수요 분석·후보 조사·개발용 추출

Issue #10의 코드·문서·합성 테스트를 이 폴더에서 관리한다. 기존 분석·추출의 집계·분류 규칙은 유지한다. 공통 규약은 [AGENTS.md](../AGENTS.md), 데이터 의미는 [M00](../docs/contracts/M00_DATA_CONTRACT.md)를 참고한다. 추가 교통망 정제는 정류장·방문·연속 구간의 개발용 초안까지만 지원하며 엔진 입력 변환·여정 연결은 포함하지 않는다.

- `data/`: 분석·후보 조사·추출 Python 패키지
- `data/docs/`: [수요 분석](data/docs/seoul_demand_analysis.md), [후보 조사](data/docs/candidate_survey.md), [구로 추출](data/docs/guro_input_extraction.md)
- [교통망 정제](data/docs/network_cleaning.md): `python -B A.Part/run.py clean_network --config <설정 경로>`로 실행한다. 대상 노선·원천 그룹·경로·단위 근거는 설정으로 받는다.
- [수요 연결·검증](data/docs/demand_linking.md): `python -B A.Part/run.py link_demand --draft-dir <기존 추출> --network-dir <정제 망> --output-dir <새 출력>`으로 원천 방문·연속 구간에 연결하고 이전 추출 보고서·출처·전체/노선별 규모를 대조한다. 실제 실행 범위는 **구로 5개 노선 추출본의 오전 OD 전체**이며 서울 전체 분석과 구분한다.
- `tests/demand/`: 실제 이용 기록을 사용하지 않는 합성 회귀 테스트
- `run.py`: 저장소 루트에서 도구를 실행하는 진입점. 작업 디렉터리를 바꾸지 않는다.

## 저장소 루트에서 실행

현재 디렉터리가 `C:\2team`일 때:

```powershell
python -B A.Part/run.py demand_analysis --help
python -B A.Part/run.py candidate_survey --help
python -B A.Part/run.py extract_guro_draft --help

python -B A.Part/run.py demand_analysis --input-dir raw/from_teammate --output-dir data/processed/a_run_demand
python -B A.Part/run.py candidate_survey --input-dir raw/from_teammate --analysis-dir data/processed/a_run_demand --output-dir data/processed/a_run_candidates
python -B A.Part/run.py extract_guro_draft --input-dir raw/from_teammate --survey-dir data/processed/a_run_candidates --output-dir data/processed/a_run_guro
```

입력은 기존 루트 `raw/`에 있고 실제 출력은 루트 `data/processed/`에 남는다. 코드 이동 때문에 자료를 옮기지 않는다. 출력 디렉터리는 새 경로여야 하며 위 경로가 이미 있으면 다른 이름을 사용한다.

## A.Part에서 실행

현재 디렉터리가 `C:\2team\A.Part`일 때 같은 자료를 가리키는 명령:

```powershell
python -B -m data.demand_analysis --input-dir ../raw/from_teammate --output-dir ../data/processed/a_run_demand
python -B -m data.candidate_survey --input-dir ../raw/from_teammate --analysis-dir ../data/processed/a_run_demand --output-dir ../data/processed/a_run_candidates
python -B -m data.extract_guro_draft --input-dir ../raw/from_teammate --survey-dir ../data/processed/a_run_candidates --output-dir ../data/processed/a_run_guro
```

두 명령 묶음은 같은 출력 경로를 사용하므로 연속으로 중복 실행하지 않는다. `A.Part`라는 디렉터리 이름을 `python -m A.Part...`처럼 패키지 이름으로 사용하지 않는다. 테스트·외부 Python에서 `data`를 import하려면 `A.Part`가 import 탐색 경로에 있어야 한다. 루트 진입점과 아래 A 테스트 명령이 이를 제공한다.

## 상대 경로와 기본값

입력·출력·임시 경로는 **실행 시 현재 작업 디렉터리 기준**이다. 코드 파일 위치 기준으로 재해석하거나 자동 이동하지 않는다. 기본 CLI 인자도 기존 그대로다.

| 상대 인자 | 저장소 루트에서 | A.Part에서 |
| --- | --- | --- |
| `raw/from_teammate` | 루트의 실제 공유 자료 | `A.Part/raw/from_teammate` |
| `data/processed/...` | 루트의 실제 로컬 출력 | `A.Part/data/processed/...` |
| `../raw/from_teammate` | 저장소 바깥의 raw 경로 | 루트의 실제 공유 자료 |
| `../data/processed/...` | 저장소 바깥의 data 경로 | 루트의 실제 로컬 출력 |

따라서 **A.Part에서는 위 예시처럼 `../raw/`와 `../data/processed/`를 명시한다.** `.gitignore`는 루트 `data/processed/`와 `A.Part/raw/`, `A.Part/data/processed/`도 제외하지만 실제 입력·출력은 기존 루트 자료 경로를 사용한다. 절대 경로도 지정할 수 있다. 정제 설정 내부 경로도 현재 작업 디렉터리 기준이며, 두 위치에서 같은 설정을 쓰려면 입력·출력을 절대 경로로 지정한다.

## 테스트

저장소 루트에서 A 테스트와 기존 루트 테스트를 각각 실행한다.

```powershell
python -B -m unittest discover -s A.Part/tests -t A.Part -v
python -B -m unittest discover -s tests -t . -v
```

A.Part 안에서는 A 테스트를 다음과 같이 실행한다.

```powershell
python -B -m unittest discover -s tests -t . -v
```

루트 `tests/`와 A.Part `tests/`는 별도 탐색 대상이다. 두 테스트 명령은 별도 프로세스로 실행한다. 루트의 기존 테스트 명령만으로 A 테스트까지 실행됐다고 보고하지 않는다. 실제 자료 분석은 테스트와 별개이며 경로 정리 확인을 위해 재실행할 필요가 없다.
