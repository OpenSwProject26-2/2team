"""Run an A tool from the repository root without changing the working directory."""

import argparse
import runpy
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("demand_analysis", "candidate_survey", "extract_guro_draft", "clean_network"))
    # Parse only the command so each tool receives its own --help and options.
    args = parser.parse_args(sys.argv[1:2])
    sys.argv = [f"{sys.argv[0]} {args.command}", *sys.argv[2:]]
    runpy.run_module(f"data.{args.command}", run_name="__main__")


if __name__ == "__main__":
    main()
