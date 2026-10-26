"""Models for the instrument slice.

Shapes of GET /api/instrument/{isin}, GET /api/instrument/detail/{isin} and
GET /extapis/findata/chart.
``extra="forbid"`` so silent schema drift by the broker fails loudly.

All five master classes were captured on 2026-10-26 (share, ETF incl. ETC,
fund, derivative, crypto). Their payloads share one 50-key shape, so
``Instrument`` is a single model with a typed ``type`` and optional fields —
not a union. The detail endpoint has no reliable class discriminator either, so
``InstrumentDetail`` is likewise one model with optional fields.
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class HistoricQuote(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    id: int
    bid: Decimal
    ask: Decimal
    ts: datetime
    isin: str


class VenueTradingFees(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    minimum_quantity_surcharge: Decimal
    fractional_surcharge: Decimal
    fee_pct: Decimal
    savings_plan_fee_pct: Decimal
    trading_venue_fee: Decimal


class TradingVenue(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    trading_venue_name: str
    mic_code: str
    trading_hours_start: str  # "07:30:00", the wire keeps plain strings
    trading_hours_stop: str
    fractional_trading: bool
    weekend_trading: bool
    savings_plan: bool
    default_venue: bool
    trading_fees: VenueTradingFees


class _MasterTradingFees(BaseModel):
    """Top-level ``tradingFees`` — note ``feeSavingsplanPct``, unlike the
    venue variant's ``savingsPlanFeePct`` (both verbatim from the capture)."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    minimum_quantity_surcharge: Decimal
    fractional_surcharge: Decimal
    fee_pct: Decimal
    fee_savingsplan_pct: Decimal


class Ranking(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    rank_total: int
    trend: int


class Sentiment(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    buy: int
    sell: int


class InstrumentType(StrEnum):
    """Asset class of an instrument master record (``type``).

    This is the **master** vocabulary. It has no ``ETC``: an ETC master reports
    ``type: "ETF"`` (captured ``GB00BLD4ZL17``). Search hits use the separate,
    six-value ``InstrumentSearchType``.
    """

    STOCK = "AKTIE"
    ETF = "ETF"
    FUND = "FONDS"
    DERIVATIVE = "DERIVAT"
    CRYPTO = "KRYPTO"


class Instrument(BaseModel):
    """The instrument master record — trading rules, venues, fees, quote.

    Captured for all five classes on 2026-10-26 (share, ETF incl. ETC, fund,
    derivative, crypto). Every payload has the same 50 keys, so this is one
    model, not a union; ``type`` identifies the class and an ETC master reports
    ``ETF``. Fields are Optional where *any* class sends null: WKN for crypto,
    shortName for ETF/ETC/funds, symbol for ETF/ETC/funds/derivatives,
    underlying/optionType for non-derivatives, ranking/sentiment for
    fund/derivative/crypto, maxAmount except crypto. They are deliberately not
    narrowed per class: a single capture per class cannot tell class structure
    from per-instrument variation.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    isin: str
    wkn: str | None  # null for crypto (no German security ID)
    short_name: str | None  # null for ETF/ETC/funds
    long_name: str
    symbol: str | None  # null for ETF/ETC/funds/derivatives
    type: InstrumentType
    option_type: str | None  # "CALL" on the captured derivative, null elsewhere
    # document links; ETF/ETC send "" when a document does not exist
    basisinformationen: str | None
    jahresbericht: str | None
    halbjahresbericht: str | None
    verkaufsprospekt: str | None
    leverage: bool
    complex: bool
    notiz: str
    underlying_isin: str | None
    underlying: str | None
    sparplan: bool
    price_scale: int
    qty_scale: int
    min_qty: Decimal
    max_amount: Decimal | None
    qty_incr: Decimal
    price_incr: Decimal
    fractional_trading: bool
    weekend_trading: bool
    finnet_short_name: str | None  # only shares carried these two
    finnet_name: str | None
    # only "OPEN" captured; other states unverified
    tradable: str
    minimum_order_value: Decimal
    low_trading_threshold: Decimal
    low_trading_threshold_quantity: Decimal
    quote_currency_code: str
    market_name: str
    is_o_tc: bool = Field(default=False, alias="isOTC")
    trading_venues: list[TradingVenue]
    knocked_out_flag: bool
    turbo_knock_out_test_required: bool
    trading_hours_start_time: str  # "07:30", like the wire
    trading_hours_stop_time: str
    quantity_unit_code: str
    emittent_short_code: str | None
    nominal: int
    endfaellig: bool
    icon_url: str | None
    # empty for shares/ETFs/funds/crypto; a derivative carried ["oco", "tsl"]
    disabled_exec_types: list[str] = []
    historic_quotes: list[HistoricQuote]
    # null in every capture; the populated shape is still unseen
    quote: object | None
    ranking: Ranking | None  # null for funds/derivatives/crypto
    sentiment: Sentiment | None
    trading_fees: _MasterTradingFees


class InstrumentSearchType(StrEnum):
    """Asset class in a search hit's ``type`` (GET /api/instrumentsearch).

    This is **not** the ``stats.hits`` vocabulary: the server returns an ETF hit
    as ``ETF`` and an exchange-traded commodity as ``ETC``, two distinct values
    that the stats bucket merges as ``ETF_ETC``. Captured 2026-10-26 with
    ``q=Bitcoin`` (both ``ETF`` and ``ETC`` hits next to ``ETF_ETC: 23``).
    """

    STOCK = "AKTIE"
    ETF = "ETF"
    ETC = "ETC"
    FUND = "FONDS"
    DERIVATIVE = "DERIVAT"
    CRYPTO = "KRYPTO"


class InstrumentSearchBucket(StrEnum):
    """Keys of ``stats.hits`` — match counts per asset class.

    Separate vocabulary from ``InstrumentSearchType``: ``ETF`` and ``ETC`` hits
    are both counted under ``ETF_ETC``. Captured 2026-10-26 with
    ``q=MSCI World&size=10`` (``ETF_ETC: 189`` while ten ``ETF`` hits were
    returned) and ``q=Bitcoin&size=40`` (``ETF_ETC: 23`` = 22 ``ETC`` + 1
    ``ETF``).
    """

    STOCK = "AKTIE"
    ETF_ETC = "ETF_ETC"
    FUND = "FONDS"
    DERIVATIVE = "DERIVAT"
    CRYPTO = "KRYPTO"


class InstrumentSearchResult(BaseModel):
    """One typeahead hit (GET /api/instrumentsearch) — enough to open the
    instrument, chart it, add it to the watchlist or start an order."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    isin: str
    wkn: str
    long_name: str
    short_name: str  # "" (not null) in the capture, even for named ETFs
    type: InstrumentSearchType
    tradable_business: bool
    weekend_trading: bool
    sparplan: bool
    fractional: bool
    is_featured: bool


class InstrumentSearchStats(BaseModel):
    """Search metadata: latency and match counts per asset class.

    ``hits`` counts every match; ``results`` holds at most ``size`` hits *per
    bucket* — a live ``size=10`` search returned 21 hits over three buckets
    (instrument.md, open question 8).
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    took: int
    hits: dict[InstrumentSearchBucket, int]

    @property
    def total(self) -> int:
        return sum(self.hits.values())


class InstrumentSearchPage(BaseModel):
    """Envelope of a search response: the hits plus the per-class counts."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    stats: InstrumentSearchStats
    results: list[InstrumentSearchResult]


class Dividend(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    year: str  # estimate years carry an "e" suffix ("2026e"), so string
    dividend_yield_percentage: float
    dividend: float


class InstrumentDetail(BaseModel):
    """Market data & fundamentals of the detail page.

    The wire has **no reliable class discriminator**: ``instrument_type`` is 1
    for both shares and the captured derivative, 4 for ETF/fund/crypto and 5
    for ETC. Field presence varies by class — derivatives omit ``first``,
    ``revenue``, ``w52Close``, ``w52High``, ``w52low`` and ``volume``; crypto
    omits most of those plus the datetimes, ``symbol`` and ``wpkn``, and adds
    ``finId`` and ``tkIdentifierIntraday``. Hence one model with optional
    fields; use ``get()`` when you need the instrument class.

    ``tk_identifier`` is the verbatim ``instruments`` query parameter of
    GET /extapis/findata/chart.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    name: str
    isin: str
    first: float | None = None
    high: float
    last: float
    low: float
    net_change: float
    percent: float
    performance_current_year: float | None = None
    prev_day: float
    revenue: float | None = None
    w52_close: float | None = None
    w52_high: float | None = None
    w52_high_date: str | None = None  # "19.01.2026", the wire keeps display dates
    w52low: float | None = None
    w52_low_date: str | None = None
    instrument_type: int
    outstanding_shares: float | None = None
    volume: float | None = None
    currency: str
    date: str
    exchange: str
    prev_day_datetime: str | None = None
    push_identifier: str
    source_datetime: str | None = None
    symbol: str | None = None  # the WKN, despite the name; absent for crypto
    time: str
    tk_identifier: str
    wpkn: str | None = None
    country: str | None = None  # share only
    branches: list[str]
    profile: str | None = None  # share only
    dividends: list[Dividend]
    # crypto-only extras
    fin_id: str | None = None
    tk_identifier_intraday: str | None = None


class ChartPeriod(StrEnum):
    """Wire values of the chart endpoint's ``period`` query parameter."""

    INTRADAY = "Intraday"
    ONE_WEEK = "OneWeek"
    ONE_MONTH = "OneMonth"
    SIX_MONTHS = "SixMonths"
    CURRENT_YEAR = "CurrentYear"
    ONE_YEAR = "OneYear"  # initial/default load in the SPA
    TWO_YEARS = "TwoYears"
    FIVE_YEARS = "FiveYears"
    MAXIMUM = "Maximum"


class ChartBar(BaseModel):
    """One chart row. Row shape varies per period — only ``Date`` and ``Close``
    are guaranteed; bar fields are optional and ``Volume`` may be absent."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    date: str = Field(alias="Date")  # "16.10.2026 22:55" (intraday) or "2026-10-15" (daily)
    open_: float | None = Field(default=None, alias="Open")
    high: float | None = Field(default=None, alias="High")
    low: float | None = Field(default=None, alias="Low")
    close: float = Field(alias="Close")
    volume: float | None = Field(default=None, alias="Volume")