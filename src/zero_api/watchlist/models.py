"""Models for the watchlist.

Shape of GET /api/wl — see docs/explorations/watchlist.md.
``extra="forbid"`` so silent schema drift by the broker fails loudly.
"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class HistoricQuote(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    id: int
    bid: Decimal
    ask: Decimal
    ts: datetime
    isin: str


class Instrument(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    historic_quotes: list[HistoricQuote]
    sparplan: bool
    knocked_out_flag: bool
    endfaellig: bool
    long_name: str
    isin: str
    type: str
    short_name: str | None = None


class WatchlistItem(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    id: int
    wl_order: int
    isin: str
    added: datetime
    ask_when_added: Decimal
    bid_when_added: Decimal
    ask: Decimal
    bid: Decimal
    quote_time: datetime
    item_name: str
    note: str


class WatchlistEntry(BaseModel):
    """One watchlist row: the instrument plus its watchlist metadata."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    instrument: Instrument
    watchlist_item: WatchlistItem
    # only ever null in the capture; populated shape unverified
    instrument_alternative: Instrument | None = None
