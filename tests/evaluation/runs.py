"""검증기 테스트용 실행 디렉터리 만들기·변조 도구.

입력은 `tests/sim/cases.py`의 합성 시나리오이고, 실행 결과는 `busstrike.sim.run`으로 만든다.
테스트 쪽에서 sim을 쓰는 것은 fixture 생성일 뿐이고, 검증기 코드(`src/busstrike/evaluation/`)의
독립성과는 별개다(simulator-progress.md 2단계 인계).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Callable

from busstrike.sim import run

from tests.sim import cases

EVENT_KEYS = ("seq", "t", "type", "vehicle_id", "trip_id", "visit", "stop_id", "stop_seq",
              "passenger_id", "alighted", "boarded", "left_behind", "load", "cause")
_PHASE = {"no_service": 0, "abandon": 2, "left_waiting": 3}
_RANK = {"arrive": 0, "alight": 1, "board": 2, "denied": 3, "depart": 4, "cancel": 0}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_run(case: cases.Case, d: Path) -> "RunDir":
    """입력과 출력을 한 디렉터리에 둔다(검증기 기본 배치)."""
    paths = case.write(d)
    run(paths["scenario.json"], paths["run_inputs.json"], paths["plan.json"], d)
    return RunDir(d)


class RunDir:
    def __init__(self, d: Path):
        self.d = Path(d)

    # ---- 읽기
    def read_json(self, name: str):
        return json.loads((self.d / name).read_text(encoding="utf-8"))

    def events(self) -> list[dict]:
        return [json.loads(x) for x in (self.d / "events.jsonl").read_text(encoding="utf-8").splitlines()]

    def arrival_s(self) -> dict[str, int]:
        return {p["passenger_id"]: p["arrival_s"] for p in
                (json.loads(x) for x in (self.d / "arrivals.jsonl").read_text(encoding="utf-8").splitlines())}

    # ---- 쓰기
    def write_json(self, name: str, doc) -> None:
        text = json.dumps(doc, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
        (self.d / name).write_bytes(text.encode("utf-8"))

    def write_events(self, events: list[dict], *, resort: bool = True) -> None:
        """§6.1 키 순서로 다시 쓰고, `resort`면 정렬 키로 다시 정렬해 seq를 매긴다(V01을 통과시키는 변조)."""
        if resort:
            arr = self.arrival_s()

            def key(e: dict) -> tuple:
                ph = _PHASE.get(e["type"], 1)
                if ph != 1:
                    return (e["t"], ph, "", 0, 0, (e["passenger_id"],))
                if e["type"] == "alight":
                    order: tuple = (e["passenger_id"],)
                elif e["type"] in ("board", "denied"):
                    order = (arr.get(e["passenger_id"], -1), e["passenger_id"])
                else:
                    order = ()
                return (e["t"], 1, e["vehicle_id"], e["visit"], _RANK[e["type"]], order)

            events = sorted(events, key=key)
        rows = []
        for i, e in enumerate(events):
            e = dict(e, seq=i)
            rows.append(json.dumps({k: e[k] for k in EVENT_KEYS if k in e}, ensure_ascii=False,
                                   separators=(",", ":")) + "\n")
        (self.d / "events.jsonl").write_bytes("".join(rows).encode("utf-8"))

    def resign(self) -> None:
        """출력 파일 해시를 run.json에 다시 적는다. 변조를 V15가 아니라 의미 검사가 잡는지 보기 위함."""
        r = self.read_json("run.json")
        for key, name in (("event_log_sha256", "events.jsonl"), ("metrics_sha256", "metrics.json"),
                          ("timetable_sha256", "timetable.json"), ("plan_sha256", "plan.json")):
            r[key] = _sha((self.d / name).read_bytes())
        self.write_json("run.json", r)

    def edit_events(self, fn: Callable[[list[dict]], list[dict] | None], *, resort: bool = True,
                    resign: bool = True) -> None:
        events = self.events()
        out = fn(events)
        self.write_events(events if out is None else out, resort=resort)
        if resign:
            self.resign()


# ---------------------------------------------------------------- 이벤트 찾기

def find(events: list[dict], **match) -> dict:
    hits = [e for e in events if all(e.get(k) == v for k, v in match.items())]
    if len(hits) != 1:
        raise AssertionError(f"expected one event for {match}, got {len(hits)}")
    return hits[0]
