"""Models for the ordering slice — see docs/explorations/order.md, order_limit.md
and order_types.md."""

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer
from pydantic.alias_generators import to_camel


class ExecType(StrEnum):
    """Wire values of the order request's ``execType`` (order_types.md).

    All types were captured as /verify payloads except ``stop`` (plain
    Stopp-Loss, only seen in an exante call) — left out until verified.
    """

    MARKET = "market"
    LIMIT = "limit"
    TSL = "tsl"  # trailing stop loss: trailing_price_pct or _abs (plus stop_price)
    OCO = "oco"  # limit_price + stop_price
    STOP_LIMIT = "stoplimit"  # limit_price + stop_price


class OrderExecType(StrEnum):
    """Wire values of ``execType`` in order responses.

    Order history also contains types the client does not place itself:
    ``savings`` (Sparplan execution), ``fraction`` (fractional order),
    ``previously_quoted`` (RFQ) and plain ``stop``. The full set is the SPA
    bundle's label map for ``execType`` (``market:"Markt", stop:"Stop",
    stoplimit:"Stop Limit", limit:"Limit", savings:"Sparplan",
    fraction:"Bruchstücke", oco:"OCO", tsl:"TSL", previously_quoted:"RFQ"``),
    so responses are typed separately from :class:`ExecType` and
    ``OrderRequest`` keeps rejecting the placement-incompatible values.
    """

    MARKET = "market"
    LIMIT = "limit"
    TSL = "tsl"
    OCO = "oco"
    STOP_LIMIT = "stoplimit"
    STOP = "stop"
    SAVINGS = "savings"
    FRACTION = "fraction"
    PREVIOUSLY_QUOTED = "previously_quoted"


class OrderRequest(BaseModel):
    """Payload for POST /api/trading/order (identical to the /verify precheck).

    The site always sends every key, ``null`` included, so serialization never
    excludes fields. Money fields go out as bare JSON numbers like the site's
    (precision is only relevant on the response side, parsed as Decimal).
    ``valid_to_date`` unset means "now" — what the UI sends for market orders
    (order.md); GTD limit orders pass an explicit date (order_limit.md).
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    isin: str
    direction: str  # "buy" / "sell"
    exec_type: ExecType
    quantity: Decimal
    mic_code: str  # target exchange, e.g. "MUNC"
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    trailing_price_abs: Decimal | None = None
    trailing_price_pct: Decimal | None = None
    minimum_quantity_surcharge: int = 1
    valid_to_date: datetime | None = None

    @field_serializer("limit_price", "stop_price", "trailing_price_abs", "trailing_price_pct", "quantity")
    def _number(self, value: Decimal | None) -> float | None:
        return None if value is None else float(value)

    @field_serializer("valid_to_date")
    def _valid_to(self, value: datetime | None) -> str:
        dt = value or datetime.now(UTC)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)  # site always sends UTC-instants ("…Z")
        return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


class ExanteResult(BaseModel):
    """Response of POST /apic/exante (order.md): the ex-ante Kostenausweis is
    generated into the Posteingang; the call reports doc id + uri, no content."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    success: bool
    posteingang_id: str
    uri: str


class Order(BaseModel):
    """One order object, as returned by placement, listing and cancellation.

    Full observed field set (extra="forbid" so broker drift fails loudly).
    Response fields vary slightly by endpoint (the list adds venueName etc.);
    everything not always present defaults to None.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    id: int
    order_id: str  # "GBR…" string key used for cancel
    ext_order_id: str | None = None  # set later by the backend/venue
    depot_id: str
    customer_id: str
    isin: str
    asset_name: str
    short_name: str | None = None
    instrument_type: str
    instrument_with_leverage: bool
    direction: str
    exec_type: OrderExecType
    status: str  # observed: Created, Open, Open_CreatedCancel; full enum unverified
    quantity: Decimal
    executed_quantity: Decimal
    open_quantity: Decimal
    limit_price: Decimal | None = None  # null for market orders
    stop_price: Decimal | None = None
    trailing_price_abs: Decimal | None = None
    trailing_price_pct: Decimal | None = None
    latest_tsl_stop: Decimal | None = None
    latest_tsl_stop_time: datetime | None = None
    valid_to_date: datetime
    # double-cap source keys defeat the camelCase alias generator → explicit aliases
    blocked_bp: Decimal = Field(alias="blockedBP")
    bp_blocked_price: Decimal | None = None
    bp_blocked_price_timestamp: datetime | None = None
    open_date: datetime
    exec_price: Decimal | None = None
    exec_date: datetime | None = None
    cancel_date: datetime | None = None
    reject_reason: str | None = None
    error_code: str | None = None
    destination_mic: str | None = Field(default=None, alias="destinationMIC")
    venue_name: str | None = None  # only in list responses (e.g. "gettex")
    last_backend_update: datetime | None = None
    created: datetime | None = None
    last_updated: datetime | None = None
    transaction_key: str | None = None
    matched_import_storno: str | None = None
    intermediary_shortcode: str | None = None
    minimum_quantity_surcharge: Decimal
    fractional_surcharge: Decimal | None = None
    fixed_remaining_amount: Decimal | None = None
    provision_bank_pct: Decimal | None = None
    provision_bank_eur: Decimal | None = None
    provision_fz_pct: Decimal | None = None
    provision_fz_eur: Decimal | None = None
    trading_venue_fee: Decimal | None = None
    quote: Any = None  # always null so far, shape unknown
    quote_request_id: str | None = None
    quote_id: str | None = None
    plv_version: str | None = None
    deliver_to: str | None = None


class OrdersPage(BaseModel):
    """One page of GET /api/trading/orders."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    list: list[Order]
    has_next: bool
