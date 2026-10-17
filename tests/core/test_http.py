"""Tests for the shared HTTP transport (recorded-traffic style, no live calls)."""

import base64

import httpx

from zero_api import ZeroClient
from zero_api.auth.models import AuthSession

# release string as returned (base64) by the captured /version.info
_RELEASE = "D2026-09_002-hf07_1791897579715_c67ab8e588112e22c8c5f8c31cd361119d7c38c2"


def _client(handle: httpx.MockTransport) -> ZeroClient:
    return ZeroClient(
        email="x@example.com",
        password="secret",
        login=lambda: AuthSession(cookies={"__i": "fake-token"}, customer_id="1234567"),
        http=httpx.Client(base_url="https://mein.finanzen-zero.net", transport=handle),
    )


class TestFzBuildHeader:
    def test_fetched_from_version_info_and_cached(self) -> None:
        seen: dict[str, str | int] = {}

        def handle(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/version.info":
                return httpx.Response(200, text=base64.b64encode((_RELEASE + "\n").encode()).decode())
            seen["build"] = request.headers.get("x-fz-build", "")
            api_calls = seen.get("api_calls")
            seen["api_calls"] = (api_calls if isinstance(api_calls, int) else 0) + 1
            return httpx.Response(200, content=b"{}")

        client = _client(httpx.MockTransport(handle))
        client._http.get_json("/api/whatever")
        client._http.get_json("/api/whatever")
        assert seen["build"] == "c67ab8e588112e22c8c5f8c31cd361119d7c38c2"
        assert seen["api_calls"] == 2  # version.info fetched once, header cached

    def test_broken_version_info_sends_no_header(self) -> None:
        seen: dict[str, str] = {}

        def handle(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/version.info":
                return httpx.Response(200, text="not-base64-release-shape!")
            seen["build"] = request.headers.get("x-fz-build", "absent")
            return httpx.Response(200, content=b"{}")

        client = _client(httpx.MockTransport(handle))
        client._http.get_json("/api/whatever")
        assert seen["build"] == "absent"
