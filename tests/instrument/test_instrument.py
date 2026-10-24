"""Tests for the instrument slice, run against recorded traffic (no live calls)."""

import json
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import httpx
import pytest

from zero_api import ZeroClient
from zero_api.auth.models import AuthSession
from zero_api.instrument.models import ChartPeriod

_SESSION = AuthSession(cookies={"__i": "fake-token", "XSRF-TOKEN": "fake-xsrf"}, customer_id="1234567")


@pytest.fixture
def fixtures(load_fixture: Callable[[str, str], Any]) -> dict[str, bytes]:
    """Recorded response bodies, routed by the transport below."""
    return {
        "/api/instrument/DE0007030009": json.dumps(load_fixture("instrument", "instrument")).encode(),
        "/api/instrument/detail/DE0007030009": json.dumps(load_fixture("instrument", "instrument_detail")).encode(),
        "/extapis/findata/chart?period=OneYear": json.dumps(load_fixture("instrument", "chart_one_year")).encode(),
        "/extapis/findata/chart?period=Maximum": json.dumps(load_fixture("instrument", "chart_maximum")).encode(),
    }


def _transport(responses: dict[str, bytes], login_calls: list[int]) -> httpx.MockTransport:
    """Rejects with 403 while the request carries no authenticated cookie;
    routes by URL path (+ period query for the chart)."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/version.info":
            return httpx.Response(200, text="not the expected base64 release string")
        if "__i=fake-token" not in request.headers.get("cookie", ""):
            return httpx.Response(403)
        period = str(request.url.params.get("period", ""))
        path = f"/extapis/findata/chart?period={period}" if period else request.url.path
        assert path in responses, f"unexpected request path: {request.url}"
        return httpx.Response(200, content=responses[path], headers={"content-type": "application/json"})

    return httpx.MockTransport(handle)


def _client(responses: dict[str, bytes], login_calls: list[int]) -> ZeroClient:
    def fake_login() -> AuthSession:
        login_calls.append(1)
        return _SESSION

    return ZeroClient(
        email="x@example.com",
        password="secret",
        login=fake_login,
        http=httpx.Client(
            base_url="https://mein.finanzen-zero.net",
            transport=_transport(responses, login_calls),
        ),
    )


class TestGet:
    def test_parses_recorded_payload(self, fixtures: dict[str, bytes]) -> None:
        login_calls: list[int] = []
        client = _client(fixtures, login_calls)
        instrument = client.instrument.get("DE0007030009")
        assert login_calls == [1]  # auto-logged-in exactly once, then cached
        assert (instrument.short_name, instrument.long_name) == ("Rheinmetall", "Rheinmetall AG")
        assert (instrument.type, instrument.notiz) == ("AKTIE", "STUECK")
        assert instrument.sparplan and instrument.tradable == "OPEN"

        assert instrument.min_qty == 1
        assert instrument.price_incr == Decimal("0.01")
        assert instrument.minimum_order_value == Decimal("0.01")

        venues = {venue.trading_venue_name: venue for venue in instrument.trading_venues}
        assert set(venues) == {"gettex", "Tradegate"}
        assert venues["gettex"].mic_code == "MUNC"
        assert venues["gettex"].default_venue and not venues["Tradegate"].default_venue
        assert venues["Tradegate"].trading_fees.trading_venue_fee == 2

        assert instrument.trading_fees.minimum_quantity_surcharge == 1
        assert instrument.historic_quotes[0].bid == 1031
        assert (instrument.ranking.rank_total, instrument.sentiment.buy) == (1, 48)

    def test_drift_fails(self, fixtures: dict[str, bytes]) -> None:
        login_calls: list[int] = []
        mutated = dict(fixtures)
        master = json.loads(mutated["/api/instrument/DE0007030009"])
        del master["priceIncr"]
        mutated["/api/instrument/DE0007030009"] = json.dumps(master).encode()
        client = _client(mutated, login_calls)
        with pytest.raises(ValueError, match="priceIncr"):
            client.instrument.get("DE0007030009")


class TestDetail:
    def test_parses_recorded_payload(self, fixtures: dict[str, bytes]) -> None:
        login_calls: list[int] = []
        client = _client(fixtures, login_calls)
        detail = client.instrument.detail("DE0007030009")
        assert detail.name == "Rheinmetall AG"
        assert detail.tk_identifier == "1330,345850,1330,814"
        assert detail.w52low == 902.0
        assert detail.w52_high == 1965.0
        assert detail.dividends[-1].year == "2026e"
        assert len(detail.branches) == 4


class TestChart:
    def test_daily_bars(self, fixtures: dict[str, bytes]) -> None:
        login_calls: list[int] = []
        client = _client(fixtures, login_calls)
        bars = client.instrument.chart("DE0007030009", ChartPeriod.ONE_YEAR)
        assert len(bars) == 254
        assert bars[0].volume is not None and bars[0].volume > 0

    def test_maximum_rows_omit_volume(self, fixtures: dict[str, bytes]) -> None:
        login_calls: list[int] = []
        client = _client(fixtures, login_calls)
        bars = client.instrument.chart("DE0007030009", ChartPeriod.MAXIMUM)
        assert len(bars) == 2982
        assert bars[0].volume is None  # old rows omit Volume...
        assert bars[-1].volume is not None  # ...recent ones have it
        assert bars[0].open_ is not None
