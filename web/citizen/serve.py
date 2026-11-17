"""저장소 루트에서 `py -3 web/citizen/serve.py`로 실행하는 시민 미리보기."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from busstrike.routing.preview_server import main

if __name__ == "__main__":
    main()
