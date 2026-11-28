"""config/local_paths.toml 읽기(커밋 안 함). 예시는 config/local_paths.example.toml."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = REPO / "config" / "local_paths.toml"


@dataclass(frozen=True)
class LocalPaths:
    work_root: Path | None = None
    transit_data: Path | None = None
    pareto_root: Path | None = None
    confirm_root: Path | None = None
    precomputed_root: Path | None = None
    sweep: str = ""                   # {gu} 자리표시 포함
    muni: str = ""
    citizen_api: str = ""
    citizen_ui: str = ""
    par: int = 3
    eval_groups: int = 3
    source: str = ""                  # 읽은 설정 파일(없으면 빈 문자열)
    extra: dict = field(default_factory=dict)

    def sweep_dir(self, gu: str) -> Path | None:
        return Path(self.sweep.format(gu=gu)) if self.sweep else None

    def muni_dir(self, gu: str) -> Path | None:
        return Path(self.muni.format(gu=gu)) if self.muni else None


def _p(v: str | None) -> Path | None:
    return Path(v).expanduser() if v else None


def load_paths(path: str | Path | None = None) -> LocalPaths:
    """설정 파일이 없으면 모든 경로가 빈 LocalPaths(화면에는 "연결 안 됨"으로 보인다)."""
    f = Path(path) if path else DEFAULT_CONFIG
    if not f.is_file():
        return LocalPaths()
    raw = tomllib.loads(f.read_text(encoding="utf-8"))
    p, c, r = raw.get("paths", {}), raw.get("citizen", {}), raw.get("run", {})
    return LocalPaths(
        work_root=_p(p.get("work_root")), transit_data=_p(p.get("transit_data")),
        pareto_root=_p(p.get("pareto_root")), confirm_root=_p(p.get("confirm_root")),
        precomputed_root=_p(p.get("precomputed_root")), sweep=p.get("sweep", ""), muni=p.get("muni", ""),
        citizen_api=c.get("api", "").rstrip("/"), citizen_ui=c.get("ui", ""),
        par=min(3, int(r.get("par", 3))), eval_groups=min(3, int(r.get("eval_groups", 3))),
        source=str(f), extra={k: v for k, v in raw.items() if k not in ("paths", "citizen", "run")})
