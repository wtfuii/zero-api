"""Portfolio slice: the held positions (GET /api/trading/positions)."""

from zero_api.core.http import AppHttp
from zero_api.portfolio.models import PositionsPage, Position


class PortfolioApi:
    def __init__(self, http: AppHttp) -> None:
        self._http = http

    def get_positions(self) -> list[Position]:
        """Return the positions currently held in the logged-in customer's depot.

        shortcut: not pageable (no page/size params observed, unlike /api/trading/orders),
        so a very large depot may come back truncated — revisit if hasNext is ever seen
        true (docs/explorations/positions.md).
        """
        data = self._http.get_json(
            "/api/trading/positions",
            params={"customerId": self._http.customer_id, "withProtectionInfo": "true"},
        )
        return PositionsPage.model_validate(data).positions
