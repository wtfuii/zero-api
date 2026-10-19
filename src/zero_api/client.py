"""Top-level client: one object, one authentication for all slices.

The session (cookies + customerId) is kept in memory; when the broker rejects
it, the client re-authenticates automatically from the stored credentials.
"""

from collections.abc import Callable

import httpx

from zero_api.account.api import AccountApi
from zero_api.auth.browser_login import browser_login
from zero_api.auth.models import AuthSession
from zero_api.core.http import AppHttp
from zero_api.ordering.api import OrderingApi
from zero_api.portfolio.api import PortfolioApi
from zero_api.watchlist.api import WatchlistApi


class ZeroClient:
    """Client for Finanzen.net ZERO.

    Instantiating with email + password is enough: the first API call triggers
    the login, and every rejected session is re-established automatically.
    """

    def __init__(
        self,
        email: str,
        password: str,
        *,
        login: Callable[[], AuthSession] | None = None,
        http: httpx.Client | None = None,
    ) -> None:
        self._http = AppHttp(http=http, relogin=login or (lambda: browser_login(email, password)))
        self.account = AccountApi(self._http)
        self.portfolio = PortfolioApi(self._http)
        self.watchlist = WatchlistApi(self._http)
        self.orders = OrderingApi(self._http)
