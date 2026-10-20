"""Ordering slice: place, list and cancel orders.

Endpoints from docs/explorations/order.md and order_limit.md. Not part of the
flow (yet): POST /apic/exante — the mandatory ex-ante cost document needs an
unattributed ``x-gb-authorization`` JWT (order.md open question 6).
"""

from zero_api.core.http import AppHttp
from zero_api.ordering.models import Order, OrderRequest, OrdersPage
from zero_api.sessiontan.api import SessionTanApi
from zero_api.sessiontan.models import EnrolledDevice


def _success_order(data: object, action: str) -> object:
    """The success envelope {"status":"success","order":{...}}; anything else
    (broker drift) fails loudly instead of pretending success."""
    if not isinstance(data, dict) or data.get("status") != "success":
        raise ValueError(f"unexpected response {action}: {data!r}")
    return data["order"]


class OrderingApi:
    def __init__(self, http: AppHttp, sessiontan: SessionTanApi | None = None) -> None:
        self._http = http
        self._sessiontan = sessiontan

    def list(self, status: str = "Open", *, page: int = 0, size: int = 1000) -> OrdersPage:
        """List orders by status (observed: "Open"; other values unverified).

        The overview page calls this on load and after changes; an empty list
        comes back as {"list": [], "hasNext": false}.
        """
        data = self._http.get_json(
            "/api/trading/orders",
            params={
                "customerId": self._http.customer_id,
                "status": status,
                "page": str(page),
                "size": str(size),
            },
        )
        return OrdersPage.model_validate(data)

    def place(self, order: OrderRequest, *, device: str | EnrolledDevice | None = None) -> Order:
        """Place an order (after the site's own /verify precheck with the same
        payload) and return the created order object with status "Created".

        Auto-unlocks the 2FA order-execution session first when locked
        (order_2fa.md), pushing to ``device`` if given, else the configured
        default device (client.sessiontan.default_device), else the single
        enrolled device; with several enrolled and none specified, place()
        cannot choose and refuses — unlock first (client.sessiontan.unlock).
        """
        payload = order.model_dump(mode="json", by_alias=True)
        payload["customer"] = self._http.customer_id
        self._ensure_unlocked(device)
        self._http.post_json("/api/trading/order/verify", json=payload)
        data = self._http.post_json("/api/trading/order", json=payload)
        return Order.model_validate(_success_order(data, "placing"))

    def cancel(self, order_id: str, *, device: str | EnrolledDevice | None = None) -> Order:
        """Delete (cancel) an order by its string orderId. Like place(), it
        demands the 2FA session unlock (order_2fa.md; the earlier "no 2FA
        needed" note in order_limit.md only observed an already-unlocked
        session), auto-unlocking with the same ``device`` resolution.
        Returns the order in a transition state such as
        "Open_CreatedCancel"; the final status was not observed."""
        self._ensure_unlocked(device)
        data = self._http.post_json(
            "/api/trading/order/cancel",
            json={"customer": self._http.customer_id, "orderId": order_id},
        )
        return Order.model_validate(_success_order(data, f"cancelling {order_id}"))

    def _ensure_unlocked(self, device: str | EnrolledDevice | None = None) -> None:
        """Probe the 2FA order-execution unlock and auto-unlock when locked
        (order_2fa.md; the locked-order rejection itself was never captured)."""
        if self._sessiontan is not None and not self._sessiontan.is_authorized():
            self._sessiontan.unlock(device)
