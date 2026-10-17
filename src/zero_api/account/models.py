"""Models for the money-transaction (Verrechnungskonto) account.

Shape of GET /api/moneytransaction/account/ — see docs/explorations/account.md.
``extra="forbid"`` so silent schema drift by the broker fails loudly.
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class MoneyTransactionAccount(BaseModel):
    """Basic info on the current brokerage cash account."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    id: int
    account_nr: str
    customer_id: str
    intraday_amount: Decimal
    intraday_timestamp: datetime
    buying_power: Decimal
    buying_power_timestamp: datetime
    transfer_power: Decimal
    transfer_power_timestamp: datetime
    last_saldo_import_date: date | None = None
    last_imported_saldo: Decimal | None = None
    last_import_match: str | None = None
    account_iban: str
    credit_amount: Decimal | None = None
    credit_amount_timestamp: datetime | None = None
    closing_period: str
    next_closing_date: date
    debit_interest: Decimal
    depot_id: int | None = None
    buying_power_without_credit: Decimal
    transfer_power_without_credit: Decimal
    overdrawn: bool
    transfer_power_blocked: Decimal
