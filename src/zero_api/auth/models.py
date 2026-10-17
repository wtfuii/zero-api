"""Auth slice models: the authenticated session state."""

from __future__ import annotations

import base64
import json
from typing import Any

from pydantic import BaseModel

from zero_api.core.exceptions import AuthenticationError


class AuthSession(BaseModel):
    """A logged-in session: the app's cookie jar plus the customerId."""

    cookies: dict[str, str]
    customer_id: str

    @classmethod
    def from_cookies(cls, cookies: dict[str, str]) -> AuthSession:
        """Build from the app's cookie jar; customerId comes from the IdToken ``__i``."""
        id_token = cookies.get("__i")
        if not id_token:
            raise AuthenticationError("no __i (IdToken) cookie after login")
        return cls(cookies=cookies, customer_id=_customer_id_from_id_token(id_token))


def _customer_id_from_id_token(id_token: str) -> str:
    """Extract the customerId from the IdToken's ``prm`` claim.

    ``prm`` is a JSON string mapping customerId -> account info (see
    docs/explorations/account.md). Note: the Cognito username (``username``
    claim / /api/auth/sync) is NOT the customerId.
    """
    payload = _decode_jwt_payload(id_token)
    prm_raw = payload.get("prm")
    if not isinstance(prm_raw, str):
        raise AuthenticationError("IdToken has no prm claim")
    prm = json.loads(prm_raw)
    if len(prm) != 1:
        raise AuthenticationError(f"expected exactly one customer in IdToken prm claim, got {len(prm)}")
    return str(next(iter(prm)))


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    try:
        payload_b64 = token.split(".")[1]
        decoded = base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4))
        payload = json.loads(decoded)
    except (IndexError, ValueError) as exc:
        raise AuthenticationError("IdToken is not a readable JWT") from exc
    if not isinstance(payload, dict):
        raise AuthenticationError("IdToken payload is not a JSON object")
    return payload
