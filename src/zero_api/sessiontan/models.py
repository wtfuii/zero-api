"""Models for the 2FA order-execution unlock (session TAN / app push) —
see docs/explorations/order_2fa.md."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class EnrolledDevice(BaseModel):
    """One device (finanzen.net ZERO app) that may receive the 2FA push,
    from GET /api/appauth/enrolleddevices."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    serial: str  # identifies the device to /api/sessiontan/requestauth
    customer_device_name: str  # e.g. "iPhone 16 Pro"
    device_manufacturer: str
    device_model: str
    enrolled: datetime


class AuthState(StrEnum):
    """Wire values of GET /api/appauth/authstate/<handle>. Only these two were
    captured (order_2fa.md open question 2) — declined/expired states are
    deliberately absent so an unknown value fails loudly instead of guessing."""

    PENDING = "AUTHORIZATION_INITIAL"
    ACCEPTED = "AUTHORIZATION_USER_ACCEPTED"
