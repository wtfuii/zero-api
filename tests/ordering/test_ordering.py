"""Tests for the ordering slice, run against recorded traffic (no live calls)."""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest

from zero_api import ZeroClient
from zero_api.auth.models import AuthSession
from zero_api.core.exceptions import BrokerApiError
from zero_api.ordering.models import ExecType

_SESSION = AuthSession(cookies={"__i": "fake-token", "XSRF-TOKEN": "fake-xsrf"}, customer_id="1234567")


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> ZeroClient:
    return ZeroClient(
        email="x@example.com",
        password="secret",
        login=lambda: _SESSION,
        http=httpx.Client(base_url="https://mein.finanzen-zero.net", transport=httpx.MockTransport(handler)),
    )


def _fixture_body(load_fixture: Callable[[str, str], Any], name: str) -> bytes:
    return json.dumps(load_fixture("ordering", name)).encode()


def _limit_request() -> Any:
    from zero_api.ordering.models import OrderRequest

    return OrderRequest(
        isin="GB00BJVQC708",
        direction="buy",
        exec_type=ExecType.LIMIT,
        quantity=Decimal("1"),
        mic_code="MUNC",
        limit_price=Decimal("2"),
        valid_to_date=datetime(2026, 11, 30, tzinfo=UTC),
    )


class TestList:
    def test_parses_recorded_payload(self, load_fixture: Callable[[str, str], Any]) -> None:
        client = _client(lambda r: httpx.Response(200, content=_fixture_body(load_fixture, "orders_open")))
        page = client.orders.list()
        assert page.has_next is False
        order = page.list[0]
        assert order.order_id == "GBRd5d801c6318847aea56587e4deb8afdb"
        assert order.status == "Open"
        assert order.exec_type == "limit"
        assert order.limit_price == Decimal("2.000000000000")
        assert order.quantity == Decimal("1.000000000000")
        assert order.blocked_bp == Decimal("3.00")
        assert order.venue_name == "gettex"
        assert order.short_name == "Helios Towers"
        assert order.valid_to_date == datetime(2026, 11, 30, tzinfo=UTC)

    def test_sends_status_and_paging(self, load_fixture: Callable[[str, str], Any]) -> None:
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return httpx.Response(200, content=_fixture_body(load_fixture, "orders_open"))

        _client(handler).orders.list()
        assert seen["url"] == (
            "https://mein.finanzen-zero.net/api/trading/orders"
            "?customerId=1234567&status=Open&page=0&size=1000"
        )


class TestPlace:
    @staticmethod
    def _handle(request: httpx.Request) -> httpx.Response | None:
        """The session is 2FA-unlocked, so place() proceeds straight to the order."""
        if request.url.path == "/api/sessiontan/isauthorized":
            return httpx.Response(200, text="true")
        if request.url.path == "/api/trading/order/verify":
            return httpx.Response(200)  # precheck: 200, empty body
        return None

    def test_parses_recorded_payload(self, load_fixture: Callable[[str, str], Any]) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            probe = self._handle(request)
            if probe is not None:
                return probe
            return httpx.Response(200, content=_fixture_body(load_fixture, "order_placed"))

        order = _client(handler).orders.place(_limit_request())
        assert order.id == 95225194
        assert order.order_id == "GBRd5d801c6318847aea56587e4deb8afdb"
        assert order.status == "Created"
        assert order.ext_order_id is None
        assert order.limit_price == Decimal("2.000000000000")
        assert order.valid_to_date == datetime(2026, 11, 30, tzinfo=UTC)

    def test_sends_captured_payload_shape(self, load_fixture: Callable[[str, str], Any]) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            probe = self._handle(request)
            if probe is not None:
                return probe
            return httpx.Response(200, content=_fixture_body(load_fixture, "order_placed"))

        _client(handler).orders.place(_limit_request())
        assert [r.url.path for r in seen if r.url.path not in ("/version.info", "/api/sessiontan/isauthorized")] == [
            "/api/trading/order/verify",
            "/api/trading/order",
        ]
        body = json.loads(seen[-1].content)
        assert body == {
            "customer": "1234567",
            "direction": "buy",
            "execType": "limit",
            "isin": "GB00BJVQC708",
            "limitPrice": 2.0,
            "minimumQuantitySurcharge": 1,
            "quantity": 1.0,
            "stopPrice": None,
            "validToDate": "2026-11-30T00:00:00Z",
            "trailingPriceAbs": None,
            "trailingPricePct": None,
            "micCode": "MUNC",
        }

    def test_rejection_raises_broker_api_error(self, load_fixture: Callable[[str, str], Any]) -> None:
        body = _fixture_body(load_fixture, "order_rejected")

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/sessiontan/isauthorized":
                return httpx.Response(200, text="true")
            if request.url.path == "/api/trading/order/verify":
                return httpx.Response(200)
            return httpx.Response(406, content=body, headers={"content-type": "application/json"})

        with pytest.raises(BrokerApiError) as exc_info:
            _client(handler).orders.place(_limit_request())
        assert exc_info.value.error_code == "ORDER_TYPE_NOT_ALLOWED_CHEAP_ORDER_OUTSIDE_SESSION_WINDOW"
        assert exc_info.value.status == 406
        assert "Handelszeiten" in exc_info.value.message

    def test_unexpected_success_envelope_fails_loudly(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/sessiontan/isauthorized":
                return httpx.Response(200, text="true")
            return httpx.Response(200, content=b'{"status":"error"}')

        with pytest.raises(ValueError, match="placing"):
            _client(handler).orders.place(_limit_request())


class TestCancel:
    @staticmethod
    def _handle(request: httpx.Request) -> httpx.Response | None:
        """The session is 2FA-unlocked, so cancel() proceeds straight to the cancel."""
        if request.url.path == "/api/sessiontan/isauthorized":
            return httpx.Response(200, text="true")
        return None

    def test_parses_recorded_payload(self, load_fixture: Callable[[str, str], Any]) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            probe = self._handle(request)
            if probe is not None:
                return probe
            return httpx.Response(200, content=_fixture_body(load_fixture, "order_cancelled"))

        order = _client(handler).orders.cancel("GBRd5d801c6318847aea56587e4deb8afdb")
        assert order.status == "Open_CreatedCancel"
        assert order.cancel_date is None

    def test_sends_order_id_body(self, load_fixture: Callable[[str, str], Any]) -> None:
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["body"] = request.content.decode()
            probe = self._handle(request)
            if probe is not None:
                return probe
            return httpx.Response(200, content=_fixture_body(load_fixture, "order_cancelled"))

        _client(handler).orders.cancel("GBRd5d801c6318847aea56587e4deb8afdb")
        assert seen["path"] == "/api/trading/order/cancel"
        assert seen["body"] == '{"customer":"1234567","orderId":"GBRd5d801c6318847aea56587e4deb8afdb"}'
