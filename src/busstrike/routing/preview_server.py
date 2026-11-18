"""D 합성 경로 화면을 localhost에서만 제공하는 개발 도구.

`/__demo__/...` 경로와 JSON 래퍼는 M08 공용 HTTP 계약이 아니다.
"""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .gtfs_preview import EXAMPLE, Preview, PreviewError, load_preview, search
from .normal_reachability import assess_normal_reachability

ROOT = Path(__file__).resolve().parents[3]
PAGE = ROOT / "web" / "citizen" / "index.html"
SELECTION = ROOT / "data" / "d_selection.json"


def _selected(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("selected")
    except FileNotFoundError:
        return None


def handler_for(
    preview: Preview | None,
    normal_folder: Path = EXAMPLE,
    selection_file: Path = SELECTION,
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
            if url.path == "/__demo__/selection":
                selected = _selected(selection_file)
                self._json({"selected": selected, "timetable_connected": False})
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
                journey_state = params.get("journey_state", ["strike_demo"])[0]
                if journey_state == "strike_selected":
                    selected = _selected(selection_file)
                    if selected is None:
                        raise PreviewError(
                            "DATA_INCOMPLETE", "운영자가 선택한 실제 운행안이 없습니다."
                        )
                    if params.get("plan_id", [None])[0] != selected.get(
                        "plan_id"
                    ) or params.get("revision", [None])[0] != str(
                        selected.get("revision")
                    ):
                        raise PreviewError(
                            "DATA_VERSION_MISMATCH",
                            "선택 운행안 ID 또는 revision이 다릅니다.",
                        )
                    raise PreviewError(
                        "DATA_INCOMPLETE",
                        "선택 운행안의 실제 시간표와 A/C 정류장 ID 매핑이 없어 경로를 연결할 수 없습니다.",
                    )
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
                    journey_state=journey_state,
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
