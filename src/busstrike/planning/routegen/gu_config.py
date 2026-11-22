"""구별 설정 파일(`gu_params.json`) 읽기: 공통 기본값(default) 위에 구마다 바꿀 값(gu.<slug>)만 덮어쓴다.

형식
{
  "default": {
    "generate": {...},            # 모든 생성 설정에 공통으로 넣을 Params 값(예: distance_basis)
    "configs": {"이름": {...}},    # 구별 실험(hub_sweep)에서 돌릴 생성 설정들(Params 값)
    "absorb": {...},              # 정밀 흡수 가정(AbsorbParams 값)
    "walk": {                     # 걷기 반영 안 만들기(walk_eval)
      "radius_m": 400, "multi": false, "service_starts": ["07:00", "06:00"],
      "generate": {...}           # 후보 풀을 만들 Params 값
      # 예전 "형평 먼저" 재현용(기본 경로는 쓰지 않음): "near_m", "near_discount", "lambdas", "targets"
    },
    "equity": {                   # 형평 지표·배분기 형평 하한(설계 2.2·6.2, 3단계)
      "far_m": 800, "floor": "muni", "route_min_per_vehicle": 90
    },
    "eval": {                     # 평가 규칙(설계 7절, reach.EvalRule 값) + 함께 낼 조합
      "station_mode": "same_line", "walk_radius_m": 400, "station_arrival": "candidates", "shared_stop": "flow_union",
      "transfer_complex": false, "walk_speed_m_min": 75, "walk_wait_limit_s": 1200,
      "combos": {"station_mode": ["fixed", "same_line", "any"], "walk_radius_m": [0, 200, 400]}
    }
  },
  "gu": {"guro": {"configs": {...}, "walk": {"near_discount": 0.5}}, ...}
}
- 사전(dict)은 키별로 겹쳐 쓰고, 목록·숫자는 통째로 바꾼다. configs는 구 쪽에 이름이 있으면 그 설정을 통째로 바꾼다.
- Params·AbsorbParams에 없는 키는 오류(오타를 조용히 넘기지 않는다).
"""

from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

from .absorb import AbsorbParams
from .generate import Params
from .reach import EvalRule

WALK_KEYS = {"radius_m", "near_m", "near_discount", "lambdas", "targets", "multi", "generate", "service_starts"}
EQUITY_KEYS = {"far_m", "floor", "route_min_per_vehicle"}
EQUITY_DEFAULT = {"far_m": 800, "floor": "muni", "route_min_per_vehicle": 90.0}


def _merge(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in b.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) and k != "configs" else v
    if "configs" in b and "configs" in a:
        out["configs"] = {**a["configs"], **b["configs"]}
    return out


def _check(kw: dict, cls, where: str) -> dict:
    names = {f.name for f in fields(cls)}
    bad = sorted(set(kw) - names)
    if bad:
        raise ValueError(f"{where}: {cls.__name__}에 없는 값 {bad}")
    return {k: tuple(v) if isinstance(v, list) else v for k, v in kw.items()}


class GuConfig:
    def __init__(self, raw: dict, gu: str, source: str = ""):
        self.gu, self.source = gu, source
        self.raw = _merge(raw.get("default", {}), raw.get("gu", {}).get(gu, {}))
        base = self.raw.get("generate", {})
        self.configs = {name: _check({**base, **kw}, Params, f"configs.{name}")
                        for name, kw in self.raw.get("configs", {}).items()}
        self.absorb = _check(self.raw.get("absorb", {}), AbsorbParams, "absorb")
        walk = {k: v for k, v in self.raw.get("walk", {}).items() if not k.startswith("_")}
        bad = sorted(set(walk) - WALK_KEYS)
        if bad:
            raise ValueError(f"walk: 모르는 값 {bad}")
        self.walk = {**walk, "generate": _check({**base, **walk.get("generate", {})}, Params, "walk.generate")}
        eqc = {k: v for k, v in self.raw.get("equity", {}).items() if not k.startswith("_")}
        bad = sorted(set(eqc) - EQUITY_KEYS)
        if bad:
            raise ValueError(f"equity: 모르는 값 {bad}")
        self.equity = {**EQUITY_DEFAULT, **eqc}
        fl = self.equity["floor"]
        if not (fl is None or fl == "muni" or (isinstance(fl, dict) and set(fl) <= {"total", "far"})):
            raise ValueError(f"equity.floor는 \"muni\", null, {{\"total\": x, \"far\": y}} 중 하나: {fl}")
        ev = {k: v for k, v in self.raw.get("eval", {}).items() if not k.startswith("_")}
        self.eval_combo_axes = ev.pop("combos", {"station_mode": ["fixed", "same_line", "any"],
                                                 "walk_radius_m": [0, 200, 400]})
        bad = sorted(set(self.eval_combo_axes) - {"station_mode", "walk_radius_m"})
        if bad:
            raise ValueError(f"eval.combos: 모르는 축 {bad}")
        self.eval = ev
        EvalRule.from_dict(ev)                       # 오타·잘못된 값을 여기서 막는다

    def eval_rule(self, **override) -> EvalRule:
        """평가·설계 기본 규칙(설정 파일 eval 절) 위에 override만 바꾼 규칙."""
        return EvalRule.from_dict({**self.eval, **override})

    def eval_combos(self) -> list[EvalRule]:
        """함께 낼 조합(기본: 목적 역 3 × 걷기 0/200/400m = 9개). 순서: 목적 역 → 걷기."""
        ax = self.eval_combo_axes
        modes = ax.get("station_mode") or [self.eval_rule().station_mode]
        radii = ax.get("walk_radius_m") or [self.eval_rule().walk_radius_m]
        return [self.eval_rule(station_mode=m, walk_radius_m=float(r)) for m in modes for r in radii]

    def params(self, name: str, **fixed) -> Params:
        return Params(**{**self.configs[name], **fixed})

    def walk_params(self, **fixed) -> Params:
        return Params(**{**self.walk["generate"], **fixed})

    def absorb_params(self) -> AbsorbParams:
        return AbsorbParams(**self.absorb)

    def record(self) -> dict:
        """결과 파일에 남길 실제 사용 값."""
        return {"source": self.source, "gu": self.gu, **self.raw}


def load(path: Path | str | None, gu: str) -> GuConfig:
    if path is None:
        return GuConfig({}, gu, "")
    p = Path(path)
    return GuConfig(json.loads(p.read_text(encoding="utf-8")), gu, str(p))
