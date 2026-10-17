"""Tests for the account slice, run against recorded traffic (no live calls)."""

import json
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import httpx
import pytest

from zero_api import ZeroClient
from zero_api.auth.models import AuthSession

_SESSION = AuthSession(cookies={"__i": "fake-token", "XSRF-TOKEN": "fake-xsrf"}, customer_id="1234567")


@pytest.fixture
def body(load_fixture: Callable[[str, str], Any]) -> bytes:
    return json.dumps(load_fixture("account", "moneytransaction_account")).encode()


def _transport(body: bytes, login_calls: list[int]) -> httpx.MockTransport:
    """Rejects with 403 while the request carries no authenticated cookie."""

    def handle(request: httpx.Request) -> httpx.Response:
        if "__i=fake-token" not in request.headers.get("cookie", ""):
            return httpx.Response(403)
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    return httpx.MockTransport(handle)


def _client(body: bytes, login_calls: list[int]) -> ZeroClient:
    """ZeroClient whose fake login counts each (re-)authentication."""

    def fake_login() -> AuthSession:
        login_calls.append(1)
        return _SESSION

    return ZeroClient(
        email="x@example.com",
        password="secret",
        login=fake_login,
        http=httpx.Client(base_url="https://mein.finanzen-zero.net", transport=_transport(body, login_calls)),
    )


class TestGetAccount:
    def test_logs_in_once_and_parses_recorded_payload(self, body: bytes) -> None:
        login_calls: list[int] = []
        client = _client(body, login_calls)
        account = client.account.get()
        assert login_calls == [1]  # auto-logged-in exactly once, then cached
        assert account.customer_id == "1234567"
        assert account.account_nr == "1234567007"
        assert account.buying_power == Decimal("20.13")
        assert account.debit_interest == Decimal("5.635")
        assert account.credit_amount is None
        assert account.overdrawn is False

    def test_reuses_cached_session(self, body: bytes) -> None:
        login_calls: list[int] = []
        client = _client(body, login_calls)
        client.account.get()
        client.account.get()
        assert login_calls == [1]  # second call does not re-authenticate

    def test_sends_customer_id_and_xsrf(self, body: bytes) -> None:
        seen: dict[str, str] = {}

        def spy_handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["xsrf"] = request.headers.get("x-xsrf-token", "")
            return httpx.Response(200, content=body)

        client = ZeroClient(
            email="x@example.com",
            password="secret",
            login=lambda: _SESSION,
            http=httpx.Client(base_url="https://mein.finanzen-zero.net", transport=httpx.MockTransport(spy_handler)),
        )
        client.account.get()
        assert seen["url"] == "https://mein.finanzen-zero.net/api/moneytransaction/account/?customerId=1234567"
        assert seen["xsrf"] == "fake-xsrf"

    def test_relogins_on_rejected_session(self, body: bytes) -> None:
        login_calls: list[int] = []
        client = _client(body, login_calls)
        # simulate a stale session: cookies wiped → first attempt 403 → re-login → retry succeeds
        client._http.apply_session(AuthSession(cookies={}, customer_id="1234567"))
        account = client.account.get()
        assert account.customer_id == "1234567"
        assert login_calls == [1]  # one re-login for the rejected session, no more
