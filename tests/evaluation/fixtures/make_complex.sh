#!/bin/sh
# 합성 complex 망 실행 결과를 만든다(검증기 입력용). 산출물은 크므로 저장소에 넣지 않는다.
#
# 이 스크립트는 B 엔진을 CLI로만 호출한다. 검증기는 결과 *파일*만 읽는다.
#
#   SIM_WORKTREE=<B worktree> OUT=<출력 루트> [PASSENGERS=1k] sh make_complex.sh
#
# 기본값은 저장소 밖이다. 저장소 안에 쓰면 이후 엔진 실행이 dirty=True로 기록된다
# (sim/IMPLEMENTATION.md 규모 파일럿 절).
set -eu

SIM_WORKTREE="${SIM_WORKTREE:?B worktree 경로를 SIM_WORKTREE로 지정하세요}"
PASSENGERS="${PASSENGERS:-1k}"
OUT="${OUT:-$SIM_WORKTREE/../verifier-fixtures}"
PY="${PY:-$SIM_WORKTREE/.venv/bin/python}"

SCEN="$OUT/scenario-complex-$PASSENGERS"
RUN="$OUT/run-complex-$PASSENGERS"
mkdir -p "$OUT"

cd "$SIM_WORKTREE"
echo "# engine commit: $(git rev-parse HEAD)"
"$PY" -m sim.harness generate --network complex --passengers "$PASSENGERS" --out "$SCEN"
"$PY" -m sim.harness run --scenario "$SCEN" --arrival-seed a1 --policy proposed-v0.2 \
    --sink jsonl --write-realization --out "$RUN"
echo "# scenario: $SCEN"
echo "# run:      $RUN"
