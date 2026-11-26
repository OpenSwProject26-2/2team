"""Fetch official public transit API responses into a local, untracked directory.

This only acquires source responses. It does not infer an A/C route or stop match.
Keep API keys in environment variables; never pass them on the command line.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import urlopen


def fetch(url: str) -> bytes:
    try:
        with urlopen(url, timeout=30) as response:
            body = response.read(20_000_001)
            if len(body) > 20_000_000:
                raise ValueError("API response exceeds 20 MB limit")
            return body
    except HTTPError as error:
        raise RuntimeError(f"Official API returned HTTP {error.code}; check service access and quota") from None
    except URLError:
        raise RuntimeError("Official API could not be reached; check network access") from None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", choices=["gyeonggi", "metro_monthly"])
    parser.add_argument("--route-id", help="Official GBIS routeId, not an A GTFS ID")
    parser.add_argument("--month", help="YYYYMM for CardSubwayTime")
    parser.add_argument("--start", type=int, default=1, help="Metro API row start, inclusive")
    parser.add_argument("--end", type=int, default=1000, help="Metro API row end, inclusive")
    parser.add_argument("--out", type=Path, required=True, help="Local directory outside Git-tracked data")
    parser.add_argument("--key-stdin", action="store_true", help="Read the API key from standard input without echoing it")
    args = parser.parse_args()
    if args.source == "gyeonggi":
        key = sys.stdin.readline().strip() if args.key_stdin else os.environ.get("DATA_GO_KR_SERVICE_KEY")
        if not key:
            parser.error("Set DATA_GO_KR_SERVICE_KEY after applying for dataset 15080662 access")
        if not args.route_id or not args.route_id.isdecimal():
            parser.error("--route-id must be an official numeric GBIS routeId")
        base = "https://apis.data.go.kr/6410000/busrouteservice/v2/"
        endpoints = {
            "route": "getBusRouteInfoItemv2",
            "stops": "getBusRouteStationListv2",
            "shape": "getBusRouteLineListv2",
        }
        urls = {name: base + method + "?" + urlencode({"serviceKey": key, "routeId": args.route_id, "format": "json"})
                for name, method in endpoints.items()}
        source_url = "https://www.data.go.kr/data/15080662/openapi.do"
        prefix = "gbis_" + args.route_id
    else:
        key = os.environ.get("SEOUL_OPEN_DATA_KEY")
        if not key:
            parser.error("Set SEOUL_OPEN_DATA_KEY after obtaining a Seoul Open Data Plaza key")
        if not args.month or len(args.month) != 6 or not args.month.isdecimal():
            parser.error("--month must be YYYYMM")
        if not 1 <= args.start <= args.end <= args.start + 999:
            parser.error("Use a page of at most 1000 positive row numbers")
        urls = {"rows": "https://openapi.seoul.go.kr:8088/" + quote(key, safe="")
                + f"/json/CardSubwayTime/{args.start}/{args.end}/{args.month}"}
        source_url = "https://data.seoul.go.kr/dataList/OA-12252/S/1/datasetView.do"
        prefix = f"metro_{args.month}_{args.start}_{args.end}"
    responses = {name: fetch(url) for name, url in urls.items()}
    for name, body in responses.items():
        parsed = json.loads(body)
        if (not isinstance(parsed, dict) or "OpenAPI_ServiceResponse" in parsed
                or ("RESULT" in parsed and parsed["RESULT"].get("CODE") != "INFO-000")):
            raise RuntimeError("Official API returned an unexpected or error payload")
    args.out.mkdir(parents=True, exist_ok=True)
    manifest = {"source_url": source_url, "retrieved_utc": datetime.now(timezone.utc).isoformat(),
                "source": args.source, "route_id": args.route_id if args.source == "gyeonggi" else None,
                "month": args.month if args.source == "metro_monthly" else None,
                "files": {}}
    for name, body in responses.items():
        path = args.out / f"{prefix}_{name}.json"
        path.write_bytes(body)
        manifest["files"][path.name] = {"sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}
    (args.out / f"{prefix}_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"status": "downloaded", "source": args.source, "files": list(manifest["files"])}, sort_keys=True))


if __name__ == "__main__":
    main()
