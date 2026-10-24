"""Tests for the 2FA order-execution unlock (sessiontan slice), run against
recorded traffic (docs/explorations/order_2fa.md, order_2fa.capture.json)."""

import json
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import httpx
import pytest

from zero_api import ZeroClient
from zero_api.auth.models import AuthSession
from zero_api.core.exceptions import TwoFactorError
from zero_api.ordering.models import ExecType, OrderRequest

_SESSION = AuthSession(cookies={"__i": "fake-token", "XSRF-TOKEN": "fake-xsrf"}, customer_id="1234567")

# Captured text/plain literals (order_2fa.capture.json).
_LOCKED = "false"
_AUTHORIZED = "true"
_HANDLE = "AB12CD34EF56GH78"  # recorded shape: 16-char uppercase alphanumeric
_PENDING, _ACCEPTED = "AUTHORIZATION_INITIAL", "AUTHORIZATION_USER_ACCEPTED"


class FakeBroker:
    """Stateful stub: each route is a queue of response bodies (last repeats)."""

    def __init__(self, routes: dict[tuple[str, str], list[str]]) -> None:
        self._routes = list(routes.items())  # insertion order = match priority
        self.requests: list[tuple[str, str, str, str]] = []  # method, path, content-type, body

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/version.info":
            return httpx.Response(200, text="broken")  # no build header, harmless
        for (method, substring), queue in self._routes:
            if request.method == method and substring in request.url.path:
                body = queue[-1] if len(queue) == 1 else queue.pop(0)
                self.requests.append((
                    request.method,
                    str(request.url).removeprefix("https://mein.finanzen-zero.net"),
                    request.headers.get("content-type", ""),
                    request.content.decode(),
                ))
                return httpx.Response(200, text=body)
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")


def _client(broker: FakeBroker) -> ZeroClient:
    return ZeroClient(
        email="x@example.com",
        password="secret",
        login=lambda: _SESSION,
        http=httpx.Client(base_url="https://mein.finanzen-zero.net", transport=httpx.MockTransport(broker.handle)),
    )


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("zero_api.sessiontan.api.sleep", lambda seconds: None)


def _devices(load_fixture: Callable[[str, str], Any], name: str) -> str:
    return json.dumps(load_fixture("sessiontan", name))


class TestEnrolledDevices:
    def test_parses_recorded_payload(self, load_fixture: Callable[[str, str], Any]) -> None:
        broker = FakeBroker({("GET", "enrolleddevices"): [_devices(load_fixture, "enrolled_devices")]})
        devices = _client(broker).sessiontan.enrolled_devices()
        assert [(d.serial, d.customer_device_name) for d in devices] == [
            ("<device-serial-1>", "iPhone 13 mini"),
            ("<device-serial-2>", "iPhone 16 Pro"),
        ]
        # nanosecond-precision recorded timestamps parse (truncated to µs)
        assert str(devices[1].enrolled) == "2026-10-17 21:54:01.666378+00:00"


class TestIsAuthorized:
    @pytest.mark.parametrize("body, expected", [(_LOCKED, False), (_AUTHORIZED, True)])
    def test_recorded_bare_literals(self, body: str, expected: bool) -> None:
        broker = FakeBroker({("GET", "isauthorized"): [body]})
        assert _client(broker).sessiontan.is_authorized() is expected

    def test_unexpected_body_fails_loudly(self) -> None:
        broker = FakeBroker({("GET", "isauthorized"): ["null"]})
        with pytest.raises(TwoFactorError, match="isauthorized"):
            _client(broker).sessiontan.is_authorized()


class TestUnlock:
    def test_full_flow_single_device_auto_picked(self, load_fixture: Callable[[str, str], Any]) -> None:
        devices_single = _devices(load_fixture, "enrolled_devices_single")
        broker = FakeBroker({
            ("GET", "enrolleddevices"): [devices_single],
            ("POST", "requestauth"): [_HANDLE],
            ("GET", "authstate"): [_PENDING, _PENDING, _ACCEPTED],
            ("POST", "verifyauth"): [_AUTHORIZED],
        })
        _client(broker).sessiontan.unlock()  # blocks until approved, then verifies
        assert broker.requests == [
            ("GET", "/api/appauth/enrolleddevices?customerId=1234567", "", ""),
            ("POST", "/api/sessiontan/requestauth?customerId=1234567", "text/plain", "<device-serial-2>"),
            ("GET", f"/api/appauth/authstate/{_HANDLE}", "", ""),
            ("GET", f"/api/appauth/authstate/{_HANDLE}", "", ""),
            ("GET", f"/api/appauth/authstate/{_HANDLE}", "", ""),
            ("POST", "/api/sessiontan/verifyauth?customerId=1234567", "text/plain", _HANDLE),
        ]

    def test_multiple_devices_without_choice_is_refused(self, load_fixture: Callable[[str, str], Any]) -> None:
        broker = FakeBroker({("GET", "enrolleddevices"): [_devices(load_fixture, "enrolled_devices")]})
        with pytest.raises(TwoFactorError, match="2 enrolled devices"):
            _client(broker).sessiontan.unlock()
        assert broker.requests == [("GET", "/api/appauth/enrolleddevices?customerId=1234567", "", "")]  # no push sent

    def test_default_device_resolves_multiple_devices(self, load_fixture: Callable[[str, str], Any]) -> None:
        broker = FakeBroker({
            ("POST", "requestauth"): [_HANDLE],
            ("GET", "authstate"): [_ACCEPTED],
            ("POST", "verifyauth"): [_AUTHORIZED],
        })
        client = _client(broker)
        client.sessiontan.default_device = "<device-serial-1>"
        client.sessiontan.unlock()  # default device, no listing needed
        assert broker.requests[0] == (
            "POST", "/api/sessiontan/requestauth?customerId=1234567", "text/plain", "<device-serial-1>"
        )

    def test_default_device_from_client_constructor(self) -> None:
        broker = FakeBroker({
            ("POST", "requestauth"): [_HANDLE],
            ("GET", "authstate"): [_ACCEPTED],
            ("POST", "verifyauth"): [_AUTHORIZED],
        })
        client = ZeroClient(
            email="x@example.com",
            password="secret",
            login=lambda: _SESSION,
            http=httpx.Client(base_url="https://mein.finanzen-zero.net", transport=httpx.MockTransport(broker.handle)),
            default_device="<device-serial-1>",
        )
        client.sessiontan.unlock()
        assert broker.requests[0] == (
            "POST", "/api/sessiontan/requestauth?customerId=1234567", "text/plain", "<device-serial-1>"
        )

    def test_explicit_device_skips_listing(self) -> None:
        broker = FakeBroker({
            ("POST", "requestauth"): [_HANDLE],
            ("GET", "authstate"): [_ACCEPTED],
            ("POST", "verifyauth"): [_AUTHORIZED],
        })
        _client(broker).sessiontan.unlock("<device-serial-2>")
        assert broker.requests[0] == (
            "POST", "/api/sessiontan/requestauth?customerId=1234567", "text/plain", "<device-serial-2>"
        )

    def test_timeout_raises(self) -> None:
        broker = FakeBroker({
            ("POST", "requestauth"): [_HANDLE],
            ("GET", "authstate"): [_PENDING],
        })
        with pytest.raises(TwoFactorError, match="not approved"):
            _client(broker).sessiontan.unlock("<device-serial-2>", timeout=-1.0)

    def test_unknown_state_fails_loudly(self) -> None:
        # decline/expiry state names were never captured (order_2fa.md open
        # question 2) — a guess would silently misinterpret the real ones.
        broker = FakeBroker({
            ("POST", "requestauth"): [_HANDLE],
            ("GET", "authstate"): [_PENDING, "AUTHORIZATION_USER_DECLINED"],
        })
        with pytest.raises(TwoFactorError, match="AUTHORIZATION_USER_DECLINED"):
            _client(broker).sessiontan.unlock("<device-serial-2>")


class TestOrderingIntegration:
    def _order(self) -> OrderRequest:
        return OrderRequest(
            isin="DE0007030009", direction="buy", exec_type=ExecType.MARKET,
            quantity=Decimal(1), mic_code="MUNC",
        )

    def _routes(self, load_fixture: Callable[[str, str], Any], authorized: str) -> dict[tuple[str, str], list[str]]:
        placed = json.dumps(load_fixture("ordering", "order_placed"))
        return {
            ("GET", "isauthorized"): [authorized],
            ("GET", "enrolleddevices"): [_devices(load_fixture, "enrolled_devices_single")],
            ("POST", "requestauth"): [_HANDLE],
            ("GET", "authstate"): [_PENDING, _ACCEPTED],
            ("POST", "verifyauth"): [_AUTHORIZED],
            ("POST", "order/verify"): [""],
            ("POST", "exante"): [json.dumps(load_fixture("ordering", "exante"))],
            ("POST", "/api/trading/order"): [placed],
        }

    def test_place_unlocks_locked_session_first(self, load_fixture: Callable[[str, str], Any]) -> None:
        broker = FakeBroker(self._routes(load_fixture, _LOCKED))
        order = _client(broker).orders.place(self._order())
        assert order.status == "Created"
        assert [(m, p) for m, p, _, _ in broker.requests] == [
            ("GET", "/api/sessiontan/isauthorized?customerId=1234567"),
            ("GET", "/api/appauth/enrolleddevices?customerId=1234567"),
            ("POST", "/api/sessiontan/requestauth?customerId=1234567"),
            ("GET", f"/api/appauth/authstate/{_HANDLE}"),
            ("GET", f"/api/appauth/authstate/{_HANDLE}"),
            ("POST", "/api/sessiontan/verifyauth?customerId=1234567"),
            ("POST", "/api/trading/order/verify"),
            ("POST", "/apic/exante"),
            ("POST", "/api/trading/order"),
        ]

    def test_place_on_authorized_session_skips_2fa(self, load_fixture: Callable[[str, str], Any]) -> None:
        broker = FakeBroker(self._routes(load_fixture, _AUTHORIZED))
        _client(broker).orders.place(self._order())
        # only the probe, then the three order-flow calls — no push flow
        assert [(m, p) for m, p, _, _ in broker.requests] == [
            ("GET", "/api/sessiontan/isauthorized?customerId=1234567"),
            ("POST", "/api/trading/order/verify"),
            ("POST", "/apic/exante"),
            ("POST", "/api/trading/order"),
        ]

    def test_cancel_unlocks_locked_session_first(self, load_fixture: Callable[[str, str], Any]) -> None:
        routes = self._routes(load_fixture, _LOCKED)
        routes[("POST", "/api/trading/order")] = []  # unused here; cancel must match first
        routes[("POST", "order/cancel")] = [json.dumps(load_fixture("ordering", "order_cancelled"))]
        broker = FakeBroker({k: v for k, v in routes.items() if v})
        _client(broker).orders.cancel("GBRd5d801c6318847aea56587e4deb8afdb")
        assert [(m, p) for m, p, _, _ in broker.requests] == [
            ("GET", "/api/sessiontan/isauthorized?customerId=1234567"),
            ("GET", "/api/appauth/enrolleddevices?customerId=1234567"),
            ("POST", "/api/sessiontan/requestauth?customerId=1234567"),
            ("GET", f"/api/appauth/authstate/{_HANDLE}"),
            ("GET", f"/api/appauth/authstate/{_HANDLE}"),
            ("POST", "/api/sessiontan/verifyauth?customerId=1234567"),
            ("POST", "/api/trading/order/cancel"),
        ]

    def test_place_unlocks_via_default_device_when_several_enrolled(
        self, load_fixture: Callable[[str, str], Any]
    ) -> None:
        broker = FakeBroker(self._routes(load_fixture, _LOCKED))
        broker._routes = [r for r in broker._routes if r[0][1] != "enrolleddevices"]
        client = _client(broker)
        client.sessiontan.default_device = "<device-serial-2>"
        client.orders.place(self._order())
        # default device used directly — no device listing, no refusal
        assert any(
            m == "POST" and p == "/api/sessiontan/requestauth?customerId=1234567" and b == "<device-serial-2>"
            for m, p, _, b in broker.requests
        )
        assert not any("enrolleddevices" in p for _, p, _, _ in broker.requests)

    def test_place_unlocks_via_explicit_device_when_several_enrolled(
        self, load_fixture: Callable[[str, str], Any]
    ) -> None:
        broker = FakeBroker(self._routes(load_fixture, _LOCKED))
        broker._routes = [r for r in broker._routes if r[0][1] != "enrolleddevices"]
        client = _client(broker)  # no default_device — only the explicit choice
        client.orders.place(self._order(), device="<device-serial-1>")
        assert any(
            m == "POST" and p == "/api/sessiontan/requestauth?customerId=1234567" and b == "<device-serial-1>"
            for m, p, _, b in broker.requests
        )
        assert not any("enrolleddevices" in p for _, p, _, _ in broker.requests)
