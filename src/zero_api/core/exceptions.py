"""Exceptions raised by the library."""


class ZeroApiError(Exception):
    """Base class of all zero-api errors."""


class AuthenticationError(ZeroApiError):
    """Login failed or the session could not be (re-)established."""


class TwoFactorError(ZeroApiError):
    """Order-execution 2FA (session TAN / app push) could not be completed,
    e.g. the push was declined/unknown state, timed out, or no single enrolled
    device could be picked."""


class BrokerApiError(ZeroApiError):
    """Broker answered non-2xx with its apiError body ({type:"apiError", ...})
    — e.g. a rejected order."""

    def __init__(self, status: int, message: str, error_code: str | None, errors: list[str]) -> None:
        self.status = status
        self.message = message
        self.error_code = error_code
        self.errors = errors
        super().__init__(f"{status} {message} (errorCode={error_code!r})")
