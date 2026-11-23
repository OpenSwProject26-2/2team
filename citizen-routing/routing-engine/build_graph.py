"""Build one of the independent OTP graphs with portable Java or Docker."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "routing-engine" / "docker-compose.yml"
JAR = ROOT / "runtime" / "downloads" / "otp-shaded-2.10.0.jar"


def graph_dir(mode: str) -> Path:
    return ROOT / "runtime" / ("otp" if mode == "normal" else "otp-strike")


def java_command(otp_dir: Path, *args: str) -> list[str] | None:
    runtimes = list((ROOT / "runtime" / "java").glob("*/bin/java.exe"))
    if not runtimes or not JAR.is_file():
        return None
    return [str(runtimes[0]), "-Xmx6g", "-jar", str(JAR), *args, str(otp_dir)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("normal", "strike"), default="normal")
    args = parser.parse_args()
    otp_dir = graph_dir(args.mode)
    manifest = json.loads((otp_dir / "manifest.json").read_text(encoding="utf-8"))
    stamp = otp_dir / "graph-version.txt"
    stamp.unlink(missing_ok=True)
    graph = otp_dir / "graph.obj"
    before = graph.stat().st_mtime_ns if graph.exists() else 0
    command = java_command(otp_dir, "--build", "--save")
    if command is None:
        service = "otp-build" if args.mode == "normal" else "otp-strike-build"
        profile = "build" if args.mode == "normal" else "strike-build"
        command = ["docker", "compose", "-f", str(COMPOSE), "--profile", profile,
                   "run", "--rm", service]
    subprocess.run(command, check=True)
    if not graph.is_file() or graph.stat().st_mtime_ns <= before:
        raise RuntimeError("OTP graph.obj was not rebuilt.")
    stamp.write_text(manifest["version"], encoding="utf-8")
    print("Graph ready for", args.mode, "input version", manifest["version"])


if __name__ == "__main__":
    main()