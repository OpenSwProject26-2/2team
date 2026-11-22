"""파업이 없을 때 역까지 소요 시간 비교(설계 3.2): 평소 버스 대 우리 셔틀.

역으로 가는 승객 한 사람마다 "출발 정류장 도착 → 목적 정류장 도착" 시간을 평소 버스와 셔틀(차량 11·22·44대)로 비교한다.

입력
- 평소 버스 표(자료팀 제공 예정): `normal_wait.csv`(대기), `normal_invehicle_k5.csv`(차내). 두 표 모두 열
  `origin_stop, station_id, hour, value_s, n`. 표본 n < 5인 칸은 표에 없다(읽을 때도 n < `min_n`인 행은 버린다).
  평소 시간(출발 정류장, 역, 시) = 대기 value_s + 차내 value_s. 두 표 중 하나라도 칸이 없으면 그 OD-시에는 평소 시간이 없다.
- 셔틀 시뮬레이터(M05) 출력 `passenger_outcomes.csv`(실행 1회 = 하루). 셔틀 시간 = arrival_s − time_s(대기 + 차내).
  시 = time_s // 3600(출발 정류장 도착 시각 기준). 목적 정류장 → 역은 호출자가 `dest_station`(정류장 → 역 ID)으로 준다.
  `dest_station`에 없는 목적 정류장의 승객은 역으로 가는 승객이 아니므로 세지 않는다(`skipped_not_station`).

지표 정의(시나리오 하나 = 결과 파일 하나)
- 지연 delay = 셔틀 시간 − 평소 시간(초). 평소 시간이 있고 셔틀로 도착한(served) 승객만 유한한 값을 가진다.
- 못 도착한 승객: outcome이 unserved(만료)·censored(시뮬 끝까지 대기/차내) 모두 "도착 못 함"으로 보고 delay = +∞로 둔다.
- 평소 시간이 없는 승객(no_baseline)은 지연 통계에서 빼고 개수와 덮임 비율(coverage = with_baseline / passengers)만 보고한다.
- 분위수 두 가지
  - `*_served_s`: 평소 시간이 있는 served 승객의 유한한 지연만으로 구한다(못 도착한 승객 수는 `not_arrived`로 따로 보고).
  - `*_all_s`: 평소 시간이 있는 승객 전원(못 도착한 승객은 +∞)으로 구한다. 분위수가 +∞에 떨어지면 `math.inf`.
    (JSON으로 쓸 때는 문자열 "inf"로 바꾼다.)
  - 대상이 없으면 None.
- 분위수 방법: 최근접 순위(nearest-rank). 값 n개를 오름차순 정렬해 순위 k = max(1, ceil(q·n))번째 값(1부터 셈).
  보간하지 않으므로 결과는 항상 실제 값 중 하나다. 중앙값(q = 0.5)은 n이 짝수면 아래쪽 값이다.
  시뮬레이터 `sim.metrics.nearest_rank_p90`과 같은 식이다.
- 평소 + T 안 도착 비율 share_within[T] = (평소 시간이 있고 served이며 delay ≤ T인 승객 수) ÷ (평소 시간이 있는 승객 수).
  T 기본값 0·600·1200초(0·10·20분). 분모가 0이면 None.
- 시간대별(`by_hour`)로도 같은 지표를 낸다.

판정(`judge`, 미리 정한 기준)
1. 차량이 늘수록(11 → 22 → 44) 중앙 지연이 엄격히 줄어든다(같으면 실패, +∞ = +∞도 실패).
2. 가장 큰 차량 조건(44대)의 중앙 지연 ≤ 600초.
판정에 쓰는 중앙값은 기본 `median_all_s`(못 도착 = +∞)다. served만의 중앙값은 차량이 적어 못 태운 승객이 빠지면 오히려
좋아 보일 수 있어서다. `median_served_s` 기준 결과도 함께 낸다.

    python -m busstrike.planning.routegen.delay --wait W.csv --invehicle V.csv --outcomes A.csv [B.csv ...] \\
        --dest-map map.csv --out out.json [--fleets 11,22,44] [--thresholds 0,600,1200]

`--dest-map` CSV 열: `stop_id, station_id`. `--fleets`를 주면 결과 파일 순서대로 차량 대수를 붙이고 판정한다.
외부 의존성은 없다(표준 라이브러리만).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

NormalKey = tuple[str, str, int]          # (출발 정류장, 역, 시)
THRESHOLDS = (0, 600, 1200)
SERVED, UNSERVED, CENSORED = "served", "unserved", "censored"
MIN_N = 5


# ---------------------------------------------------------------- 평소 버스 표

def _read_table(path: Path, min_n: int) -> dict[NormalKey, int]:
    out: dict[NormalKey, int] = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            if int(r["n"]) < min_n:          # 표에 원래 없어야 하지만 들어와도 쓰지 않는다
                continue
            key = (r["origin_stop"].strip(), r["station_id"].strip(), int(r["hour"]))
            if key in out:
                raise ValueError(f"{path}: 같은 칸이 두 번 있다 {key}")
            out[key] = round(float(r["value_s"]))
    return out


def load_normal(wait_csv: Path, invehicle_csv: Path, min_n: int = MIN_N) -> dict[NormalKey, int]:
    """평소 시간(초) = 대기 + 차내. 두 표에 모두 있는 칸만 돌려준다(한쪽만 있으면 평소 시간 없음)."""
    wait = _read_table(Path(wait_csv), min_n)
    inv = _read_table(Path(invehicle_csv), min_n)
    return {k: wait[k] + inv[k] for k in wait.keys() & inv.keys()}


# ---------------------------------------------------------------- 셔틀 결과

@dataclass(frozen=True)
class Record:
    """역으로 가는 승객 1명의 셔틀 결과."""

    passenger_id: str
    origin: str
    station_id: str
    hour: int
    outcome: str                  # served | unserved | censored
    shuttle_s: int | None         # served면 arrival_s − time_s, 아니면 None


def read_outcomes(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def shuttle_records(outcomes_rows: Iterable[Mapping[str, str]], dest_station: Mapping[str, str]) -> tuple[list[Record], int]:
    """`passenger_outcomes.csv` 행 → 역 승객 기록. (기록, 역으로 가지 않아 뺀 승객 수)를 돌려준다."""
    out: list[Record] = []
    skipped = 0
    for r in outcomes_rows:
        station = dest_station.get(str(r["destination"]).strip())
        if station is None:
            skipped += 1
            continue
        outcome = r["outcome"]
        time_s = int(r["time_s"])
        if outcome == SERVED:
            if r.get("arrival_s") in (None, ""):
                raise ValueError(f"served인데 arrival_s가 없다: {r['passenger_id']}")
            shuttle = int(r["arrival_s"]) - time_s
        elif outcome in (UNSERVED, CENSORED):
            shuttle = None
        else:
            raise ValueError(f"모르는 outcome {outcome!r}: {r['passenger_id']}")
        out.append(Record(r["passenger_id"], str(r["origin"]).strip(), station, time_s // 3600, outcome, shuttle))
    return out, skipped


# ---------------------------------------------------------------- 지표

def nearest_rank(values: Sequence[float], q: float) -> float | None:
    """최근접 순위 분위수: 정렬 뒤 max(1, ceil(q·n))번째 값. 값이 없으면 None."""
    if not values:
        return None
    if not 0 < q <= 1:
        raise ValueError(f"q는 (0, 1] 안이어야 한다: {q}")
    ordered = sorted(values)
    return ordered[max(1, math.ceil(q * len(ordered))) - 1]


def _summary(records: Sequence[Record], normal: Mapping[NormalKey, int], thresholds: Sequence[int]) -> dict:
    served = sum(1 for r in records if r.outcome == SERVED)
    based = [(r, normal[(r.origin, r.station_id, r.hour)]) for r in records
             if (r.origin, r.station_id, r.hour) in normal]
    finite = [r.shuttle_s - base for r, base in based if r.outcome == SERVED]
    with_all = finite + [math.inf] * (len(based) - len(finite))
    n_b = len(based)
    return {
        "passengers": len(records),
        "served": served,
        "unserved": sum(1 for r in records if r.outcome == UNSERVED),
        "censored": sum(1 for r in records if r.outcome == CENSORED),
        "with_baseline": n_b,
        "no_baseline": len(records) - n_b,
        "coverage": (n_b / len(records)) if records else None,
        "served_with_baseline": len(finite),
        "not_arrived": n_b - len(finite),          # 평소 시간 있음 + unserved/censored
        "median_served_s": nearest_rank(finite, 0.5),
        "p90_served_s": nearest_rank(finite, 0.9),
        "median_all_s": nearest_rank(with_all, 0.5),
        "p90_all_s": nearest_rank(with_all, 0.9),
        "share_within": {int(t): (sum(1 for d in finite if d <= t) / n_b) if n_b else None for t in thresholds},
    }


def delay_metrics(records: Sequence[Record], normal: Mapping[NormalKey, int],
                  thresholds: Sequence[int] = THRESHOLDS) -> dict:
    """시나리오 하나의 지연 지표(정의는 모듈 설명). 전체 + `by_hour`(시 → 같은 지표)."""
    by_hour: dict[int, list[Record]] = defaultdict(list)
    for r in records:
        by_hour[r.hour].append(r)
    out = _summary(records, normal, thresholds)
    out["thresholds_s"] = [int(t) for t in thresholds]
    out["by_hour"] = {h: _summary(rs, normal, thresholds) for h, rs in sorted(by_hour.items())}
    return out


# ---------------------------------------------------------------- 판정

def _judge_key(by_fleet: Mapping[int, Mapping], fleets: Sequence[int], key: str, max_delay_s: float) -> dict:
    medians = [by_fleet[f][key] for f in fleets]
    known = all(m is not None for m in medians)
    mono = known and all(a > b for a, b in zip(medians, medians[1:]))   # inf > inf는 거짓
    top = medians[-1]
    within = top is not None and top <= max_delay_s
    return {"medians_s": dict(zip(fleets, medians)), "monotonic_decrease": mono,
            "top_fleet_within_limit": within, "pass": mono and within}


def judge(by_fleet: Mapping[int, Mapping], fleets: Sequence[int] = (11, 22, 44), max_delay_s: float = 600,
          key: str = "median_all_s") -> dict:
    """미리 정한 기준: 차량이 늘수록 중앙 지연이 엄격히 감소 + 가장 큰 차량 조건의 중앙 지연 ≤ max_delay_s.

    `key` 기준 결과가 판정(`pass`)이고, 다른 중앙값 기준 결과는 `other`로 같이 낸다. 중앙값이 None이면 실패.
    """
    fleets = sorted(fleets)
    missing = [f for f in fleets if f not in by_fleet]
    if missing:
        raise KeyError(f"차량 조건 결과가 없다: {missing}")
    other = "median_served_s" if key == "median_all_s" else "median_all_s"
    main = _judge_key(by_fleet, fleets, key, max_delay_s)
    return {"key": key, "fleets": list(fleets), "max_delay_s": max_delay_s, **main,
            "other": {"key": other, **_judge_key(by_fleet, fleets, other, max_delay_s)}}


# ---------------------------------------------------------------- CLI

def load_dest_map(path: Path) -> dict[str, str]:
    """`stop_id, station_id` CSV → 정류장 → 역. 한 정류장이 다른 역 두 곳에 매이면 멈춘다."""
    out: dict[str, str] = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            s, k = r["stop_id"].strip(), r["station_id"].strip()
            if out.get(s, k) != k:
                raise ValueError(f"{path}: 정류장 {s}가 역 두 곳({out[s]}, {k})에 매여 있다")
            out[s] = k
    return out


def _jsonable(x):
    if isinstance(x, float) and math.isinf(x):
        return "inf"
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    return x


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m busstrike.planning.routegen.delay")
    ap.add_argument("--wait", required=True, type=Path, help="normal_wait.csv")
    ap.add_argument("--invehicle", required=True, type=Path, help="normal_invehicle_k5.csv")
    ap.add_argument("--outcomes", required=True, nargs="+", type=Path, help="passenger_outcomes.csv(시나리오마다 하나)")
    ap.add_argument("--dest-map", required=True, type=Path, help="stop_id, station_id CSV")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--fleets", default=None, help="결과 파일 순서대로 차량 대수(예: 11,22,44). 주면 판정한다")
    ap.add_argument("--thresholds", default=",".join(map(str, THRESHOLDS)))
    ap.add_argument("--max-delay-s", default=600, type=float)
    a = ap.parse_args(argv)

    thresholds = tuple(int(x) for x in a.thresholds.split(","))
    normal = load_normal(a.wait, a.invehicle)
    dest = load_dest_map(a.dest_map)
    fleets = None if a.fleets is None else [int(x) for x in a.fleets.split(",")]
    if fleets is not None and len(fleets) != len(a.outcomes):
        ap.error("--fleets 개수가 --outcomes 개수와 다르다")

    scenarios = []
    by_fleet: dict[int, dict] = {}
    for i, path in enumerate(a.outcomes):
        recs, skipped = shuttle_records(read_outcomes(path), dest)
        m = delay_metrics(recs, normal, thresholds)
        m["skipped_not_station"] = skipped
        entry = {"outcomes": str(path), "metrics": m}
        if fleets is not None:
            entry["fleet"] = fleets[i]
            by_fleet[fleets[i]] = m
        scenarios.append(entry)
    result = {"normal_cells": len(normal), "scenarios": scenarios}
    if fleets is not None:
        result["judge"] = judge(by_fleet, fleets=fleets, max_delay_s=a.max_delay_s)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(_jsonable(result), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{a.out}: 시나리오 {len(scenarios)}개, 평소 시간 칸 {len(normal)}개")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
