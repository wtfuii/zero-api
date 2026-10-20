"""Tests for the shared HTTP transport (recorded-traffic style, no live calls)."""

import base64
import json

import httpx

from zero_api import ZeroClient
from zero_api.auth.models import AuthSession
from zero_api.core.http import AppHttp

# release string as returned (base64) by the captured /version.info
_RELEASE = "D2026-09_002-hf07_1791897579715_c67ab8e588112e22c8c5f8c31cd361119d7c38c2"


def _jwt(exp: float) -> str:
    return _jwt_claims({"exp": exp})


def _jwt_claims(claims: dict[str, object]) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"header.{payload}.signature"


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


class TestAutoRefresh:
    """Proactive token refresh: expired __i → REFRESH_TOKEN_AUTH, not full re-login."""

    def test_expired_id_token_refreshes_instead_of_full_login(self) -> None:
        import time

        calls: list[str] = []
        full_logins: list[int] = []
        new_id_token = _jwt_claims({"prm": json.dumps({"1": {}}), "exp": time.time() + 3600})

        def full_login() -> AuthSession:
            full_logins.append(1)
            raise AssertionError("full login must not run when a refresh succeeds")

        def handle(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            calls.append(path)
            if path == "/apic/authc":
                assert json.loads(request.read())["AuthFlow"] == "REFRESH_TOKEN_AUTH"
                return httpx.Response(
                    200,
                    json={"AuthenticationResult": {"AccessToken": "a", "ExpiresIn": 3600, "IdToken": new_id_token}},
                )
            if path == "/api/auth/save":
                return httpx.Response(200, content=b"", headers={"set-cookie": "__i=" + new_id_token + "; Path=/"})
            return httpx.Response(200, content=b"{}")

        http = httpx.Client(base_url="https://mein.finanzen-zero.net", transport=httpx.MockTransport(handle))
        app = AppHttp(http=http, relogin=full_login)
        app.apply_session(
            AuthSession(
                cookies={"__i": _jwt(time.time() - 10), "__r": "r-token", "XSRF-TOKEN": "xsrf"},
                customer_id="1",
            )
        )
        app.get_json("/api/whatever")
        assert full_logins == []  # refresh flow used instead
        assert calls == ["/apic/authc", "/api/auth/save", "/version.info", "/api/whatever"]
        assert app._session is not None and app._session.cookies["__i"] == new_id_token

    def test_dead_refresh_token_falls_back_to_full_login(self) -> None:
        import time

        full_logins: list[int] = []

        def full_login() -> AuthSession:
            full_logins.append(1)
            return AuthSession(cookies={"__i": "fresh-token"}, customer_id="1")

        def handle(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == "/apic/authc":
                return httpx.Response(400, json={"__type": "NotAuthorizedException", "message": "Refresh Token has expired"})
            return httpx.Response(200, content=b"{}")

        http = httpx.Client(base_url="https://mein.finanzen-zero.net", transport=httpx.MockTransport(handle))
        app = AppHttp(http=http, relogin=full_login)
        app.apply_session(
            AuthSession(
                cookies={"__i": _jwt(time.time() - 10), "__r": "dead-token", "XSRF-TOKEN": "xsrf"},
                customer_id="1",
            )
        )
        app.get_json("/api/whatever")
        assert full_logins == [1]  # refresh failed → full SRP login took over
