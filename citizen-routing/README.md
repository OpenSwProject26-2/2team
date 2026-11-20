# 독립형 시민 길찾기

운영자 대시보드와 기존 시민 미리보기를 변경하지 않는 별도 서비스입니다. A GTFS와 B/C 셔틀 GTFS를 읽어 OTP 2.10.0 그래프를 준비합니다. OTP가 실제 운행시간표와 OSM 보행망을 사용해 경로를 계산하며, 시민 API는 그 결과만 반환합니다.

실행과 데이터 계약: [docs/setup.md](docs/setup.md)

- 화면: http://127.0.0.1:8775/
- Place search: GET /api/citizen-routing/places?q=... (local OSM index and GTFS stops)
- API: GET /api/citizen-routing/plan
- OTP: http://127.0.0.1:8080/otp/gtfs/v1
- 준비 자료: A GTFS ZIP, 실제 B/C 셔틀 GTFS(선택), 서울 권역 .osm.pbf, 파업 대상 route_id JSON(선택)
