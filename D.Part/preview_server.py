"""D 합성 경로 화면을 localhost에서만 제공하는 개발 도구.

`/__demo__/...` 경로와 JSON 래퍼는 M08 공용 HTTP 계약이 아니다.
"""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from gtfs_preview import EXAMPLE, Preview, PreviewError, load_preview, search
from normal_reachability import assess_normal_reachability

PAGE = Path(__file__).with_name("preview.html")


def handler_for(
    preview: Preview | None, normal_folder: Path = EXAMPLE
) -> type[BaseHTTPRequestHandler]:
    class PreviewHandler(BaseHTTPRequestHandler):
        def _json(self, payload: dict[str, object]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            url = urlsplit(self.path)
            if url.path == "/":
                body = PAGE.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if url.path == "/__demo__/normal-reachability":
                params = parse_qs(url.query, keep_blank_values=True)
                required = (
                    "query_id",
                    "origin_stop_id",
                    "destination_stop_id",
                    "departure_at",
                )
                if any(len(params.get(name, [])) != 1 for name in required):
                    result = assess_normal_reachability("", "", "", "", normal_folder)
                else:
                    result = assess_normal_reachability(
                        params["query_id"][0],
                        params["origin_stop_id"][0],
                        params["destination_stop_id"][0],
                        params["departure_at"][0],
                        normal_folder,
                    )
                self._json({"preview_only": True, "result": result})
                return
            if url.path not in {"/__demo__/config", "/__demo__/search"}:
                self.send_error(404)
                return
            if preview is None:
                self._json(
                    {
                        "preview_only": True,
                        "ok": False,
                        "error": PreviewError(
                            "DATA_INCOMPLETE", "시나리오 예제 시간표가 없습니다."
                        ).as_dict(),
                    }
                )
                return
            if url.path == "/__demo__/config":
                self._json(
                    {
                        "preview_only": True,
                        "plan_id": preview.plan_id,
                        "revision": preview.revision,
                        "service_date": preview.service_date.isoformat(),
                        "network_snapshot_id": preview.network_snapshot_id,
                        "stops": sorted(preview.stops),
                        "source": preview.source,
                        "verification": preview.verification,
                    }
                )
                return
            params = parse_qs(url.query, keep_blank_values=True)
            try:
                required = (
                    "origin",
                    "destination",
                    "departure_at",
                    "plan_id",
                    "revision",
                )
                if any(len(params.get(name, [])) != 1 for name in required):
                    raise PreviewError(
                        "INPUT_INVALID", "검색 필드를 하나씩 입력하세요."
                    )
                try:
                    revision = int(params["revision"][0])
                except ValueError as error:
                    raise PreviewError(
                        "INPUT_INVALID", "revision은 정수여야 합니다."
                    ) from error
                result = search(
                    preview,
                    params["origin"][0],
                    params["destination"][0],
                    params["departure_at"][0],
                    params["plan_id"][0],
                    revision,
                )
            except PreviewError as error:
                self._json(
                    {"preview_only": True, "ok": False, "error": error.as_dict()}
                )
                return
            self._json({"preview_only": True, "ok": True, "result": result})

    return PreviewHandler


def main() -> None:
    parser = argparse.ArgumentParser(description="D 합성 경로 미리보기 서버")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    try:
        preview = load_preview()
    except PreviewError:
        preview = None
    with ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(preview)) as server:
        print(f"합성·미검증 개발 미리보기: http://127.0.0.1:{server.server_port}/")
        server.serve_forever()


if __name__ == "__main__":
    main()
