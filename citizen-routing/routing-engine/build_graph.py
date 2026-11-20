"""Build an OTP graph with portable Java when available, otherwise Docker."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OTP = ROOT / "runtime" / "otp"
COMPOSE = ROOT / "routing-engine" / "docker-compose.yml"
JAR = ROOT / "runtime" / "downloads" / "otp-shaded-2.10.0.jar"


def java_command(*args: str) -> list[str] | None:
    runtimes = list((ROOT / "runtime" / "java").glob("*/bin/java.exe"))
    if not runtimes or not JAR.is_file():
        return None
    return [str(runtimes[0]), "-Xmx6g", "-jar", str(JAR), *args, str(OTP)]


def main() -> None:
    manifest = json.loads((OTP / "manifest.json").read_text(encoding="utf-8"))
    stamp = OTP / "graph-version.txt"
    stamp.unlink(missing_ok=True)
    graph = OTP / "graph.obj"
    before = graph.stat().st_mtime_ns if graph.exists() else 0
    command = java_command("--build", "--save") or [
        "docker", "compose", "-f", str(COMPOSE), "--profile", "build",
        "run", "--rm", "otp-build"
    ]
    subprocess.run(command, check=True)
    if not graph.is_file() or graph.stat().st_mtime_ns <= before:
        raise RuntimeError("OTP graph.obj was not rebuilt.")
    stamp.write_text(manifest["version"], encoding="utf-8")
    print("Graph ready for input version", manifest["version"])


if __name__ == "__main__":
    main()
