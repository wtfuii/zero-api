"""Tests for the watchlist slice, run against recorded traffic (no live calls)."""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest

from zero_api import ZeroClient
from zero_api.auth.models import AuthSession

_SESSION = AuthSession(cookies={"__i": "fake-token", "XSRF-TOKEN": "fake-xsrf"}, customer_id="1234567")


@pytest.fixture
def json_body(load_fixture: Callable[[str, str], Any]) -> bytes:
    return json.dumps(load_fixture("watchlist", "watchlist")).encode()


def _transport(content: bytes, login_calls: list[int]) -> httpx.MockTransport:
    """Rejects with 403 while the request carries no authenticated cookie."""

    def handle(request: httpx.Request) -> httpx.Response:
        if "__i=fake-token" not in request.headers.get("cookie", ""):
            return httpx.Response(403)
        return httpx.Response(200, content=content, headers={"content-type": "application/json"})

    return httpx.MockTransport(handle)


def _client(content: bytes, login_calls: list[int]) -> ZeroClient:
    def fake_login() -> AuthSession:
        login_calls.append(1)
        return _SESSION

    return ZeroClient(
        email="x@example.com",
        password="secret",
        login=fake_login,
        http=httpx.Client(
            base_url="https://mein.finanzen-zero.net",
            transport=_transport(content, login_calls),
        ),
    )


class TestGet:
    def test_parses_recorded_payload(self, json_body: bytes) -> None:
        login_calls: list[int] = []
        client = _client(json_body, login_calls)
        entries = client.watchlist.get()
        assert login_calls == [1]  # auto-logged-in exactly once, then cached
        assert [entry.watchlist_item.wl_order for entry in entries] == [1, 2, 3, 4, 5]

        first = entries[0]
        assert first.instrument.long_name == "21Shares Crypto Basket Index ETP/ETC"
        assert first.instrument.type == "ETF"
        assert first.instrument.short_name is None
        assert first.instrument.sparplan is True
        assert first.watchlist_item.isin == "CH0445689208"
        assert first.watchlist_item.bid == Decimal("13.131")
        assert first.watchlist_item.bid_when_added == Decimal("18.121")
        assert first.watchlist_item.note == ""
        assert first.watchlist_item.added == datetime(2025, 10, 17, 11, 0, 37, 252000, tzinfo=UTC)
        assert first.instrument.historic_quotes[0].bid == Decimal("13.293")

        infineon = entries[2]
        assert infineon.instrument.type == "AKTIE"
        assert infineon.instrument.short_name == "Infineon"
        assert infineon.instrument_alternative is None

    def test_sends_customer_id_and_xsrf(self, json_body: bytes) -> None:
        seen: dict[str, str] = {}

        def spy_handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["xsrf"] = request.headers.get("x-xsrf-token", "")
            return httpx.Response(200, content=json_body)

        client = ZeroClient(
            email="x@example.com",
            password="secret",
            login=lambda: _SESSION,
            http=httpx.Client(base_url="https://mein.finanzen-zero.net", transport=httpx.MockTransport(spy_handler)),
        )
        client.watchlist.get()
        assert seen["url"] == "https://mein.finanzen-zero.net/api/wl?customerId=1234567"
        assert seen["xsrf"] == "fake-xsrf"


class TestAdd:
    @pytest.fixture
    def add_body(self, load_fixture: Callable[[str, str], Any]) -> bytes:
        return json.dumps(load_fixture("watchlist", "watchlist_add")).encode()

    def test_parses_recorded_payload(self, add_body: bytes) -> None:
        client = _client(add_body, [])
        entry = client.watchlist.add_to_watchlist("DE0007030009")
        # nanosecond-precision `added` from the POST response (GET shows ms)
        assert entry.watchlist_item.added == datetime(2026, 10, 19, 4, 22, 16, 476555, tzinfo=UTC)
        assert entry.watchlist_item.wl_order == 0
        assert entry.instrument.long_name == "Rheinmetall AG"
        assert entry.instrument.short_name == "Rheinmetall"
        assert entry.watchlist_item.isin == "DE0007030009"
        assert entry.watchlist_item.bid == Decimal("1089.0")
        assert entry.instrument_alternative is None

    def test_sends_isin_in_path_and_empty_json_body(self, add_body: bytes) -> None:
        seen: dict[str, str] = {}

        def spy_handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["body"] = request.content.decode()
            seen["content_type"] = request.headers.get("content-type", "")
            return httpx.Response(200, content=add_body)

        client = ZeroClient(
            email="x@example.com",
            password="secret",
            login=lambda: _SESSION,
            http=httpx.Client(base_url="https://mein.finanzen-zero.net", transport=httpx.MockTransport(spy_handler)),
        )
        client.watchlist.add_to_watchlist("DE0007030009")
        assert seen["url"] == "https://mein.finanzen-zero.net/api/wl/DE0007030009?customerId=1234567"
        assert seen["body"] == "{}"
        assert "application/json" in seen["content_type"]


class TestRemove:
    @pytest.fixture
    def remove_body(self, load_fixture: Callable[[str, str], Any]) -> bytes:
        return json.dumps(load_fixture("watchlist", "watchlist_remove")).encode()

    def test_recorded_true_body_removes(self, remove_body: bytes) -> None:
        client = _client(remove_body, [])
        client.watchlist.remove_from_watchlist("DE0007030009")  # returns None on success

    def test_sends_isin_in_path_and_no_body(self, remove_body: bytes) -> None:
        seen: dict[str, str] = {}

        def spy_handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["method"] = request.method
            seen["body"] = request.content.decode()
            seen["content_type"] = request.headers.get("content-type", "")
            return httpx.Response(200, content=remove_body)

        client = ZeroClient(
            email="x@example.com",
            password="secret",
            login=lambda: _SESSION,
            http=httpx.Client(base_url="https://mein.finanzen-zero.net", transport=httpx.MockTransport(spy_handler)),
        )
        client.watchlist.remove_from_watchlist("DE0007030009")
        assert seen["url"] == "https://mein.finanzen-zero.net/api/wl/DE0007030009?customerId=1234567"
        assert seen["method"] == "DELETE"
        assert seen["body"] == ""  # no request body at all
        assert seen["content_type"] == ""

    def test_unexpected_body_fails_loudly(self) -> None:
        client = _client(b"false", [])
        with pytest.raises(ValueError, match="DE0007030009"):
            client.watchlist.remove_from_watchlist("DE0007030009")
