"""Run the selected-route OSRM preprocessor from the repository root."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from busstrike.api.d_bus_routing import main

if __name__ == "__main__":
    main()
