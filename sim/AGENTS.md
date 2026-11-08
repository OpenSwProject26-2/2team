# M05 보조 규칙
원본 규칙은 `../AGENTS.md`, `../CONTRIBUTING.md`, `../docs/contracts/M00_DATA_CONTRACT.md`다.
공용 규칙과 충돌하면 루트 문서가 우선한다. 공용 문서를 복제하거나 독자적으로 변경하지 않는다.
이 파일에는 M05 전용 규칙만 추가한다.

- 내부 시각은 Asia/Seoul service date 기준 정수 초 time_s다.
- 사건은 (time_s, phase, stable_id) 순서로 처리한다.
- phase: 차량 도착·하차 → 승객 발생·도보·환승 도착 → 승차 → 차량 출발 → 기한 만료.
- 승객 물리 상태와 결과 상태를 분리한다.
- served, unserved, censored는 배타적이며 합은 발생 승객 수와 같다.
- 정원 초과, 승객 복제·소실, OD 변경, 시간 역행, 복수 차량 탑승을 금지한다.
- 원시 교통카드 식별자·개인정보·API 키를 사용하지 않는다.
- M04·M06은 포트 또는 adapter로만 연결하고 M07을 직접 import하지 않는다.
- 미정 주행시간·환승·혼잡 정책에 숨은 기본값을 두지 않는다.
