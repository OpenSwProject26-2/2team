"""Run the prepared OTP graph with portable Java on localhost."""
from __future__ import annotations

import subprocess

from build_graph import java_command


def main() -> None:
    command = java_command("--load", "--serve")
    if not command:
        raise RuntimeError("Portable Java and OTP JAR are missing from runtime/.")
    subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
