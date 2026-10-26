"""Tests for the instrument slice, run against recorded traffic (no live calls)."""

import json
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import httpx
import pytest

from zero_api import ZeroClient
from zero_api.auth.models import AuthSession
from zero_api.instrument.models import (
    ChartPeriod,
    InstrumentSearchBucket,
    InstrumentSearchType,
    InstrumentType,
)

_SESSION = AuthSession(cookies={"__i": "fake-token", "XSRF-TOKEN": "fake-xsrf"}, customer_id="1234567")


@pytest.fixture
def fixtures(load_fixture: Callable[[str, str], Any]) -> dict[str, bytes]:
    """Recorded response bodies, routed by the transport below."""
    return {
        "/api/instrument/DE0007030009": json.dumps(load_fixture("instrument", "instrument")).encode(),
        "/api/instrument/IE00B4L5Y983": json.dumps(load_fixture("instrument", "instrument_etf")).encode(),
        "/api/instrument/GB00BLD4ZL17": json.dumps(load_fixture("instrument", "instrument_etc")).encode(),
        "/api/instrument/IE00BYX5P602": json.dumps(load_fixture("instrument", "instrument_fonds")).encode(),
        "/api/instrument/DE000UBS0JQ8": json.dumps(load_fixture("instrument", "instrument_derivat")).encode(),
        "/api/instrument/XC000A2YY6Q6": json.dumps(load_fixture("instrument", "instrument_krypto")).encode(),
        "/api/instrument/detail/DE0007030009": json.dumps(load_fixture("instrument", "instrument_detail")).encode(),
        "/api/instrument/detail/IE00B4L5Y983": json.dumps(load_fixture("instrument", "instrument_detail_etf")).encode(),
        "/api/instrument/detail/GB00BLD4ZL17": json.dumps(load_fixture("instrument", "instrument_detail_etc")).encode(),
        "/api/instrument/detail/IE00BYX5P602": json.dumps(load_fixture("instrument", "instrument_detail_fonds")).encode(),
        "/api/instrument/detail/DE000UBS0JQ8": json.dumps(load_fixture("instrument", "instrument_detail_derivat")).encode(),
        "/api/instrument/detail/XC000A2YY6Q6": json.dumps(load_fixture("instrument", "instrument_detail_krypto")).encode(),
        "/api/instrumentsearch?q=helio": json.dumps(load_fixture("instrument", "instrument_search")).encode(),
        "/api/instrumentsearch?q=GB00BJVQC708": json.dumps(load_fixture("instrument", "instrument_search_isin")).encode(),
        "/api/instrumentsearch?q=MSCI World": json.dumps(load_fixture("instrument", "instrument_search_etf")).encode(),
        "/api/instrumentsearch?q=Bitcoin": json.dumps(load_fixture("instrument", "instrument_search_crypto")).encode(),
        "/extapis/findata/chart?period=OneYear&instruments=1330,345850,1330,814": json.dumps(load_fixture("instrument", "chart_one_year")).encode(),
        "/extapis/findata/chart?period=Maximum&instruments=1330,345850,1330,814": json.dumps(load_fixture("instrument", "chart_maximum")).encode(),
        "/extapis/findata/chart?period=OneYear&instruments=1330,10608388,1330,814": json.dumps(load_fixture("instrument", "chart_etf")).encode(),
        "/extapis/findata/chart?period=OneYear&instruments=402,148732229,402,814": json.dumps(load_fixture("instrument", "chart_derivat")).encode(),
        "/extapis/findata/chart?period=OneYear&instruments=300011,676,,": json.dumps(load_fixture("instrument", "chart_krypto")).encode(),
    }


def _transport(
    responses: dict[str, bytes],
    login_calls: list[int],
    sent: list[httpx.Request] | None = None,
) -> httpx.MockTransport:
    """Rejects with 403 while the request carries no authenticated cookie;
    routes by URL path (+ the query parameters a slice actually varies)."""

    def handle(request: httpx.Request) -> httpx.Response:
        if sent is not None:
            sent.append(request)
        if request.url.path == "/version.info":
            return httpx.Response(200, text="not the expected base64 release string")
        if "__i=fake-token" not in request.headers.get("cookie", ""):
            return httpx.Response(403)
        if request.url.path == "/api/instrumentsearch":
            path = f"{request.url.path}?q={request.url.params.get('q', '')}"
        elif request.url.path == "/extapis/findata/chart":
            period = str(request.url.params.get("period", ""))
            instruments = str(request.url.params.get("instruments", ""))
            path = f"/extapis/findata/chart?period={period}&instruments={instruments}"
        else:
            path = request.url.path
        assert path in responses, f"unexpected request path: {request.url}"
        return httpx.Response(200, content=responses[path], headers={"content-type": "application/json"})

    return httpx.MockTransport(handle)


def _client(
    responses: dict[str, bytes],
    login_calls: list[int],
    sent: list[httpx.Request] | None = None,
) -> ZeroClient:
    def fake_login() -> AuthSession:
        login_calls.append(1)
        return _SESSION

    return ZeroClient(
        email="x@example.com",
        password="secret",
        login=fake_login,
        http=httpx.Client(
            base_url="https://mein.finanzen-zero.net",
            transport=_transport(responses, login_calls, sent),
        ),
    )


class TestSearch:
    def test_parses_recorded_payload(self, fixtures: dict[str, bytes]) -> None:
        sent: list[httpx.Request] = []
        client = _client(fixtures, [], sent)
        page = client.instrument.search("helio", size=3)

        assert [hit.short_name for hit in page.results] == [
            "Heliostar Metals",
            "Helios Towers",
            "Helios Technologies",
        ]
        assert page.stats.took == 1
        assert page.stats.hits[InstrumentSearchBucket.STOCK] == 4
        assert page.stats.total == 4  # one more hit than returned, because size=3

        towers = page.results[1]
        assert (towers.isin, towers.wkn) == ("GB00BJVQC708", "A2PTUX")
        assert towers.type is InstrumentSearchType.STOCK
        assert towers.sparplan and towers.fractional
        assert not towers.weekend_trading

        assert sent[-1].url.params["q"] == "helio"
        assert sent[-1].url.params["size"] == "3"

    def test_isin_lookup_and_default_size(self, fixtures: dict[str, bytes]) -> None:
        sent: list[httpx.Request] = []
        client = _client(fixtures, [], sent)
        page = client.instrument.search("GB00BJVQC708")
        assert [hit.isin for hit in page.results] == ["GB00BJVQC708"]
        assert sent[-1].url.params["size"] == "10"

    def test_parses_etf_fund_and_derivative_hits(self, fixtures: dict[str, bytes]) -> None:
        """Live ``q=MSCI World&size=10`` (2026-10-26): hits say ``ETF`` while
        the stats bucket aggregates them as ``ETF_ETC``."""
        client = _client(fixtures, [])
        page = client.instrument.search("MSCI World", size=10)

        assert page.stats.hits[InstrumentSearchBucket.ETF_ETC] == 189
        assert page.stats.hits[InstrumentSearchBucket.FUND] == 1
        assert page.stats.hits[InstrumentSearchBucket.DERIVATIVE] == 36
        assert page.stats.hits[InstrumentSearchBucket.STOCK] == 0
        assert page.stats.total == 226

        assert {hit.type for hit in page.results} == {
            InstrumentSearchType.ETF,
            InstrumentSearchType.FUND,
            InstrumentSearchType.DERIVATIVE,
        }
        etf = page.results[0]
        assert (etf.isin, etf.wkn, etf.type) == ("IE00B60SX394", "A0RGCS", InstrumentSearchType.ETF)
        assert etf.short_name == ""  # the wire sends "", not null

        fund = next(hit for hit in page.results if hit.type is InstrumentSearchType.FUND)
        assert (fund.isin, fund.fractional) == ("IE00BYX5P602", False)
        deriv = next(hit for hit in page.results if hit.type is InstrumentSearchType.DERIVATIVE)
        assert (deriv.isin, deriv.sparplan) == ("DE000UBS0JQ8", False)

        # size caps each stats bucket, not the response: 10 ETF + 10 DERIVAT + 1 FONDS
        assert len(page.results) == 21

    def test_etf_and_etc_hits_share_the_etf_etc_bucket(self, fixtures: dict[str, bytes]) -> None:
        """Live ``q=Bitcoin&size=40`` (2026-10-26): ``ETF`` and ``ETC`` are
        distinct hit types but one stats bucket; crypto hits have an empty WKN."""
        client = _client(fixtures, [])
        page = client.instrument.search("Bitcoin", size=40)

        assert page.stats.hits[InstrumentSearchBucket.ETF_ETC] == 23
        assert page.stats.hits[InstrumentSearchBucket.CRYPTO] == 2
        assert page.stats.hits[InstrumentSearchBucket.DERIVATIVE] == 4752
        assert page.stats.hits[InstrumentSearchBucket.FUND] == 0

        etc = next(hit for hit in page.results if hit.type is InstrumentSearchType.ETC)
        assert (etc.isin, etc.wkn) == ("GB00BLD4ZL17", "A3GPMN")
        etf = next(hit for hit in page.results if hit.type is InstrumentSearchType.ETF)
        assert etf.isin == "IE0008EKJPN4"
        crypto = next(hit for hit in page.results if hit.type is InstrumentSearchType.CRYPTO)
        assert (crypto.isin, crypto.wkn, crypto.short_name) == ("XC000A2YY6Q6", "", "BTC")
        assert len(page.results) == 68

    def test_unknown_hit_type_fails(self, fixtures: dict[str, bytes]) -> None:
        mutated = dict(fixtures)
        key = "/api/instrumentsearch?q=Bitcoin"
        body = json.loads(mutated[key])
        body["results"][-1]["type"] = "ZERTIFIKAT"
        mutated[key] = json.dumps(body).encode()
        client = _client(mutated, [])
        with pytest.raises(ValueError, match="ZERTIFIKAT"):
            client.instrument.search("Bitcoin", size=40)

    def test_unknown_stats_bucket_fails(self, fixtures: dict[str, bytes]) -> None:
        mutated = dict(fixtures)
        key = "/api/instrumentsearch?q=Bitcoin"
        body = json.loads(mutated[key])
        body["stats"]["hits"] = {
            "AKTIE": 3,
            "ETF": 23,
            "FONDS": 0,
            "DERIVAT": 4752,
            "KRYPTO": 2,
        }
        mutated[key] = json.dumps(body).encode()
        client = _client(mutated, [])
        with pytest.raises(ValueError, match="ETF"):
            client.instrument.search("Bitcoin", size=40)

    def test_drift_fails(self, fixtures: dict[str, bytes]) -> None:
        mutated = dict(fixtures)
        key = "/api/instrumentsearch?q=helio"
        body = json.loads(mutated[key])
        body["results"][0]["currency"] = "EUR"
        mutated[key] = json.dumps(body).encode()
        client = _client(mutated, [])
        with pytest.raises(ValueError, match="currency"):
            client.instrument.search("helio", size=3)


class TestGet:
    def test_parses_recorded_payload(self, fixtures: dict[str, bytes]) -> None:
        login_calls: list[int] = []
        client = _client(fixtures, login_calls)
        instrument = client.instrument.get("DE0007030009")
        assert login_calls == [1]  # auto-logged-in exactly once, then cached
        assert instrument.type is InstrumentType.STOCK
        assert (instrument.short_name, instrument.long_name) == ("Rheinmetall", "Rheinmetall AG")
        assert instrument.notiz == "STUECK"
        assert instrument.sparplan and instrument.tradable == "OPEN"
        assert instrument.option_type is None and instrument.underlying is None

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
        assert instrument.ranking is not None and instrument.sentiment is not None
        assert (instrument.ranking.rank_total, instrument.sentiment.buy) == (1, 48)

    def test_parses_etf(self, fixtures: dict[str, bytes]) -> None:
        client = _client(fixtures, [])
        instrument = client.instrument.get("IE00B4L5Y983")
        assert instrument.type is InstrumentType.ETF
        assert instrument.wkn == "A0RPWH"
        # fund products carry no short name, ticker symbol or finnet names
        assert instrument.short_name is None
        assert instrument.symbol is None
        assert instrument.finnet_short_name is None and instrument.finnet_name is None
        assert instrument.option_type is None and instrument.underlying is None
        assert instrument.ranking is not None and instrument.sentiment is not None
        assert instrument.ranking.rank_total == 1
        assert instrument.sentiment.buy == 61
        assert instrument.disabled_exec_types == []

    def test_parses_etc_as_etf(self, fixtures: dict[str, bytes]) -> None:
        """The search hit says ETC, the master says ETF."""
        client = _client(fixtures, [])
        instrument = client.instrument.get("GB00BLD4ZL17")
        assert instrument.type is InstrumentType.ETF
        assert instrument.isin == "GB00BLD4ZL17"
        assert instrument.short_name is None and instrument.symbol is None
        assert instrument.complex is True
        assert instrument.emittent_short_code == "Coin"
        assert instrument.ranking is not None
        assert instrument.ranking.rank_total == 69

    def test_parses_fund(self, fixtures: dict[str, bytes]) -> None:
        client = _client(fixtures, [])
        instrument = client.instrument.get("IE00BYX5P602")
        assert instrument.type is InstrumentType.FUND
        assert instrument.isin == "IE00BYX5P602"
        assert instrument.short_name is None and instrument.symbol is None
        assert instrument.ranking is None and instrument.sentiment is None
        assert instrument.icon_url is not None
        assert instrument.sparplan is False
        assert instrument.emittent_short_code is None

    def test_parses_derivative(self, fixtures: dict[str, bytes]) -> None:
        client = _client(fixtures, [])
        instrument = client.instrument.get("DE000UBS0JQ8")
        assert instrument.type is InstrumentType.DERIVATIVE
        assert instrument.option_type == "CALL"
        assert instrument.underlying == "MSCI World 4.5% Decrement EUR Index"
        assert instrument.underlying_isin == "GB00BSBGRG79"
        assert instrument.short_name is not None
        assert instrument.symbol is None and instrument.icon_url is None
        assert instrument.ranking is None and instrument.sentiment is None
        # first non-empty disabledExecTypes ever captured
        assert instrument.disabled_exec_types == ["oco", "tsl"]
        assert instrument.market_name == "UBS"
        assert instrument.is_o_tc is True
        assert instrument.minimum_order_value == 0

    def test_parses_crypto(self, fixtures: dict[str, bytes]) -> None:
        client = _client(fixtures, [])
        instrument = client.instrument.get("XC000A2YY6Q6")
        assert instrument.type is InstrumentType.CRYPTO
        assert instrument.wkn is None  # no German security ID
        assert (instrument.short_name, instrument.symbol) == ("BTC", "BTCEUR")
        assert instrument.max_amount == Decimal("100000")
        assert instrument.qty_scale == 8
        assert instrument.min_qty == Decimal("0.00000001")
        assert instrument.fractional_trading is False
        assert instrument.ranking is None and instrument.sentiment is None
        assert instrument.icon_url is None

    def test_unknown_type_fails(self, fixtures: dict[str, bytes]) -> None:
        mutated = dict(fixtures)
        key = "/api/instrument/IE00B4L5Y983"
        master = json.loads(mutated[key])
        master["type"] = "ZERTIFIKAT"
        mutated[key] = json.dumps(master).encode()
        client = _client(mutated, [])
        with pytest.raises(ValueError, match="ZERTIFIKAT"):
            client.instrument.get("IE00B4L5Y983")

    @pytest.mark.parametrize(
        "isin",
        [
            "DE0007030009",
            "IE00B4L5Y983",
            "GB00BLD4ZL17",
            "IE00BYX5P602",
            "DE000UBS0JQ8",
            "XC000A2YY6Q6",
        ],
    )
    def test_extra_field_fails_for_every_class(
        self, fixtures: dict[str, bytes], isin: str
    ) -> None:
        mutated = dict(fixtures)
        key = f"/api/instrument/{isin}"
        master = json.loads(mutated[key])
        master["unexpectedField"] = 1
        mutated[key] = json.dumps(master).encode()
        client = _client(mutated, [])
        with pytest.raises(ValueError, match="unexpectedField"):
            client.instrument.get(isin)

    def test_etc_search_hit_maps_to_etf_master(self, fixtures: dict[str, bytes]) -> None:
        client = _client(fixtures, [])
        page = client.instrument.search("Bitcoin", size=40)
        hit = next(hit for hit in page.results if hit.isin == "GB00BLD4ZL17")
        assert hit.type is InstrumentSearchType.ETC
        master = client.instrument.get(hit.isin)
        assert master.type is InstrumentType.ETF

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

    @pytest.mark.parametrize(
        ("isin", "instrument_type"),
        [
            ("DE0007030009", 1),  # share
            ("DE000UBS0JQ8", 1),  # derivative — same int!
            ("IE00B4L5Y983", 4),  # ETF
            ("IE00BYX5P602", 4),  # fund
            ("XC000A2YY6Q6", 4),  # crypto
            ("GB00BLD4ZL17", 5),  # ETC
        ],
    )
    def test_instrument_type_is_not_a_discriminator(
        self, fixtures: dict[str, bytes], isin: str, instrument_type: int
    ) -> None:
        client = _client(fixtures, [])
        assert client.instrument.detail(isin).instrument_type == instrument_type

    def test_derivative_omits_market_fields(self, fixtures: dict[str, bytes]) -> None:
        client = _client(fixtures, [])
        detail = client.instrument.detail("DE000UBS0JQ8")
        assert detail.first is None
        assert detail.revenue is None
        assert detail.w52_close is None
        assert detail.w52_high is None
        assert detail.w52low is None
        assert detail.volume is None
        assert detail.outstanding_shares == 10000.0
        assert (detail.w52_high_date, detail.w52_low_date) == ("", "")
        assert detail.branches == [] and detail.dividends == []
        assert (detail.symbol, detail.wpkn) == ("UBS0JQ", "UBS0JQ")

    def test_crypto_extras_and_omissions(self, fixtures: dict[str, bytes]) -> None:
        client = _client(fixtures, [])
        detail = client.instrument.detail("XC000A2YY6Q6")
        assert detail.fin_id == "676"
        assert detail.tk_identifier == "300011,676,,"
        assert detail.tk_identifier_intraday == "300012,676,,"
        assert detail.first is None
        assert detail.performance_current_year is None
        assert detail.revenue is None
        assert detail.w52_close is None
        assert detail.outstanding_shares is None
        assert detail.volume is None
        assert detail.prev_day_datetime is None
        assert detail.source_datetime is None
        assert detail.symbol is None and detail.wpkn is None
        assert detail.w52_high_date is None and detail.w52_low_date is None
        assert detail.w52_high == 99745.03259047188
        assert detail.exchange == "CXR"

    def test_fund_omits_outstanding_shares(self, fixtures: dict[str, bytes]) -> None:
        client = _client(fixtures, [])
        detail = client.instrument.detail("IE00BYX5P602")
        assert detail.outstanding_shares is None
        assert (detail.revenue, detail.volume) == (6.0, 72)
        assert detail.country is None and detail.profile is None
        assert detail.dividends == []

    def test_etf_omits_company_fields(self, fixtures: dict[str, bytes]) -> None:
        client = _client(fixtures, [])
        detail = client.instrument.detail("IE00B4L5Y983")
        assert detail.country is None and detail.profile is None
        assert detail.outstanding_shares == 1013795902.0
        assert detail.volume == 2553160


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

    def test_etf_rows_occasionally_omit_open(self, fixtures: dict[str, bytes]) -> None:
        client = _client(fixtures, [])
        bars = client.instrument.chart("IE00B4L5Y983", ChartPeriod.ONE_YEAR)
        assert len(bars) == 253
        assert sum(bar.open_ is None for bar in bars) == 1
        assert all(bar.volume is not None for bar in bars)

    def test_derivative_rows_omit_open_and_volume(self, fixtures: dict[str, bytes]) -> None:
        client = _client(fixtures, [])
        bars = client.instrument.chart("DE000UBS0JQ8", ChartPeriod.ONE_YEAR)
        assert len(bars) == 244
        assert all(bar.open_ is None and bar.volume is None for bar in bars)
        assert all(bar.high is not None and bar.low is not None for bar in bars)

    def test_crypto_rows_only_have_date_and_close(self, fixtures: dict[str, bytes]) -> None:
        client = _client(fixtures, [])
        bars = client.instrument.chart("XC000A2YY6Q6", ChartPeriod.ONE_YEAR)
        assert len(bars) == 366
        assert all(
            bar.open_ is None and bar.high is None and bar.low is None and bar.volume is None
            for bar in bars
        )
        assert bars[0].date == "2025-10-26 00:00"
