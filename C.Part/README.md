# C · 노선 및 차량 배분 최적화

| 항목 | 위치 |
| --- | --- |
| 실제 코드·실행 설명 | [opt/](opt/) |
| 기준안 | [baselines.py](opt/baselines.py) |
| 근사 평가 | [evaluator.py](opt/evaluator.py) |
| 파라미터 탐색·전수 비교 | [search.py](opt/search.py) |
| B 연결 포트 | [integration.py](opt/integration.py) |
| 합성 데이터 | [synthetic.json](opt/examples/synthetic.json) |
| 테스트 | [test_planning.py](tests/opt/test_planning.py) |

저장소 루트에서:

```bash
cd C.Part
python3 -m unittest discover -s tests -t . -v
python3 -m opt --input opt/examples/synthetic.json
```

실제 B 연동은 별도 작업입니다. 결과는 합성 입력의 근사 평가이며 실제 운행 승인이나 성능을 뜻하지 않습니다.
