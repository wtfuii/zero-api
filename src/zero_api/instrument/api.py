"""Instrument slice: instrument master record, detail data and chart series."""

from zero_api.core.http import AppHttp
from zero_api.instrument.models import (
    ChartBar,
    ChartPeriod,
    Instrument,
    InstrumentDetail,
    InstrumentSearchPage,
)


class InstrumentApi:
    def __init__(self, http: AppHttp) -> None:
        self._http = http

    def search(self, query: str, *, size: int = 10) -> InstrumentSearchPage:
        """Typeahead instrument search by name, WKN or ISIN
        (GET /api/instrumentsearch, see instrument.md).

        ``query`` is free text as typed into the app's search box; ``size`` caps
        the returned hits (the SPA asks for 3 — the server-side cap is
        unverified). ``page.stats.hits`` still counts every match per asset
        class, so a capped result set never hides how many hits exist.
        """
        data = self._http.get_json(
            "/api/instrumentsearch", params={"q": query, "size": str(size)}
        )
        return InstrumentSearchPage.model_validate(data)

    def get(self, isin: str) -> Instrument:
        """The instrument master record (GET /api/instrument/{isin}).
        Trading rules, venues, per-venue fees and the latest quotes."""
        return Instrument.model_validate(self._http.get_json(f"/api/instrument/{isin}"))

    def detail(self, isin: str) -> InstrumentDetail:
        """Market data & fundamentals (GET /api/instrument/detail/{isin})."""
        return InstrumentDetail.model_validate(self._http.get_json(f"/api/instrument/detail/{isin}"))

    def chart(
        self,
        isin: str,
        period: ChartPeriod = ChartPeriod.ONE_YEAR,
    ) -> list[ChartBar]:
        """Chart series for one scale (GET /extapis/findata/chart).

        Needs the ``tkIdentifier`` from the detail endpoint, exactly like the
        site: it fetches detail first, then the chart (two GETs per call).
        """
        tk = self.detail(isin).tk_identifier
        data = self._http.get_json(
            "/extapis/findata/chart",
            params={"period": period.value, "instruments": tk},
        )
        return [ChartBar.model_validate(row) for row in data]
