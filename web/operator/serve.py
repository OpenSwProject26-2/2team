"""저장소 루트에서 `py -3 web/operator/serve.py`로 실행하는 로컬 진입점."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from busstrike.api.d_dashboard import main

if __name__ == "__main__":
    main()
