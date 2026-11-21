"""역 ID → 호선·지선 꼬리표 대응표(`station_lines.csv`)를 만든다. 서울 전체 역(다른 구에서도 쓰도록).

    python3 experiments/routegen_lab/build_station_lines.py \
        --capacity <seoul_routegen_inputs_0610_v4>/subway/subway_capacity_0609.csv \
        --stations <seoul_routegen_inputs_0610_v4>/network/stations.csv \
        [--out experiments/routegen_lab/station_lines.csv]

출처: `subway_capacity_0609.csv`의 `line`, `station_id`(역 ID는 호선마다 따로다. 같은 ID가 호선 둘 이상에 나오면
모든 호선을 갖는다). 꼬리표(`tags`, 공백으로 구분)는 "같은 호선·같은 지선" 판정에 쓴다. 두 역의 꼬리표가 하나라도 겹치면
같은 호선·같은 지선이다(`busstrike.planning.routegen.reach`).

지선 규칙(이 스크립트의 `branch_tags`가 원본)
- 1호선: 경인선(18xx) → `1-경인`, 경부선(17xx)·장항선(14xx) → `1-경부`. 두 갈래 열차가 함께 다니는 구간
  (구로 1701과 그 북쪽: 영등포 1006·신도림 1007 등 10xx, 서울역~청량리 01xx, 경원선 19xx) → 둘 다.
- 2호선: 신정지선(도림천 0247·양천구청 0248·신정네거리 0249·까치산 0200) → `2-신정지선`, 갈림 역 신도림 0234는 `2`와 둘 다.
  성수지선(용답 0244·신답 0245·신설동 0246·용두 0250) → `2-성수지선`, 갈림 역 성수 0211은 둘 다.
- 5호선: 마천지선(둔촌동 2555 ~ 마천 2561) → `5-마천지선`, 갈림 역 강동 2549는 `5`와 둘 다.
- 나머지 호선은 호선 번호 하나.
`--stations`의 역 중 표에 없는 역(예: 신림선 44xx)은 꼬리표 없이 `missing` 목록으로 출력한다(표에 넣지 않음).
그런 역은 평가에서 자기 자신만 같은 호선으로 본다(`reach.StationIndex`).
같은 이름 역(환승역의 호선별 ID)은 `complex`(이름) 칸으로 묶어 둔다. 기본 평가에는 쓰지 않는다(옵션 `transfer_complex`).
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

SHINJEONG = {"0247", "0248", "0249", "0200"}
SEONGSU = {"0244", "0245", "0246", "0250"}
MACHEON = {f"{i:04d}" for i in range(2555, 2562)}


def branch_tags(station_id: str, line: str) -> list[str]:
    s = station_id
    if line == "1":
        if s.startswith("18"):
            return ["1-경인"]
        if (s.startswith("17") and s != "1701") or s.startswith("14"):
            return ["1-경부"]
        return ["1-경부", "1-경인"]
    if line == "2":
        if s in SHINJEONG:
            return ["2-신정지선"]
        if s == "0234":
            return ["2", "2-신정지선"]
        if s in SEONGSU:
            return ["2-성수지선"]
        if s == "0211":
            return ["2", "2-성수지선"]
        return ["2"]
    if line == "5":
        if s in MACHEON:
            return ["5-마천지선"]
        if s == "2549":
            return ["5", "5-마천지선"]
        return ["5"]
    return [line]


def build(capacity: Path, stations: Path | None) -> tuple[list[dict], list[dict]]:
    lines: dict[str, set[str]] = {}
    names: dict[str, str] = {}
    with open(capacity, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            lines.setdefault(r["station_id"], set()).add(r["line"])
            names[r["station_id"]] = r["station_name"]
    rows = []
    for sid in sorted(lines):
        tags = sorted({t for ln in lines[sid] for t in branch_tags(sid, ln)})
        rows.append({"station_id": sid, "station_name": names[sid], "lines": " ".join(sorted(lines[sid])),
                     "tags": " ".join(tags), "complex": names[sid]})
    missing = []
    if stations:
        with open(stations, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                sid = r.get("station_id") or r.get("stop_id")
                if sid not in lines:
                    missing.append({"station_id": sid, "station_name": r.get("station_name") or r.get("stop_name")})
    return rows, missing


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--capacity", required=True, type=Path)
    ap.add_argument("--stations", type=Path, default=None, help="표에 없는 역을 찾을 역 목록(서울 전체 stations.csv)")
    ap.add_argument("--out", type=Path, default=HERE / "station_lines.csv")
    args = ap.parse_args()
    rows, missing = build(args.capacity, args.stations)
    with open(args.out, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, ["station_id", "station_name", "lines", "tags", "complex"], lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print(json.dumps({"stations": len(rows), "out": str(args.out), "missing": missing}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
