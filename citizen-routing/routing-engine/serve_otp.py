"""Run one prepared OTP graph with portable Java on localhost."""
from __future__ import annotations

import argparse
import subprocess

from build_graph import graph_dir, java_command


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("normal", "strike"), default="normal")
    args = parser.parse_args()
    port = "8080" if args.mode == "normal" else "8081"
    command = java_command(graph_dir(args.mode), "--load", "--serve", "--port", port)
    if not command:
        raise RuntimeError("Portable Java and OTP JAR are missing from runtime/. Use Docker Compose.")
    subprocess.run(command, check=True)


if __name__ == "__main__":
    main()