"""구별 설정 파일(`gu_params.json`) 읽기: 공통 기본값(default) 위에 구마다 바꿀 값(gu.<slug>)만 덮어쓴다.

형식
{
  "default": {
    "generate": {...},            # 모든 생성 설정에 공통으로 넣을 Params 값(예: distance_basis)
    "configs": {"이름": {...}},    # 구별 실험(hub_sweep)에서 돌릴 생성 설정들(Params 값)
    "absorb": {...},              # 정밀 흡수 가정(AbsorbParams 값)
    "walk": {                     # 걷기·형평 실험(walk_eval)
      "radius_m": 400, "near_m": 800, "near_discount": 0.3, "lambdas": [3], "targets": [0.7], "multi": true,
      "generate": {...}           # 후보 풀을 만들 Params 값
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

WALK_KEYS = {"radius_m", "near_m", "near_discount", "lambdas", "targets", "multi", "generate"}


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
        walk = self.raw.get("walk", {})
        bad = sorted(set(walk) - WALK_KEYS)
        if bad:
            raise ValueError(f"walk: 모르는 값 {bad}")
        self.walk = {**walk, "generate": _check({**base, **walk.get("generate", {})}, Params, "walk.generate")}

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
