# 독립형 시민 길찾기

운영자 대시보드와 기존 시민 미리보기를 변경하지 않는 별도 서비스입니다. A GTFS와 선택적으로 제공되는 B/C 셔틀 GTFS를 읽어 OTP 2.10.0 그래프를 준비합니다. OTP가 실제 운행시간표와 OSM 보행망을 사용해 경로를 계산하며, 시민 API는 그 결과만 반환합니다.

현재 화면은 출발지·목적지 검색과 지도 클릭 선택, 추천 경로 카드·지도 강조, 최적 경로순·최소 시간순·최소 환승순·최소 도보순 정렬을 제공합니다. 버스는 마을·시내·경기·미분류로 표시합니다. 시민 지도는 별도 MapLibre 스타일에서 일반 시설 POI를 확대 수준에 따라 단계적으로 표시하며, 정류장·역과 사용자가 선택한 장소 표시는 유지합니다.

실행과 데이터 계약: [docs/setup.md](docs/setup.md)

- 화면: http://127.0.0.1:8775/
- 장소 검색: GET /api/citizen-routing/places?q=... (로컬 OSM 색인과 GTFS 정류장)
- API: GET /api/citizen-routing/plan
- 시민 지도 스타일: GET /citizen-map-style.json (원본 OpenFreeMap Liberty는 유지)
- OTP: http://127.0.0.1:8080/otp/gtfs/v1
- 준비 자료: A GTFS ZIP, 실제 B/C 셔틀 GTFS(선택), 서울 권역 .osm.pbf, 파업 대상 route_id JSON(선택)
