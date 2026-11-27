"""운영자 실행 입력 자동 조립: 구·파업 범위·기간을 받아 파이프라인 입력 파일이 다 있는지 검사하고 manifest.json을 남긴다.

    from busstrike.data import assemble
    got = assemble("guro", {"mode": "full"}, ["20261026", …, "20261030"], ["20261028"])
    got["input_hash"], got["manifest_path"], got["cached"]

- 경로는 config/local_paths.toml(커밋 안 함)에서 읽는다(paths.load_paths).
- 검사 항목은 docs/design/pipeline.md·routegen-data.md의 입력: D1 구 실험 주간 묶음(수요·정류장·소요시간),
  D2 생성기 입력(버스 통행 도로망 road.json + turns.json, OSM 도로, 필수 시설, 구별 설정), D3 지자체안 변환본.
  하나라도 없으면 AssembleError(무엇이 없는지 + 만드는 법)를 낸다.
- 파일마다 크기·sha256을 manifest에 적는다. sha256은 (경로, 크기, 수정 시각)이 같으면 다시 계산하지 않는다(sha_cache.json).
- 입력 해시 = sha256(구, 파업, 기간, manifest의 파일 해시, 코드 커밋). 같은 해시면 이전 manifest를 그대로 쓴다(cached=True).
- 테스트 주간(11/02~06) 날짜는 받지 않고, <구>_0610_v4_test 폴더·*_test.zip은 경로조차 만들지 않는다.
- 카드 ID·승객 단위 자료는 읽지 않는다(파일 해시만 계산).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .paths import REPO, LocalPaths, load_paths

EXP_WEEK = ("20261026", "20261027", "20261028", "20261029", "20261030")
TEST_WEEK = tuple(f"202611{d:02d}" for d in range(2, 7))
STRIKE_SEED = 20261121                  # 파이프라인 파업 범위 추첨 seed(고정, pipeline/run.py STRIKE_SEED)
LAB = REPO / "experiments" / "routegen_lab"


class AssembleError(Exception):
    """입력이 모자라거나 잘못됨. missing = [{item, path, how}]."""

    def __init__(self, message: str, missing: list[dict] | None = None):
        super().__init__(message)
        self.missing = missing or []


@dataclass(frozen=True)
class Item:
    group: str        # D1 | D2 | D3 | 원본
    name: str
    path: Path
    how: str          # 없을 때 만드는 법 / 받을 곳
    required: bool = True


def normalize_strike(strike: dict | float | None) -> dict:
    """{"mode": "full"} | {"mode": "ratio", "ratio": 0~1, "seed": 20261121} → 정규화. 파이프라인 값 rate는 1.0 = 전면."""
    if strike is None or strike == 1.0 or (isinstance(strike, dict) and strike.get("mode", "full") == "full"):
        return {"mode": "full", "ratio": 1.0, "seed": STRIKE_SEED}
    if isinstance(strike, (int, float)):
        strike = {"mode": "ratio", "ratio": float(strike)}
    ratio = float(strike.get("ratio", 0))
    if not 0 < ratio <= 1:
        raise AssembleError(f"파업 비율은 0 초과 1 이하: {ratio}")
    seed = int(strike.get("seed", STRIKE_SEED))
    if seed != STRIKE_SEED:
        raise AssembleError(f"파업 노선 추첨 seed는 파이프라인 고정값 {STRIKE_SEED}만 지원한다(받은 값 {seed})")
    return {"mode": "full" if ratio == 1.0 else "ratio", "ratio": ratio, "seed": seed}


def check_days(demand_days: list[str], eval_days: list[str]) -> tuple[list[str], list[str]]:
    dd = sorted({d.replace("-", "") for d in demand_days})
    ed = sorted({d.replace("-", "") for d in eval_days})
    bad = sorted((set(dd) | set(ed)) & set(TEST_WEEK))
    if bad:
        raise AssembleError(f"테스트 주간(11/02~06)은 고를 수 없다: {bad}")
    if dd != list(EXP_WEEK):
        raise AssembleError("생성 수요 기간은 실험 주간 10/26~30 전체만 지원한다(파이프라인 demand_period=exp)")
    if not ed or set(ed) - set(EXP_WEEK):
        raise AssembleError(f"평가 날짜는 실험 주간 10/26~30 안에서 하루 이상: {ed}")
    return dd, ed


def items(gu: str, paths: LocalPaths, demand_days: list[str]) -> list[Item]:
    if not paths.work_root:
        raise AssembleError("config/local_paths.toml의 paths.work_root가 없다(예시: config/local_paths.example.toml)",
                            [{"item": "설정", "path": "config/local_paths.toml",
                              "how": "config/local_paths.example.toml을 복사해 경로를 채운다"}])
    W = paths.work_root
    root = W / "region_all" / f"{gu}_0610_v4"
    unzip = (f"원본 bundles_v4/split/{gu}_0610_v4.zip(또는 region_bundles_0610_v4.zip의 {gu}_0610_v4)을 "
             f"{W / 'region_all'}에 푼다. 실험 주간 묶음만 풀고 _test 묶음은 풀지 않는다")
    out = [Item("D1", "정류장(network/stops.csv)", root / "network/stops.csv", unzip),
           Item("D1", "역(network/stations.csv)", root / "network/stations.csv", unzip),
           Item("D1", "역 지정 정류장(network/station_stops.csv)", root / "network/station_stops.csv", unzip),
           Item("D1", "정류장 간 소요시간(network/travel_times.json)", root / "network/travel_times.json", unzip),
           Item("D1", "흡수 노선(network/absorbing_routes.csv)", root / "network/absorbing_routes.csv", unzip),
           Item("D1", "수요 원자료 집계(demand/demand_flows.csv.gz)", root / "demand/demand_flows.csv.gz", unzip)]
    for d in demand_days:
        b = root / "bundles" / f"affected_{d}"
        out += [Item("D1", f"수요 묶음 {d} demand_bins.csv", b / "demand_bins.csv", unzip),
                Item("D1", f"수요 묶음 {d} travel_times.json", b / "travel_times.json", unzip)]
        scen = sorted(b.glob("scenario_f*.json")) if b.is_dir() else []
        out.append(Item("D1", f"수요 묶음 {d} 시나리오 틀(scenario_f*.json)", scen[0] if scen else b / "scenario_f*.json",
                        unzip))
    sweep, muni = paths.sweep_dir(gu), paths.muni_dir(gu)
    no_sweep = ("우리 안 망 폴더: roadnet.py로 OSM 버스 도로에서 net/road.json·turns.json을 만들고 hub_sweep.py로 hub_sweep.json "
                "(docs/design/routegen-data.md). 이 PC의 구로 = routegen_integrated/improve_1122/final/all25/guro/sweep")
    out += [Item("D2", "버스 통행 도로망(net/road.json)", (sweep or Path("<sweep 미설정>")) / "net/road.json", no_sweep),
            Item("D2", "U턴 금지(net/turns.json)", (sweep or Path("<sweep 미설정>")) / "net/turns.json", no_sweep),
            Item("D2", "허브 설정(hub_sweep.json)", (sweep or Path("<sweep 미설정>")) / "hub_sweep.json", no_sweep),
            Item("D2", "OSM 도로(osm/<구>_roads.json)", W / "osm" / f"{gu}_roads.json",
                 "experiments/routegen_lab 도로 추출(routegen-data.md 'OSM 도로')로 만든다"),
            Item("D2", "필수 시설(facilities_seoul.csv)", W / "routegen_v2/facilities/facilities_seoul.csv",
                 "facilities_seoul_v1.zip을 routegen_v2/facilities에 푼다"),
            Item("D2", "구별 설정(gu_params_r1.json)", LAB / "gu_params_r1.json", "저장소에 있음"),
            Item("D2", "구별 설정(gu_params.json)", LAB / "gu_params.json", "저장소에 있음"),
            Item("D2", "호선 꼬리표(station_lines.csv)", LAB / "station_lines.csv", "저장소에 있음"),
            Item("D3", "지자체안 운행안(plan.json)", (muni or Path("<muni 미설정>")) / "plan.json",
                 "eval_municipal.py로 지자체안 변환(improve_1122/final/all25/<구>/muni07)"),
            Item("D3", "지자체안 무정차 구간 망(road_muni.json)", (muni or Path("<muni 미설정>")) / "road_muni.json",
                 "eval_municipal.py 출력")]
    if paths.transit_data:
        z = paths.transit_data / "bundles_v4" / "split" / f"{gu}_0610_v4.zip"
        out.append(Item("원본", f"원본 묶음 {z.name}(해시 기록만)", z, "seoul_transit_data 원본", required=False))
    return out


def _git_commit() -> str:
    try:
        head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain", "--untracked-files=no", "--",
                                "src", "experiments", "citizen-routing/backend"],     # 결과에 영향 주는 코드만(화면 파일 제외)
                               capture_output=True, text=True).stdout.strip()
        return head + ("-dirty" if dirty else "")
    except OSError:
        return "unknown"


class ShaCache:
    def __init__(self, path: Path):
        self.path = path
        try:
            self.data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}
        self.dirty = False

    def sha(self, f: Path) -> tuple[int, str, str]:
        st = f.stat()
        key = str(f.resolve())
        hit = self.data.get(key)
        if hit and hit[0] == st.st_size and hit[1] == st.st_mtime_ns:
            return st.st_size, hit[2], _iso(st.st_mtime)
        h = hashlib.sha256()
        with f.open("rb") as s:
            for chunk in iter(lambda: s.read(1 << 20), b""):
                h.update(chunk)
        self.data[key] = [st.st_size, st.st_mtime_ns, h.hexdigest()]
        self.dirty = True
        return st.st_size, h.hexdigest(), _iso(st.st_mtime)

    def save(self) -> None:
        if self.dirty:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, self.path)


def _iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))


def assemble(gu: str, strike: dict | float | None, demand_days: list[str], eval_days: list[str], *,
             paths: LocalPaths | None = None, cache_root: Path | None = None, code_commit: str | None = None) -> dict:
    """입력 검사 + manifest.json. 돌려주는 값: input_hash, manifest_path, manifest, cached, seconds."""
    t0 = time.time()
    if not gu or not gu.replace("_", "").isalpha() or not gu.islower():
        raise AssembleError(f"구 ID가 잘못됐다: {gu!r}")
    paths = paths or load_paths()
    st = normalize_strike(strike)
    dd, ed = check_days(demand_days, eval_days)
    its = items(gu, paths, dd)
    for it in its:
        if "_test" in it.path.name or "_v4_test" in str(it.path):
            raise AssembleError(f"테스트 주간 자료 경로가 섞였다: {it.path}")
    missing = [{"group": it.group, "item": it.name, "path": str(it.path), "how": it.how}
               for it in its if it.required and not it.path.is_file()]
    if missing:
        raise AssembleError(f"{gu} 입력 {len(missing)}개가 없다: " + ", ".join(m["item"] for m in missing[:5]), missing)
    cache_root = Path(cache_root) if cache_root else (paths.pareto_root / "_assemble" if paths.pareto_root else None)
    if cache_root is None:
        raise AssembleError("조립 캐시 위치가 없다(paths.pareto_root 설정)")
    sc = ShaCache(cache_root / "sha_cache.json")
    files = []
    for it in its:
        if not it.path.is_file():
            files.append({"group": it.group, "item": it.name, "path": str(it.path), "present": False})
            continue
        size, h, mtime = sc.sha(it.path)
        files.append({"group": it.group, "item": it.name, "path": str(it.path), "present": True, "size": size,
                      "mtime": mtime, "sha256": h})
    sc.save()
    commit = code_commit if code_commit is not None else _git_commit()
    key = {"gu": gu, "strike": st, "demand_days": dd, "eval_days": ed, "code_commit": commit,
           "files": sorted((f["path"], f.get("sha256")) for f in files if f["present"])}
    input_hash = hashlib.sha256(json.dumps(key, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    mdir = cache_root / gu / input_hash[:16]
    mp = mdir / "manifest.json"
    cached = mp.is_file()
    if cached:
        manifest = json.loads(mp.read_text(encoding="utf-8"))
    else:
        manifest = {"schema": "busstrike.assemble/1", "gu": gu, "strike": st, "demand_days": dd, "eval_days": ed,
                    "code_commit": commit, "input_hash": input_hash, "created_at": _iso(time.time()),
                    "dirs": {"work_root": str(paths.work_root), "region": str(paths.work_root / "region_all" / f"{gu}_0610_v4"),
                             "sweep": str(paths.sweep_dir(gu)), "muni": str(paths.muni_dir(gu))},
                    "test_week": "열지 않음(11/02~06 날짜 거절, _test 묶음 경로 없음)",
                    "files": files}
        mdir.mkdir(parents=True, exist_ok=True)
        tmp = mp.with_suffix(".tmp")
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, mp)
    return {"input_hash": input_hash, "manifest_path": str(mp), "manifest": manifest, "cached": cached,
            "seconds": round(time.time() - t0, 3)}


def muni_fleet(gu: str, paths: LocalPaths | None = None) -> int | None:
    """지자체안 대수(가용 대수 기본값). 변환본이 없으면 None."""
    paths = paths or load_paths()
    m = paths.muni_dir(gu)
    try:
        plan = json.loads((m / "plan.json").read_text(encoding="utf-8")) if m else None
    except (OSError, ValueError):
        return None
    return sum(int(r.get("vehicles", 0)) for r in plan["routes"]) if plan else None
