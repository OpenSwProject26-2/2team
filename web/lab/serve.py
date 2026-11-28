"""노선 실험실 충족률·형평성 곡선 화면을 띄우는 정적 서버(계산 없음).

    python3 web/lab/serve.py --run <파이프라인 --out 폴더> [--port 8795]

- /            → web/lab/index.html
- /lab/<파일>   → web/lab/ (pareto_panel.js·css, plan_layer.js)
- /vendor/<파일> → web/operator/vendor/ (MapLibre, 지도 스타일; 읽기만)
- /run/pareto.json, /run/export/<plan id>.json → --run 폴더(이 둘만)
"""
from __future__ import annotations

import argparse
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

HERE = Path(__file__).resolve().parent
VENDOR = HERE.parent / "operator" / "vendor"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8"}
LAB_FILES = {"pareto_panel.js", "pareto_panel.css", "plan_layer.js"}
EXPORT = re.compile(r"^/run/export/([A-Za-z0-9_.+\-]+\.json)$")


def resolve(path: str, run: Path) -> Path | None:
    if path in {"/", "/index.html"}:
        return HERE / "index.html"
    if path.startswith("/lab/") and path[5:] in LAB_FILES:
        return HERE / path[5:]
    if path.startswith("/vendor/"):
        name = path[8:]
        return VENDOR / name if "/" not in name and (VENDOR / name).is_file() else None
    if path == "/run/pareto.json":
        return run / "pareto.json"
    m = EXPORT.match(path)
    if m and ".." not in m.group(1):
        return run / "export" / m.group(1)
    return None


def make_handler(run: Path):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            file = resolve(unquote(urlsplit(self.path).path), run)
            if file is None or not file.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            body = file.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", TYPES.get(file.suffix, "application/octet-stream"))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            pass
    return Handler


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", required=True, type=Path, help="pareto.json과 export/가 있는 파이프라인 출력 폴더")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8795)
    args = ap.parse_args()
    run = args.run.resolve()
    if not (run / "pareto.json").is_file():
        raise SystemExit(f"{run}/pareto.json 없음")
    print(f"충족률·형평성 곡선: http://{args.host}:{args.port}/  ({run})", flush=True)
    ThreadingHTTPServer((args.host, args.port), make_handler(run)).serve_forever()


if __name__ == "__main__":
    main()
