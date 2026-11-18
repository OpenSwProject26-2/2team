"""서울 25개 자치구 경계 GeoJSON(`web/operator/data/districts.geojson`)을 OSM 행정경계에서 만든다(수동 실행).

    python3 web/operator/build_districts.py --osm <Overpass out geom JSON> [--out web/operator/data/districts.geojson]

입력은 Overpass 질의 `rel["boundary"="administrative"]["admin_level"="6"](서울 범위);out geom;` 결과다.
- 서울 구만 남긴다(행정안전부 행정코드 `ref:KR:mois:admin`이 11로 시작).
- 속성: `sgg`(5자리 구 코드 = 행정코드 앞 5자리), `sggnm`(구 이름), `sido`=11, `sidonm`=서울특별시.
- 외곽(outer) 길을 끝점으로 이어 고리를 만들고 Douglas-Peucker로 단순화한다(기본 약 10m). 외부 패키지 없음.
- 데이터: © OpenStreetMap contributors, ODbL 1.0. 이 파일을 재배포할 때 출처와 라이선스를 함께 둔다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def rings(ways: list[list[tuple[float, float]]]) -> list[list[tuple[float, float]]]:
    """끝점이 맞닿는 길들을 이어 닫힌 고리로."""
    left = [list(w) for w in ways if len(w) >= 2]
    out = []
    while left:
        ring = left.pop(0)
        while ring[0] != ring[-1]:
            for i, w in enumerate(left):
                if w[0] == ring[-1]:
                    ring += w[1:]
                elif w[-1] == ring[-1]:
                    ring += w[-2::-1]
                elif w[-1] == ring[0]:
                    ring = w[:-1] + ring
                elif w[0] == ring[0]:
                    ring = w[:0:-1] + ring
                else:
                    continue
                left.pop(i)
                break
            else:
                raise ValueError("외곽 길이 닫히지 않는다")
        out.append(ring)
    return out


def simplify(pts: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    """Douglas-Peucker(경위도 그대로, tol 도 단위). 닫힌 고리는 첫 점을 유지한다."""
    if len(pts) < 5:
        return pts

    def dp(a: int, b: int, keep: set[int]) -> None:
        (x1, y1), (x2, y2) = pts[a], pts[b]
        dx, dy = x2 - x1, y2 - y1
        n2 = dx * dx + dy * dy
        best, at = -1.0, -1
        for i in range(a + 1, b):
            x, y = pts[i]
            if n2 == 0:
                d = (x - x1) ** 2 + (y - y1) ** 2
            else:
                t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / n2))
                d = (x - x1 - t * dx) ** 2 + (y - y1 - t * dy) ** 2
            if d > best:
                best, at = d, i
        if at >= 0 and best > tol * tol:
            keep.add(at)
            dp(a, at, keep)
            dp(at, b, keep)

    mid = len(pts) // 2                      # 닫힌 고리는 두 반으로 나눠 단순화
    keep = {0, mid, len(pts) - 1}
    dp(0, mid, keep)
    dp(mid, len(pts) - 1, keep)
    return [pts[i] for i in sorted(keep)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--osm", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=HERE / "data" / "districts.geojson")
    ap.add_argument("--tol", type=float, default=0.0001, help="단순화 허용 오차(도, 약 10m)")
    args = ap.parse_args()
    feats = []
    for e in json.loads(args.osm.read_text(encoding="utf-8"))["elements"]:
        t = e.get("tags", {})
        code = t.get("ref:KR:mois:admin", "")
        if e.get("type") != "relation" or not code.startswith("11"):
            continue
        ways = [[(round(g["lon"], 6), round(g["lat"], 6)) for g in m["geometry"]]
                for m in e.get("members", []) if m.get("type") == "way" and m.get("role") == "outer" and m.get("geometry")]
        polys = [[[list(p) for p in simplify(r, args.tol)]] for r in rings(ways)]
        geom = {"type": "Polygon", "coordinates": polys[0]} if len(polys) == 1 else {"type": "MultiPolygon", "coordinates": polys}
        feats.append({"type": "Feature", "properties": {"sgg": code[:5], "sggnm": t["name"], "sido": "11", "sidonm": "서울특별시",
                                                        "osm_relation": e["id"]}, "geometry": geom})
    feats.sort(key=lambda f: f["properties"]["sgg"])
    doc = {"type": "FeatureCollection",
           "source": "OpenStreetMap 행정경계(admin_level 6), © OpenStreetMap contributors, ODbL 1.0. build_districts.py로 단순화",
           "features": feats}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(feats)}개 구 → {args.out} ({args.out.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
