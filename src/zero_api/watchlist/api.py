"""Watchlist slice: the user's watchlist (GET/POST/DELETE /api/wl)."""

from zero_api.core.http import AppHttp
from zero_api.watchlist.models import WatchlistEntry


class WatchlistApi:
    def __init__(self, http: AppHttp) -> None:
        self._http = http

    def get(self) -> list[WatchlistEntry]:
        """Return the logged-in customer's watchlist, richest data source (JSON, not the CSV export)."""
        data = self._http.get_json("/api/wl", params={"customerId": self._http.customer_id})
        return [WatchlistEntry.model_validate(entry) for entry in data]

    def add_to_watchlist(self, isin: str) -> WatchlistEntry:
        """Add an instrument (by ISIN) to the watchlist and return the created entry.

        Server prepends it with wlOrder 0; already-on-list behaviour unverified
        (docs/explorations/watchlist_add.md).
        """
        data = self._http.post_json(f"/api/wl/{isin}", params={"customerId": self._http.customer_id}, json={})
        return WatchlistEntry.model_validate(data)

    def remove_from_watchlist(self, isin: str) -> None:
        """Remove an instrument (by ISIN) from the watchlist. Keyed by ISIN, not item id.

        Success body is the JSON literal ``true``; anything else (error cases —
        ISIN not on list etc. — unverified) fails loudly instead of pretending
        success (docs/explorations/watchlist_remove.md).
        """
        data = self._http.delete_json(f"/api/wl/{isin}", params={"customerId": self._http.customer_id})
        if data is not True:
            raise ValueError(f"unexpected response removing {isin} from watchlist: {data!r}")
