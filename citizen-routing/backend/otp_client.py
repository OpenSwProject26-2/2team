"""Small OTP 2.8.1 GTFS GraphQL client."""
from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

QUERY = """
query CitizenPlan($origin: PlanLabeledLocationInput!, $destination: PlanLabeledLocationInput!,
                   $dateTime: PlanDateTimeInput!, $first: Int,
                   $preferences: PlanPreferencesInput) {
  planConnection(origin: $origin, destination: $destination,
                 dateTime: $dateTime, first: $first, preferences: $preferences) {
    routingErrors { code description }
    edges {
      node {
        start end duration walkTime waitingTime numberOfTransfers
        legs {
          mode duration distance transitLeg
          start { scheduledTime } end { scheduledTime }
          from { name lat lon stop { gtfsId } }
          to { name lat lon stop { gtfsId } }
          route { gtfsId shortName longName mode }
          legGeometry { points }
          stopCalls { stopLocation { ... on Stop { lat lon } } }
        }
      }
    }
  }
}
"""


class OTPError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def query(endpoint: str, variables: dict) -> dict:
    request = Request(endpoint, json.dumps({"query": QUERY, "variables": variables}).encode("utf-8"),
                      {"Content-Type": "application/json", "Accept": "application/json"},
                      method="POST")
    try:
        with urlopen(request, timeout=25) as response:
            result = json.load(response)
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise OTPError("OTP_UNAVAILABLE", "OTP 서버에 연결할 수 없습니다. 그래프와 서버 상태를 확인하세요.") from error
    except (ValueError, UnicodeError) as error:
        raise OTPError("OTP_RESPONSE_INVALID", "OTP 응답을 해석할 수 없습니다.") from error
    if result.get("errors"):
        detail = "; ".join(e.get("message", "GraphQL 오류") for e in result["errors"][:3])
        raise OTPError("OTP_QUERY_FAILED", detail)
    plan = result.get("data", {}).get("planConnection")
    if not isinstance(plan, dict):
        raise OTPError("OTP_RESPONSE_INVALID", "OTP 경로 응답이 없습니다.")
    if plan.get("routingErrors") and not plan.get("edges"):
        details = "; ".join(e.get("description") or e.get("code", "경로 오류")
                            for e in plan["routingErrors"][:3])
        raise OTPError("NO_PATH", details)
    return plan


def healthy(endpoint: str) -> bool:
    request = Request(endpoint, b'{"query":"{ __typename }"}',
                      {"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=3) as response:
            result = json.load(response)
        return not result.get("errors") and bool(result.get("data"))
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        return False
