"""CLI to materialize independent, ignored OTP inputs."""
import argparse
from pathlib import Path

from .gtfs_adapter import prepare


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--existing", type=Path, required=True, help="A release ZIP, nested ZIP, or GTFS folder")
    parser.add_argument("--shuttle", type=Path, help="B/C exported GTFS ZIP or folder with stops.txt")
    parser.add_argument("--strike-routes", type=Path, help="JSON with affected_route_ids")
    parser.add_argument("--osm", type=Path, required=True, help="regional .osm.pbf")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "runtime" / "otp")
    args = parser.parse_args()
    print(prepare(args.existing, args.shuttle, args.strike_routes, args.osm, args.output))


if __name__ == "__main__":
    main()
