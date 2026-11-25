"""실험에 쓰는 알고리즘 12종(docs/design/pipeline.md 3절): 후보 종류 3가지 × (호선 묶기, 소외 우선 삽입) 켬·끔.

- 후보 종류(base): sector = 섹터 순회(tour_sector), feeder = 피더 순회(tour_feeder),
  oneway_loop = 편도 노선 생성 후 순회(R1: gu_params_r1.json, 시작점 최적화 + 길이 상한 목록).
- 호선 묶기(line_group) = 같은 호선 역을 같은 목적지로 본다. 소외 우선 삽입(fill) = 역 800m 밖 수요에 가산점을 주며 정류장을 끼운다.
  두 옵션은 서로 독립이고 기본은 둘 다 끔.
- 12종 모두 배분기는 allocate.Model 하나(노선별 대수는 배분기가 정한다).
"""

from __future__ import annotations

from dataclasses import dataclass

# 소외 우선 삽입의 값(지금 G4 값 그대로: newalgo_1125·pareto_1125 run.py FILL)
FILL = {"fill": True, "fill_bonus": 1.0, "fill_top": 5, "fill_pool": 60}
LINE_GROUP = {"line_group": True}

BASES = ("sector", "feeder", "oneway_loop")
BASE_KO = {"sector": "섹터 순회", "feeder": "피더 순회", "oneway_loop": "편도 노선 생성 후 순회"}
# 이름에서 쓰는 짧은 앞부분(표 3절과 같은 이름)
_SHORT = {"sector": "섹터 순회", "feeder": "피더 순회", "oneway_loop": "편도 후 순회"}
# 실험 코드와의 대응(둘 다 끔만 정확히 같다. 나머지는 가장 가까운 예전 실험, 같지 않음)
EXP_CODES = {
    ("sector", 0, 0): "pareto_1125 A3 · tour_1124 T-sector",
    ("feeder", 0, 0): "pareto_1125 A2 · tour_1124 T-feeder(구로 T-only)",
    ("oneway_loop", 0, 0): "pareto_1125 A1 · improve_1122 R1(gen_imp)",
    ("feeder", 1, 1): "(가까운 것) newalgo F4 · pareto A4 — 그때는 배차 예산·tour_only가 같이 켜져 있어 같지 않음",
    ("sector", 1, 1): "(가까운 것) newalgo S4 · pareto A5 — 그때는 배차 예산과 피더 순회 풀이 같이 있어 같지 않음",
}


@dataclass(frozen=True)
class Algo:
    base: str
    line_group: bool
    fill: bool

    @property
    def id(self) -> str:
        return f"{self.base}_lg{int(self.line_group)}_fill{int(self.fill)}"

    @property
    def name(self) -> str:
        if not self.line_group and not self.fill:
            return BASE_KO[self.base]
        extra = "둘 다" if self.line_group and self.fill else ("호선" if self.line_group else "소외")
        return f"{_SHORT[self.base]} + {extra}"

    @property
    def exp_code(self) -> str:
        return EXP_CODES.get((self.base, int(self.line_group), int(self.fill)), "")

    def gen_overrides(self) -> dict:
        """walk_eval --gen JSON(생성 설정 덮어쓰기). 둘 다 끔이면 비어 있다(예전 명령 그대로)."""
        return {**(LINE_GROUP if self.line_group else {}), **(FILL if self.fill else {})}

    def walk_eval_args(self) -> list[str]:
        """후보 종류별 walk_eval 인자(공통 인자 뒤에 붙는다)."""
        if self.base == "oneway_loop":
            out: list[str] = []
        else:
            kind = "sector" if self.base == "sector" else "feeder"
            out = ["--tour", kind, "--tour-pool", f"tour_{kind}"]
        g = self.gen_overrides()
        if g:
            import json
            out += ["--gen", json.dumps(g, sort_keys=True)]
        return out


ALGOS: tuple[Algo, ...] = tuple(Algo(b, lg, fl) for b in BASES for lg, fl in ((False, False), (True, False),
                                                                                 (False, True), (True, True)))
BY_ID = {a.id: a for a in ALGOS}


def select(spec: str) -> list[Algo]:
    """'all' 또는 쉼표로 이은 ID(예: feeder_lg0_fill0,sector_lg1_fill1)."""
    if spec == "all":
        return list(ALGOS)
    out = []
    for x in filter(None, (s.strip() for s in spec.split(","))):
        if x not in BY_ID:
            raise ValueError(f"알 수 없는 알고리즘 {x!r}. 가능한 값: all, {', '.join(BY_ID)}")
        out.append(BY_ID[x])
    return out
