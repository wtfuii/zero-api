"""Exceptions raised by the library."""


class ZeroApiError(Exception):
    """Base class of all zero-api errors."""


class AuthenticationError(ZeroApiError):
    """Login failed or the session could not be (re-)established."""
