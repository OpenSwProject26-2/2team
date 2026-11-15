"""M07 평가·독립 검증.

`docs/verifier_spec.md`의 M05 독립 검증기가 여기에 있다. 이 패키지는 M05 엔진(`sim`)과
M04 운행안(`opt`·`busstrike.planning`)을 **import하지 않는다**. 공유하는 것은 명세 문서와
출력 파일 형식뿐이다(명세 §1 코드 분리). 이를 `tests/evaluation/test_import_independence.py`가
정적으로 검사한다.
"""
