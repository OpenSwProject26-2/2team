# 공통 합성 교통망·수요 입력 초안

B·C·D가 검토하고 재사용할 작은 A 입력이다. 공용 DTO나 팀 정책의 승인본이 아니며, 모든 공통 사례의 수송 동작을 검증했다는 뜻도 아니다. 실제 원시 자료는 사용하지 않았다.

## 출처와 변경 범위

계산값은 기존 C 담당 기여인 [`C.Part/opt/examples/synthetic.json`](../../C.Part/opt/examples/synthetic.json)에서 가져왔다. 관련 Issue #3, [`C.Part/opt/README.md`](../../C.Part/opt/README.md), [`C.Part/opt/ADR-001.md`](../../C.Part/opt/ADR-001.md)를 함께 참고한다. 기존 정류장·구간·시간·거리·허용 전이·회차 지점·수요를 그대로 보존했으며 새 수요 사례를 추가하지 않았다. 이번 A 작업은 두 JSON 분리, 가상 좌표·메타데이터 추가, 필드 설명, 재구성 예제와 입력 검증이다. 후보 노선·차량·정책은 기존 담당자 예제를 참조하며 수정하지 않았다.

## 파일 구조와 필드

두 JSON의 `metadata`는 설명·버전이며 `data`는 계산 자료다. `network.json`의 `coordinates`는 별도 좌표 자료다. `compose.py`는 계산 필드를 명시적으로 선택하므로 메타데이터·좌표·추가 설명 열을 opt에 전달하지 않는다.

| 메타데이터 필드 | 자료형·의미 |
| --- | --- |
| `schema_version` | string, 이 로컬 초안의 구조 버전 `a-common-synthetic-v1` |
| `dataset_version` / `data_version` | string, 원본 계산 데이터셋 / 망 또는 수요 버전. 현재 `synthetic-v1` |
| `synthetic` / `status` | boolean `true` / string `draft_for_team_review` |
| `source.path`, `related_issue`, `credit`, `changes` | string, 출처 파일·연결 Issue·기존 기여·이번 변경 |
| `time_reference.service_date` | ISO 날짜 string, `2026-11-07` 유지 |
| `time_reference.timezone`, `unit`, `origin` | string, `Asia/Seoul`, `s`, `service_date_midnight` |
| `assumptions` / `network_assumptions` | string 배열, 공통 / 교통망의 합성 가정 |
| `coordinate_reference` | object, CRS `EPSG:4326`, 축 순서 `[lat, lon]`, 단위 `degree`, 합성 표시 |
| 수요의 `scope_mode` / `network_version` | string, 기존 사례 `single_bus_leg` / 참조 망 버전 |
| 수요의 `arrival_semantics` | string, 정류장 도착 시각의 합성 의미 |

### network.json

| 계산 필드 | 자료형·단위·참조 |
| --- | --- |
| `data.stops` | 고유한 비어 있지 않은 string ID 배열: A/B/C/D. 계산용 문자열 목록 유지 |
| `data.segments` | 방향별 구간 object 배열 |
| `segment_id` | 고유 string ID: ab/bc/bd/ba/cb/db |
| `origin`, `destination` | string, `stops` 참조. 반대 방향은 별도 구간 |
| `travel_s` | 양수 int, 초. 모든 구간 60초 |
| `distance_m` | 음수가 아닌 int, m. 모든 구간 300m |
| `data.allowed_turns` | 두 구간 ID의 배열을 담은 배열. 앞 구간 도착과 뒤 구간 출발이 연결됨. 총 8쌍 |
| `data.turnaround_stops` | string ID 배열, `stops` 참조. A/B/C/D의 가상 회차 허용 |
| `coordinates` | 정류장 ID를 키로 하는 object. 모든 정류장에 정확히 한 좌표 |
| `coordinates.<stop_id>.lat`, `.lon` | 유한 number, degree. 위도 [-90, 90], 경도 [-180, 180] |

좌표는 배치 확인용 가상 값이다. 구간 거리·주행시간은 좌표에서 계산하지 않는다. 실제 도로·허용 회전·회차·운행 가능성의 증거가 아니다.

### demand.json

`data.demands`는 다음 필드를 가진 object 배열이다.

| 필드 | 자료형·단위·참조 |
| --- | --- |
| `demand_id` | 고유한 비어 있지 않은 string ID: d1~d5 |
| `origin`, `destination` | string, network의 `stops` 참조 |
| `arrival_s` | 음수가 아닌 int, 서비스 날짜 자정부터의 초. 합성 승객의 출발 정류장 도착 시각이며 승차 태그 시각이 아님 |
| `passenger_count` | 음수가 아닌 int, 명. 행 수로 대체하지 않음 |
| `original_route_id` | string, 기존 예제의 `original=true` 후보 노선 참조. 현재 r1/r2 |

`arrival_s`는 이미 생성된 값이며 실행 중 난수로 다시 만들지 않는다. 현재 도착은 0~120초, 총 5묶음·9명이다. 이 파일은 시간대별 관측 OD인 M00 `DemandRow` 자체가 아니라, 기존 개발 사례의 합성 발생 입력이다.

`arrival_s`는 출발 정류장에서 가정한 승객 발생 시각이며, M00 `time_bin_s`의 집계 시간대와 다르다. 실제 시간대별 수요를 승객 발생 시각으로 변환하려면 별도의 후속 규칙과 담당자 합의가 필요하다.

## 시간·운영 가정과 재구성

기본 사례는 **2026-11-07, Asia/Seoul 자정 기준 정수 초, 운영 0~480초**이며 07~09시 사례가 아니다. 미래 사례에서 86,400초를 넘는 서비스일 시각을 상한으로 잘라서는 안 된다. 07~09시 확장은 수요만 옮기지 말고 운영 창·차량 가용시간·시간표와 함께 담당자 합의 후 맞춘다.

기존 예제의 차량 2대는 각각 정원 2명, A에서 출발·복귀, 가용시간 0~480초다. 정차·종점 회차 0초는 손계산용 기존 가정이며 실제 기본값이 아니다. C의 `outbound`는 수송, `return_segments`는 공차 복귀다. 복귀 중 반대 방향 수송을 가정하지 않는다. 고정 이동시간·거리와 허용 전이는 이 작은 합성 사례에만 적용한다.

저장소 루트에서 Python 3.11 이상, 표준 라이브러리와 기존 C의 opt만으로 실행한다. 공통 파일의 기본 경로와 C 원본 경로는 `compose.py` 위치에서 계산한다. 명시적으로 전달한 `--network`, `--demand`, `--example`, `--output` 상대 경로는 실행 중인 작업 디렉터리 기준이다.

```powershell
# 계산 입력을 stdout으로 확인
python -B -m tests.fixtures.compose

# 새 파일로 저장할 때만 사용: 기존 파일은 덮어쓰지 않음
python -B -m tests.fixtures.compose --output common_synthetic_inputs.json

# C의 opt import가 필요한 명령만 현재 프로세스의 PYTHONPATH 설정
$fixturePreviousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = (Resolve-Path ./C.Part).Path
    python -B -m opt --input common_synthetic_inputs.json
    # 공통 입력 테스트: 루트 tests 패키지 사용
    python -B -m unittest discover -s tests -t . -v
} finally {
    $env:PYTHONPATH = $fixturePreviousPythonPath
}

# 별도 Python 프로세스: C.Part/tests 패키지와 C.Part/opt 사용
python -B -m unittest discover -s C.Part/tests -t C.Part -v
```

`compose` 자체는 opt를 import하지 않으므로 PYTHONPATH 설정 없이 실행된다. 두 테스트는 서로 다른 `tests` 패키지를 사용하므로 한 Python 프로세스에서 묶어 실행하지 않는다. 위 설정은 현재 PowerShell 프로세스에만 적용하고 이전 값으로 복원한다. Bash에서는 공통 테스트를 `PYTHONPATH=C.Part python -B -m unittest discover -s tests -t . -v`로 실행할 수 있다.

다음 Python 사용 예제는 저장소 루트에서 C.Part가 PYTHONPATH에 설정된 환경(위 `try` 블록 안 등)으로 실행한다. 임시 계산 파일은 종료 시 제거한다.

```python
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from opt.models import load_inputs
from tests.fixtures.compose import compose_inputs

with TemporaryDirectory() as directory:
    path = Path(directory) / "inputs.json"
    path.write_text(json.dumps(compose_inputs()), encoding="utf-8")
    inputs = load_inputs(path)
    assert inputs.total_demand == 9
```

망 필드·수요는 새 JSON에서, 후보 노선·fleet·policy와 시나리오 식별자는 기존 예제에서 가져온다. 버전·서비스일·scope는 명시적으로 대응한다. 기본 사례에서 기존 입력과 같은 checksum을 갖도록 계산값을 보존한 것이며, 수정된 사례의 불변 입력 ID 발급 정책을 새로 정한 것은 아니다. 변경 사례를 공유할 때에는 별도 버전·입력 묶음 ID와 checksum을 고정할 책임이 남는다.

## 책임 경계와 사례의 한계

| 담당 | 책임과 이번 상태 |
| --- | --- |
| A | 합성 망·수요·좌표, 입력 형식·참조·재구성 검증 제공 |
| B | 운영 조건·차량 일정·용량/대기열 동작·독립 검증·손계산 정답. 기존 예제의 조건을 빌렸지만 새 일정·정답은 만들지 않음 |
| C | 후보·운행안·배분과 근사 평가. 기존 예제와 planning 호환성만 확인 |
| D | 경로·API·정상 수단/환승 처리. 실제 엔진 연결은 미실행 |

공통 테스트 계획의 사례별 범위는 다음과 같다. 별도 계획서 원문은 저장소에서 확인하지 못했으므로 아래 표가 모든 계획 사례를 충족한다고 주장하지 않는다.

| 사례 | 현재 입력에서 표현 | 실제 검증과 남은 항목 |
| --- | --- | --- |
| 정원 초과 | d1 A→C 3명, 차량 1대 정원 2명 | 원본 계산값·참조 보존 및 C 전체 근사 결과 확인. B 대기·분할 수송과 손계산 정답 미확인 |
| 반대 방향 | d4 C→A 1명, cb/ba 구간 | 입력 보존 확인. 현재 후보는 해당 구간을 공차 복귀로 사용. 반대 방향 outbound와 B 일정 미제공·수송 미검증 |
| 공유 OD | d5 A→B 1명, r1/r2/r3가 경로 공유 | 입력 보존·C 전체 근사 결과 확인. B/D 실제 선택·대기열 동작 미검증 |
| 중간 정류장·공유 구간 | d2 B→C 2명, d1과 bc 공유 | 입력 보존 확인. B 실제 구간 정원·승하차 사건 처리 미검증 |
| 대기 경계 | 직전/동일/직후 경계 사례 미제공 | B 운행편·사건 순서·손계산 정답 필요, 미검증 |
| 차량 일정 중복 | 충돌 일정 미제공 | B 차량 임무·복귀 시간·위반 기대값 필요, 미검증 |
| 정상 교통 환승 | 정상 운행편·환승 입력 미제공 | A/D 정상 GTFS·보행·최소 환승시간 연결 후 검증 |
| 순환·반복 방문 | 수송 구간 내 반복 방문 사례 미제공 | A/C/D occurrence와 미지원 처리 합의 필요, 미검증 |

균등 **7/9**, 수요 비례 **6/9**는 기존 C 근사 평가와의 호환성 검사다. B 시뮬레이션 정답이나 실제 성능이 아니다.

공통 테스트 15개는 두 새 파일의 필수 필드·자료형·ID·참조·좌표 범위, 재구성 경로의 실제 사용, 메타데이터 제외, 기존 계산 입력과 `load_inputs` checksum 일치, 위 두 근사 결과를 검사하며 통과했다. C 기존 테스트 22개도 별도 프로세스에서 통과했다. B 코드나 B 의존 테스트는 공통 테스트에 추가하지 않았으며, C 테스트의 모의 포트 검사와 실제 B 연결 검사는 구분한다.

이와 별도로 저장소 밖 임시 가상환경(Python 3.13.7, B의 `sim/requirements.txt`에 명시된 `simpy==4.1.2`)에서 B PR #6의 고정 커밋 `99df9bf28b9b9eadcd93cf48cb433e88fed2dbe0`을 연결했다. 현재 공통 파일을 `compose_inputs()`로 재구성하고 현재 C.Part 코드와 B 어댑터·엔진으로 실행한 결과, 균등은 수송 7명·미수송 2명, 수요 비례는 수송 6명·미수송 3명이며 미결정 인원은 모두 0명이었다. 두 안은 동일한 RunInputs를 사용했다. 이는 해당 합성 기본 사례의 별도 연결 확인이며, B 손계산 사례의 대조·독립 검증·전체 팀 통합 완료가 아니다. B가 반환한 `independently_verified`도 `false`다. 위 사례표의 개별 경계·사건 동작은 이 합계 확인만으로 검증됐다고 보지 않는다.

부족한 사례와 최소 추가 입력:

| 사례 | 후속 입력·확인 |
| --- | --- |
| 대기 경계 | B가 최대 대기 직전/동일/직후 도착·운행편·탑승 순서와 손계산 기대값 제공 |
| 차량 일정 중복 | B가 동일 차량의 충돌 임무·이동/복귀 시간과 위반 기대값 제공 |
| 정상 교통 환승 | A/D가 정상 GTFS 정류장·운행편·서비스일·정류장 대응, 보행·환승 최소시간·조회 실패 의미 연결 |
| 순환·반복 방문 | A/C/D가 direction·sequence·occurrence ID와 미지원 처리 합의 |

정상 GTFS 연결은 A와 D의 후속 작업이다. 이번 fixture에는 정상 운행편·환승 보행·API 요청·B 사건 로그·정답 파일이 없다.

리뷰 시 B·C·D와 ID namespace·버전 대응, 서비스일 시간 기준, 방향별 수송과 공차 구분, 허용 전이·회차 의미, 미지원 반복 방문, 도착/탑승 및 대기 경계의 사건 순서, 정상 교통 환승과 데이터 부족 상태를 확인한다. 공용 계약이나 운영 정책은 이 문서로 확정하지 않는다.
