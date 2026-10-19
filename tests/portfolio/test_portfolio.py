"""Tests for the portfolio slice, run against recorded traffic (no live calls)."""

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
    return json.dumps(load_fixture("portfolio", "positions")).encode()


def _client(body: bytes) -> ZeroClient:
    return ZeroClient(
        email="x@example.com",
        password="secret",
        login=lambda: _SESSION,
        http=httpx.Client(base_url="https://mein.finanzen-zero.net", transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=body, headers={"content-type": "application/json"}),
        )),
    )


class TestGetPositions:
    def test_parses_recorded_payload(self, body: bytes) -> None:
        positions = _client(body).portfolio.get_positions()
        assert len(positions) == 1
        position = positions[0]
        assert position.isin == "GB00BJVQC708"
        assert position.depot_id == "K1234567001"
        assert position.quantity == Decimal("1")
        assert position.available_quantity == Decimal("1")
        assert position.quote == Decimal("2.65")
        assert position.avg_entry_quote == Decimal("2.65")
        assert position.avg_entry_quote_edited is None
        assert position.entry_quote_fifo == Decimal("3.65")
        assert position.asset_name == "Helios Towers PLC Registered Shs"
        assert position.order_execution_types_for_protection == []

    def test_sends_customer_id_and_protection_flag(self, body: bytes) -> None:
        seen: dict[str, str] = {}

        def spy_handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return httpx.Response(200, content=body)

        client = ZeroClient(
            email="x@example.com",
            password="secret",
            login=lambda: _SESSION,
            http=httpx.Client(base_url="https://mein.finanzen-zero.net", transport=httpx.MockTransport(spy_handler)),
        )
        client.portfolio.get_positions()
        assert seen["url"] == (
            "https://mein.finanzen-zero.net/api/trading/positions?customerId=1234567&withProtectionInfo=true"
        )

    def test_empty_depot(self, load_fixture: Callable[[str, str], Any]) -> None:
        body = json.dumps(load_fixture("portfolio", "positions_empty")).encode()
        assert _client(body).portfolio.get_positions() == []
