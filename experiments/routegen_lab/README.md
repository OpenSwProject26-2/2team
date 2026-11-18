# routegen 노선 실험실

운영자 대시보드(`web/operator`) 서버에 붙는 실험 화면이다. 구 하나의 지도 위에서 다음을 보고 바꿔 본다.

1. 버스 통행 가능 도로(=1): 기존 버스 구간을 OpenStreetMap 도로에 맞춰 표시한 도로
2. 파업 노선, 마을·경기버스 노선
3. 정류장 잔여 수요: 마을·경기 흡수 체크에 따라 빠지는 수요를 따로 표시
4. 셔틀 안: 망(원래/도로) × 수요 변형 × 대수별 사전 계산 안과 시뮬레이터 결과(실험·테스트 5일)
5. 직접 실행: 권역 반경·정차 수·우회율·편도 상한·대수를 바꿔 노선 생성(근사), 표시 중인 안을 하루 시뮬레이션(변환기 → M05 → M07)

시뮬레이션 결과는 실제 운행 성능이 아니다. 도로 망의 새 구간 시간은 그 도로의 버스 실측 속도로 추정한 값이다(출처: 실측 아님).

## 망 두 가지

| 망 | 정류장끼리 이어지는 조건 | 구간 시간 |
| --- | --- | --- |
| 원래 | 기존 버스가 그 순서·방향으로 다니는 구간 | 묶음 실측(04 중앙값) |
| 도로 | 원래 + 버스 통행 가능 도로로 900m 안, 경로 중간에 다른 정류장이 없는 구간 | 원래 구간은 실측, 새 구간은 도로 길이 ÷ 그 도로의 버스 실측 속도 |

도로 망 만들기(`roadnet.py`): OSM 도로 방향 그래프(일방통행 반영) → 정류장을 60m 안 도로 노드에 붙임 → 기존 버스 구간마다 도로 최단경로(≤ max(2×직선, 직선+400m))를 찾아 그 도로에 1 → 1인 도로(양방향 도로면 반대 방향 포함)로 정류장 간 새 구간.
아직 반영하지 않은 것: 회전 제한, 길 건너편 정류장 구분(같은 도로 노드에 붙을 수 있음), 매칭 못 한 기존 구간(구로 144/1,858개).

## 실행

```sh
W=<저장소 밖 작업 폴더>; R=$W/zip_v4/region     # region_bundles_0610_v4.zip을 푼 곳
python3 experiments/routegen_lab/fetch_osm.py --root $R/guro_0610_v4 --out $W/osm/guro_roads.json   # 1) OSM 도로(한 번)
# 2) 사전 계산(망 2 × 변형 4 × 대수 × 실험·테스트 5일)
python3 experiments/routegen_lab/run_networks.py --region $R --osm $W/osm/guro_roads.json --gu guro --out $W/lab_guro --jobs 7
# 3) 화면 레이어
python3 experiments/routegen_lab/build_lab_data.py --region $R --osm $W/osm/guro_roads.json --gu guro --lab $W/lab_guro
# 4) 대시보드 서버(실험실 포함)
python3 web/operator/serve.py --lab-dir $W/lab_guro --lab-region $R
# → http://127.0.0.1:8766/lab
```

데이터·실행 결과는 저장소 밖(`--out`, `--lab`)에 둔다. 커밋하지 않는다.
