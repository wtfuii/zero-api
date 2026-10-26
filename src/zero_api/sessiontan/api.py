"""2FA order-execution unlock (session TAN / app push) — the state machine
behind the order screen.

The flow introduces no new tokens: it is server-side session state keyed by a
short-lived auth handle. All bodies on these endpoints are text/plain literals.
"""

from time import monotonic, sleep

from zero_api.core.exceptions import TwoFactorError
from zero_api.core.http import AppHttp
from zero_api.sessiontan.models import AuthState, EnrolledDevice


class SessionTanApi:
    def __init__(self, http: AppHttp, default_device: str | EnrolledDevice | None = None) -> None:
        self._http = http
        # Used by unlock() (and thus place()'s auto-unlock) when no device is
        # passed — the only way a multi-device account can auto-unlock.
        self.default_device = default_device

    def is_authorized(self) -> bool:
        """Is this web session currently unlocked for order execution?"""
        body = self._http.get_text(
            "/api/sessiontan/isauthorized", params=self._customer()
        ).strip()
        if body not in ("true", "false"):
            raise TwoFactorError(f"unexpected isauthorized response: {body!r}")
        return body == "true"

    def enrolled_devices(self) -> list[EnrolledDevice]:
        """Devices that may receive the 2FA push (the device-selection list)."""
        data = self._http.get_json("/api/appauth/enrolleddevices", params=self._customer())
        return [EnrolledDevice.model_validate(entry) for entry in data]

    def request_auth(self, device_serial: str) -> str:
        """Send the 2FA push to a device; returns the pending auth handle."""
        return self._http.post_text(
            "/api/sessiontan/requestauth", params=self._customer(), content=device_serial
        )

    def auth_state(self, auth_handle: str) -> AuthState:
        """Current decision state of a pending push (poll while pending)."""
        raw = self._http.get_text(f"/api/appauth/authstate/{auth_handle}").strip()
        try:
            return AuthState(raw)
        except ValueError:
            raise TwoFactorError(f"unknown 2FA auth state {raw!r}") from None

    def abort(self, auth_handle: str) -> None:
        """Abort a pending push (what the UI does on dismiss)."""
        response = self._http.post_text("/api/appauth/abortauth", content=auth_handle)
        self._text_true(response, "abortauth")

    def unlock(
        self,
        device: str | EnrolledDevice | None = None,
        *,
        timeout: float = 60.0,
        poll_interval: float = 2.0,
    ) -> None:
        """Request a 2FA push and block until the user approves it on the phone.

        ``device`` is a serial, an EnrolledDevice, or None to use the configured
        ``default_device``, then the only enrolled device. With several enrolled
        and no default, we refuse to pick — set ``client.sessiontan.default_device``
        or pass one explicitly. Then verify, leaving the cookie session unlocked
        server-side.
        """
        serial = device.serial if isinstance(device, EnrolledDevice) else device
        if serial is None:
            serial = (
                self.default_device.serial
                if isinstance(self.default_device, EnrolledDevice)
                else self.default_device
            )
        if serial is None:
            devices = self.enrolled_devices()
            if len(devices) != 1:
                raise TwoFactorError(
                    f"{len(devices)} enrolled devices — set client.sessiontan.default_device "
                    "or pass the device to unlock (client.sessiontan.unlock(<serial>))"
                )
            serial = devices[0].serial
        handle = self.request_auth(serial)
        deadline = monotonic() + timeout
        while True:
            state = self.auth_state(handle)
            if state is AuthState.ACCEPTED:
                self._verify(handle)
                return
            if monotonic() >= deadline:
                raise TwoFactorError(f"2FA push not approved within {timeout}s")
            sleep(poll_interval)

    def _verify(self, auth_handle: str) -> None:
        """Finalize the unlock after the push was accepted."""
        response = self._http.post_text(
            "/api/sessiontan/verifyauth", params=self._customer(), content=auth_handle
        )
        self._text_true(response, "verifyauth")

    def _customer(self) -> dict[str, str]:
        return {"customerId": self._http.customer_id}

    @staticmethod
    def _text_true(body: str, call: str) -> None:
        if body.strip() != "true":
            raise TwoFactorError(f"unexpected {call} response: {body.strip()!r}")
