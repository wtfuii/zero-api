"""Tests for the http-first login flow (auth/api.py) against the recorded
SPA flow shapes (docs/explorations/login.md, login.capture.json).
"""

import base64
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from zero_api.auth.api import http_login, http_refresh
from zero_api.auth.models import AuthSession
from zero_api.core.exceptions import AuthenticationError

# recorded refresh-response shape (raw.har, sanitized): no RefreshToken in it
_REFRESH_RESULT: dict[str, Any] = json.loads(
    (Path(__file__).parent / "fixtures" / "refresh_flow.json").read_text()
)["result"]


class _Recorder:
    """Mock server speaking the recorded login/refresh flow, remembering every call."""

    def __init__(self, fixture: dict[str, Any]) -> None:
        self.challenge = fixture["challenge"]
        self.result = fixture["result"]
        self.calls: list[tuple[str, dict[str, str], bytes]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.url.path, dict(request.headers), request.read()))
        path = request.url.path
        if path == "/apic/authc":
            target = request.headers["x-amz-target"].rsplit(".", 1)[-1]
            if target == "InitiateAuth" and json.loads(request.read() or b"{}").get("AuthFlow") == "REFRESH_TOKEN_AUTH":
                return httpx.Response(200, json=_REFRESH_RESULT)
            if target == "InitiateAuth":
                return httpx.Response(200, json=self.challenge)
            return httpx.Response(200, json=self.result)
        if path == "/api/auth/save":
            # the server stores the raw-token body as cookie __r / __i (login.md)
            token = request.read().decode()
            name = "__r" if token == self.result["AuthenticationResult"]["RefreshToken"] else "__i"
            return httpx.Response(200, content=b"", headers={"set-cookie": f"{name}={token}; Path=/; HttpOnly"})
        raise AssertionError(f"unexpected path: {path}")


def _login(recorder: _Recorder) -> httpx.Client:
    return httpx.Client(
        base_url="https://mein.finanzen-zero.net", transport=httpx.MockTransport(recorder.handle)
    )


class TestHttpLoginFlow:
    def test_mimics_recorded_call_order(self, load_fixture: Callable[[str, str], Any]) -> None:
        recorder = _Recorder(load_fixture("auth", "login_flow"))
        session = http_login(_login(recorder), "probe@example.com", "pw")

        assert [path for path, _h, _b in recorder.calls] == [
            "/apic/authc",
            "/apic/authc",
            "/api/auth/save",
            "/api/auth/save",
        ]

        _p, init_headers, init_body = recorder.calls[0]
        assert init_headers["x-amz-target"] == "AWSCognitoIdentityProviderService.InitiateAuth"
        assert init_headers["content-type"] == "application/x-amz-json-1.1"
        init_payload = json.loads(init_body)
        assert init_payload["AuthFlow"] == "USER_SRP_AUTH"
        assert init_payload["AuthParameters"]["USERNAME"] == "probe@example.com"
        assert 767 <= len(init_payload["AuthParameters"]["SRP_A"]) <= 768  # 3072-bit SRP_A

        _p, resp_headers, resp_body = recorder.calls[1]
        assert resp_headers["x-amz-target"] == "AWSCognitoIdentityProviderService.RespondToAuthChallenge"
        responses = json.loads(resp_body)["ChallengeResponses"]
        challenge = recorder.challenge["ChallengeParameters"]
        assert responses["USERNAME"] == challenge["USER_ID_FOR_SRP"]
        assert responses["PASSWORD_CLAIM_SECRET_BLOCK"] == challenge["SECRET_BLOCK"]
        assert len(base64.b64decode(responses["PASSWORD_CLAIM_SIGNATURE"])) == 32

        # token saves: refresh token first, then id token (recorded order)
        tokens = [body.decode() for _p, _h, body in recorder.calls[2:]]
        assert tokens == [
            recorder.result["AuthenticationResult"]["RefreshToken"],
            recorder.result["AuthenticationResult"]["IdToken"],
        ]
        for path, headers, _b in recorder.calls[2:]:
            assert headers["content-type"] == "text/plain"
            assert headers["x-xsrf-token"]  # self-issued XSRF token
        xsrf = recorder.calls[2][1]["x-xsrf-token"]
        assert recorder.calls[3][1]["x-xsrf-token"] == xsrf  # same token on both saves

        assert session.customer_id == "4917975"  # from the IdToken prm claim
        assert session.cookies["__r"] == recorder.result["AuthenticationResult"]["RefreshToken"]
        assert session.cookies["__i"] == recorder.result["AuthenticationResult"]["IdToken"]
        assert session.cookies["XSRF-TOKEN"]  # self-issued


class TestLoudFailures:
    def test_cognito_error_body_surfaces_message(self) -> None:
        def handle(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400, json={"__type": "NotAuthorizedException", "message": "Incorrect username or password."}
            )

        http = httpx.Client(base_url="https://mein.finanzen-zero.net", transport=httpx.MockTransport(handle))
        with pytest.raises(AuthenticationError, match="Incorrect username or password"):
            http_login(http, "probe@example.com", "wrong")

    def test_waf_challenge_page_is_loud(self) -> None:
        # waf_probe.md open question 1: WAF enforcement could be adaptive —
        # a sudden challenge must fail loudly, not parse as a login.
        def handle(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, text="<html>AWSCaptcha challenge</html>", headers={"x-amzn-waf-action": "captcha"})

        http = httpx.Client(base_url="https://mein.finanzen-zero.net", transport=httpx.MockTransport(handle))
        with pytest.raises(AuthenticationError, match="WAF"):
            http_login(http, "probe@example.com", "pw")


class TestHttpRefresh:
    def test_mimics_recorded_refresh_flow(self, load_fixture: Callable[[str, str], Any]) -> None:
        recorder = _Recorder(load_fixture("auth", "login_flow"))
        client = _login(recorder)
        session = http_login(client, "probe@example.com", "pw")  # establishes __r/__i

        recorder.calls.clear()
        refreshed = http_refresh(client, session)

        assert [path for path, _h, _b in recorder.calls] == ["/apic/authc", "/api/auth/save"]
        assert recorder.calls[0][1]["x-amz-target"] == "AWSCognitoIdentityProviderService.InitiateAuth"
        payload = json.loads(recorder.calls[0][2])
        assert payload["AuthFlow"] == "REFRESH_TOKEN_AUTH"
        assert payload["AuthParameters"]["REFRESH_TOKEN"] == recorder.result["AuthenticationResult"]["RefreshToken"]
        assert "ClientMetadata" not in payload  # recorded request carries none
        # only the new IdToken is re-saved (no rotation — __r stays valid)
        assert recorder.calls[1][2].decode() == _REFRESH_RESULT["AuthenticationResult"]["IdToken"]
        assert refreshed.cookies["__i"] == _REFRESH_RESULT["AuthenticationResult"]["IdToken"]
        assert refreshed.cookies["__r"] == recorder.result["AuthenticationResult"]["RefreshToken"]
        assert refreshed.customer_id == "4917975"

    def test_no_refresh_token_raises(self) -> None:
        http = httpx.Client(base_url="https://mein.finanzen-zero.net")
        with pytest.raises(AuthenticationError, match="__r"):
            http_refresh(http, AuthSession(cookies={"__i": "t"}, customer_id="1"))
