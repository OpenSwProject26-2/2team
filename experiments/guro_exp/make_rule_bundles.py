"""실행 묶음 사본을 만들고 scenario의 policy에 `service_end_rule` 필드 하나만 더한다.

    python3 experiments/guro_exp/make_rule_bundles.py --bundles <DATA>/bundles \
        --rule start_within_window_return_by_available --out <OUT>/bundles_new/bundles

- 원본 묶음 파일은 읽기만 한다.
- 사본의 scenario_f{F}.json = 원본 + `policy.service_end_rule`. 나머지 필드·값은 그대로다.
- 사본의 run_inputs_f{F}.json은 `scenario_sha256`과 `checksum`만 새 scenario에 맞춰 다시 계산한다
  (run.py가 둘을 실제 파일과 대조하기 때문이다). `input_bundle_id`·seed·수요·구간시간은 그대로다.
- demand_bins.csv·travel_times.json은 바이트 그대로 복사한다.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from busstrike.sim import io  # noqa: E402
from busstrike.sim.arrivals import input_checksum  # noqa: E402
from busstrike.sim.models import SERVICE_END_RULES  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bundles", required=True, type=Path)
    ap.add_argument("--rule", required=True, choices=SERVICE_END_RULES)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    src_root, out_root = args.bundles.resolve(), args.out.resolve()
    if out_root == src_root or src_root in out_root.parents:
        sys.exit("--out은 원본 묶음 폴더 밖이어야 한다")
    out_root.mkdir(parents=True, exist_ok=True)
    made = 0
    for b in sorted(p for p in src_root.iterdir() if p.is_dir()):
        d = out_root / b.name
        if d.exists():
            shutil.rmtree(d)
        d.mkdir()
        for name in ("demand_bins.csv", "travel_times.json"):
            shutil.copyfile(b / name, d / name)
        for scen_path in sorted(b.glob("scenario_f*.json")):
            fleet = scen_path.stem.split("_f")[1]
            scen = json.loads(scen_path.read_text(encoding="utf-8"))
            if "service_end_rule" in scen["policy"]:
                sys.exit(f"원본에 이미 service_end_rule이 있다: {scen_path}")
            scen["policy"]["service_end_rule"] = args.rule
            scen_bytes = io.json_bytes(scen)
            (d / scen_path.name).write_bytes(scen_bytes)
            io.scenario_from_dict(scen)                           # 스키마 검사

            ri_path = b / f"run_inputs_f{fleet}.json"
            ri = json.loads(ri_path.read_text(encoding="utf-8"))
            ri["scenario_sha256"] = io.sha256_bytes(scen_bytes)
            ri["checksum"] = input_checksum(ri["arrival_meta"]["sha256"], ri["seed"], ri["travel_time_sha256"],
                                            ri["scenario_sha256"])
            (d / ri_path.name).write_bytes(io.json_bytes(ri))
            made += 1
    print(f"{made}개 scenario 사본 → {out_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
