"""Account slice API calls."""

from zero_api.account.models import MoneyTransactionAccount
from zero_api.core.http import AppHttp


class AccountApi:
    def __init__(self, http: AppHttp) -> None:
        self._http = http

    def get(self) -> MoneyTransactionAccount:
        """Return the money-transaction (Verrechnungskonto) data of the logged-in customer."""
        data = self._http.get_json("/api/moneytransaction/account/", params={"customerId": self._http.customer_id})
        return MoneyTransactionAccount.model_validate(data)
