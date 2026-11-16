"""구 실험 묶음 읽기와 변형별 잔여 수요.

묶음 구조(구로 `guro_0610`): network/{travel_times.json, stops.csv, stations.csv, station_stops.csv},
demand/demand_flows.csv.gz, bundles/<변형>_<YYYYMMDD>/{scenario_f*.json, demand_bins.csv, ...}.

잔여 수요 r(s→k, t)
- `walkable == False`이고 `station_stop_id`가 있는 행만 쓴다.
- 마을 변형은 `maeul_reach` 행을, 경기 변형은 `gg_reach` 행을 뺀다.
- 조정(후보 생성·배분)은 5일 평균 = 날짜 합 ÷ 날짜 수로 한다.
"""

from __future__ import annotations

import csv
import gzip
import math
from dataclasses import dataclass, field
from pathlib import Path

from busstrike.sim import io as sio
from busstrike.sim.arrivals import realize_bins
from busstrike.sim.models import DemandFlow, Scenario
from busstrike.sim.traveltime import TravelTimeTable

VARIANTS: dict[str, tuple[bool, bool]] = {   # 변형 → (마을 흡수 제외, 경기 흡수 제외)
    "affected": (False, False),
    "affected_maeul": (True, False),
    "affected_gg": (False, True),
    "affected_maeul_gg": (True, True),
}
HOURS = (6, 7, 8, 9)
KINDS = ("access", "egress")


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


@dataclass(frozen=True)
class Stop:
    stop_id: str
    name: str
    lat: float
    lon: float
    gu: str


@dataclass(frozen=True)
class Station:
    station_id: str
    name: str
    lat: float
    lon: float
    access_stop: str | None   # 역 지정 승강 정류장(status=ok만)
    egress_stop: str | None


@dataclass
class Network:
    tt: TravelTimeTable
    stops: dict[str, Stop]
    stations: dict[str, Station]

    def stop_dist_m(self, a: str, b: str) -> float:
        sa, sb = self.stops[a], self.stops[b]
        return haversine_m(sa.lat, sa.lon, sb.lat, sb.lon)

    def station_stop_dist_m(self, station_id: str, stop_id: str) -> float:
        k, s = self.stations[station_id], self.stops[stop_id]
        return haversine_m(k.lat, k.lon, s.lat, s.lon)

    def station_gu(self, station_id: str) -> str | None:
        k = self.stations[station_id]
        for sid in (k.access_stop, k.egress_stop):
            if sid and sid in self.stops:
                return self.stops[sid].gu
        return None


def _read_csv(path: Path) -> list[dict]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load_network(root: Path) -> Network:
    net = root / "network"
    tt = TravelTimeTable.from_dict(sio.read_json(net / "travel_times.json"))
    stops = {r["stop_id"]: Stop(r["stop_id"], r["stop_name"], float(r["stop_lat"]), float(r["stop_lon"]), r["gu"])
             for r in _read_csv(net / "stops.csv")}
    designated: dict[tuple[str, str], str] = {}
    for r in _read_csv(net / "station_stops.csv"):
        if r["status"] != "ok":          # too_far는 쓰지 않는다
            continue
        key = (r["station_id"], r["kind"])
        if key in designated and designated[key] != r["station_stop_id"]:
            raise ValueError(f"station {key} has two designated stops")
        designated[key] = r["station_stop_id"]
    stations = {}
    for r in _read_csv(net / "stations.csv"):
        sid = r["station_id"]
        stations[sid] = Station(sid, r["station_name"], float(r["stop_lat"]), float(r["stop_lon"]),
                                designated.get((sid, "access")), designated.get((sid, "egress")))
    return Network(tt, stops, stations)


@dataclass(frozen=True)
class FlowRow:
    date: str
    hour: int
    kind: str
    stop_id: str
    station_id: str
    station_stop_id: str
    flow: str          # 원문 문자열(정수화 대조용)
    maeul_reach: bool
    gg_reach: bool


def load_flows(root: Path) -> list[FlowRow]:
    """잔여 수요의 모집단: 도보 불가 + 지정 승강 정류장 있음."""
    out = []
    for r in _read_csv(root / "demand" / "demand_flows.csv.gz"):
        if r["walkable"] != "False" or not r["station_stop_id"]:
            continue
        out.append(FlowRow(r["date"], int(r["hour"]), r["kind"], r["stop_id"], r["station_id"],
                           r["station_stop_id"], r["flow"], r["maeul_reach"] == "True", r["gg_reach"] == "True"))
    return out


def variant_rows(rows: list[FlowRow], variant: str) -> list[FlowRow]:
    no_maeul, no_gg = VARIANTS[variant]
    return [r for r in rows if not (no_maeul and r.maeul_reach) and not (no_gg and r.gg_reach)]


@dataclass
class Demand:
    """한 변형의 5일 평균 잔여 수요.

    - `pair[(kind, stop_id, station_id)]` = 시간별 [6, 7, 8, 9시] 유량(역 단위, 허브·권역용)
    - `od[(origin_stop, dest_stop)]` = 시간별 유량(시뮬레이터가 보는 정류장 1개 → 1개 OD, 덮개·충족용)
      access는 stop → 역 access 지정 정류장, egress는 역 egress 지정 정류장 → stop.
    """
    variant: str
    dates: tuple[str, ...]
    pair: dict[tuple[str, str, str], list[float]] = field(default_factory=dict)
    od: dict[tuple[str, str], list[float]] = field(default_factory=dict)

    def station_total(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for (_, _, k), v in self.pair.items():
            out[k] = out.get(k, 0.0) + sum(v)
        return out

    def od_total(self, key: tuple[str, str]) -> float:
        v = self.od.get(key)
        return sum(v) if v else 0.0

    def total(self) -> float:
        return sum(sum(v) for v in self.od.values())


def build_demand(rows: list[FlowRow], variant: str) -> Demand:
    vr = variant_rows(rows, variant)
    dates = tuple(sorted({r.date for r in rows}))
    nd = len(dates)
    d = Demand(variant, dates)
    for r in vr:
        f = float(r.flow) / nd
        if f <= 0:
            continue
        h = HOURS.index(r.hour)
        d.pair.setdefault((r.kind, r.stop_id, r.station_id), [0.0] * 4)[h] += f
        key = (r.stop_id, r.station_stop_id) if r.kind == "access" else (r.station_stop_id, r.stop_id)
        d.od.setdefault(key, [0.0] * 4)[h] += f
    return d


def read_scenario(bundle_dir: Path, fleet: int) -> Scenario:
    return sio.scenario_from_dict(sio.read_json(bundle_dir / f"scenario_f{fleet}.json"))


def check_bundles(root: Path, rows: list[FlowRow], seed: int = 42, bin_s: int = 3600) -> list[dict]:
    """변형·날짜마다 잔여 수요를 시뮬레이터 정수화(`realize_bins`)에 넣어 묶음 demand_bins.csv와 대조한다.

    정수화 함수는 시뮬레이터 것을 그대로 부른다. 결과 행이 한 줄이라도 다르면 match=False.
    """
    out = []
    bundles = root / "bundles"
    for variant in VARIANTS:
        vr = variant_rows(rows, variant)
        for date in sorted({r.date for r in rows}):
            bdir = bundles / f"{variant}_{date}"
            scen = read_scenario(bdir, 11)
            flows = [DemandFlow(r.kind, r.stop_id, r.station_stop_id, r.hour * 3600, f"{float(r.flow):.6f}")
                     for r in vr if r.date == date and float(r.flow) > 0]
            mine = realize_bins(flows, scen.policy, scen.window, seed, bin_s)
            theirs = sio.read_demand_bins(bdir / "demand_bins.csv")
            out.append({
                "bundle": bdir.name, "variant": variant, "date": date,
                "flow_sum": round(sum(float(f.flow) for f in flows), 3),
                "passengers_mine": sum(b.passenger_count for b in mine),
                "passengers_bundle": sum(b.passenger_count for b in theirs),
                "rows_mine": len(mine), "rows_bundle": len(theirs),
                "match": tuple(mine) == tuple(theirs),
            })
    return out
