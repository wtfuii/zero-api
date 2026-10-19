"""Models for the depot positions (Depotbestände).

Shape of GET /api/trading/positions — see docs/explorations/positions.md.
``extra="forbid"`` so silent schema drift by the broker fails loudly.
"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class HistoricQuote(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    id: int
    bid: Decimal
    ask: Decimal
    ts: datetime
    isin: str


class PositionInstrument(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    historic_quotes: list[HistoricQuote]
    logo_url: str
    sparplan: bool
    knocked_out_flag: bool
    endfaellig: bool
    long_name: str
    isin: str
    type: str
    short_name: str | None = None


class Position(BaseModel):
    """One held position: instrument quantity, current quote and entry quotes."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    id: int
    isin: str
    depot_id: str
    quote_time: datetime
    quote: Decimal
    quantity: Decimal
    available_quantity: Decimal
    avg_entry_quote: Decimal
    avg_entry_quote_edited: Decimal | None = None
    avg_entry_quote_original: Decimal
    asset_name: str
    matched_import: str | None = None
    entry_quote_fifo: Decimal
    entry_quote_fifo_date_time: datetime
    entry_quote_fifo_info_marker: str | None = None
    # only ever observed as []; item shape unverified
    order_execution_types_for_protection: list[str]
    entry_quote: Decimal


class PositionsPage(BaseModel):
    """Response envelope of GET /api/trading/positions."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    # the broker's key is "list" — shadowing the builtin is not valid typing
    positions: list[Position] = Field(alias="list", alias_priority=2)
    has_next: bool
    instruments: list[PositionInstrument]
