# 커밋 해시 대응표

2026-11-09에 로컬 커밋 22개의 메시지를 한글로 바꾸고 Claude 공동 작성자 줄을 지웠다(push 전).
메시지만 바뀌었고 파일 내용(트리), 작성자, 작성·커밋 시각은 모두 같다(22개 전부 트리 해시 대조).
`results/p3_memory.*`, `results/scale_pilot.*`, `docs/verifier_spec.md`, `results/runs/` 측정 기록에는
측정 당시 해시가 그대로 남아 있으므로 이 표로 새 해시를 찾는다. 기존 커밋은 로컬 태그
`archive/pre-ko-messages`로 보존한다(push하지 않음).

| 기존 | 새 해시 | 트리 | 제목 |
| --- | --- | --- | --- |
| `7066032` | `37a15c3` | `b194180` | feat(simulation): 결정적 R5 bin 내 균등 도착 생성기 추가 (#5) |
| `2573165` | `c384155` | `de35784` | feat(simulation): C adapter에서 검증된 R5 realization 수용 (#5) |
| `e866796` | `de41d00` | `2225cbe` | feat(simulation): 해시 seed 기반 합성 망과 OD-bin 수요 생성기 추가 (#5) |
| `bb6a78b` | `b067905` | `358c00b` | feat(simulation): 단계별 측정 하네스와 sink 분리 추가 (#5) |
| `39d730f` | `9d37dab` | `c11371f` | feat(simulation): 시간·메모리 가드와 failures.jsonl 기록 추가 (#5) |
| `2c0bce2` | `557bc81` | `56e5a97` | test(simulation): 리팩터링 전 golden 사건 로그 기록 (#5) |
| `b9ea13b` | `69b4601` | `133e4ef` | perf(simulation): 승차 시 전체 승객 스캔을 정류장별 대기열로 교체 (#5) |
| `b93d0f2` | `10b8553` | `1f8d302` | feat(evaluation): M1–M6 지표, 실패 사유, R12 검사 추가 (#5) |
| `3acc250` | `6c4995b` | `78de0b8` | feat(simulation): 측정 하네스에서 M1–M6 보고서 출력 (#5) |
| `26e58e1` | `39218fd` | `7187866` | perf(simulation): 승객·운행편 사건을 지연 스트림으로 처리 (#5) |
| `b4ad699` | `3c05d6a` | `a623238` | perf(simulation): RunInputs 체크섬 분할 계산과 승객 객체 __slots__ 적용 (#5) |
| `5a95596` | `65c266f` | `4d8b134` | feat(simulation): 측정 하네스에 결과 스트리밍 모드 추가 (#5) |
| `c07ae0d` | `e5143a8` | `f4739fc` | feat(simulation): 가드를 적용한 규모 오름차순 파일럿 실행기 추가 (#5) |
| `191babc` | `2ea39e7` | `b14e6cc` | docs(simulation): P3 메모리·시간 변화 측정 보고 (#5) |
| `58e9f91` | `8c840e8` | `417ef11` | fix(simulation): env manifest를 실행 시작 시점에 기록 (#5) |
| `bf867d7` | `8a280f5` | `a73bfaa` | feat(simulation): 하네스에서 R5 realization 파일 선택 저장 (#5) |
| `698aca0` | `b878f29` | `a214e13` | fix(simulation): env manifest에서 추적 파일 변경과 미추적 파일 구분 (#5) |
| `6f8e0bb` | `22b8c76` | `83a0215` | docs(simulation): 복잡 합성망 규모 파일럿 결과 추가 (#5) |
| `b147822` | `ca643f6` | `02c3ec2` | docs(evaluation): 독립 M05 검증기 검사 항목과 fixture 명세 (#5) |
| `cab9f46` | `0a3bb49` | `118a683` | docs(simulation): 파일럿 실행기와 새 하네스 옵션 문서화 (#5) |
| `b402140` | `78cd93d` | `0ed85b9` | Merge origin/main: C.Part 디렉터리 이동 반영 (#5) |
| `e676a37` | `3a7deed` | `cfb4ca0` | refactor(simulation): R5 입력을 C의 정확 시각 arrival_s 정의에 맞춤 (#5) |
