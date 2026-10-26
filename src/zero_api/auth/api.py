"""HTTP-first login: Cognito SRP through the app's own /apic/authc proxy.

Mimics the SPA flow's request order; no browser and no aws-waf-token needed.
The GetUser call after login is SPA user-info housekeeping and is skipped.
"""

import uuid
from typing import Any

import httpx

from zero_api.auth.models import AuthSession
from zero_api.auth.srp import CognitoSrp
from zero_api.core.exceptions import AuthenticationError

# From the deployed bundle / recorded login traffic.
_CLIENT_ID = "6hcbp28i4mvooqt0edcq5u284s"
_POOL_ID = "eu-central-1_W7ZDh3Al1"

_AUTHC_HEADERS = {
    "content-type": "application/x-amz-json-1.1",
    "x-amz-user-agent": "aws-amplify/5.0.4 api/2.0",  # proven accepted by the proxy
    "cache-control": "no-store",
}


def http_login(http: httpx.Client, email: str, password: str) -> AuthSession:
    """Full email + password login over plain HTTP, on the client's own session.

    InitiateAuth (SRP round 1) → RespondToAuthChallenge (round 2) → the two
    POST /api/auth/save calls that turn RefreshToken/IdToken into the ``__r``
    and ``__i`` cookies. Returns the resulting session.
    """
    srp = CognitoSrp(email, password, _POOL_ID)
    init = _authc(
        http,
        "InitiateAuth",
        {
            "AuthFlow": "USER_SRP_AUTH",
            "ClientId": _CLIENT_ID,
            "AuthParameters": {"USERNAME": email, "SRP_A": srp.srp_a_hex},
            "ClientMetadata": {},
        },
    )
    if init.get("ChallengeName") != "PASSWORD_VERIFIER":
        raise AuthenticationError(f"unexpected Cognito challenge: {init.get('ChallengeName')!r}")
    verified = _authc(
        http,
        "RespondToAuthChallenge",
        {
            "ChallengeName": "PASSWORD_VERIFIER",
            "ClientId": _CLIENT_ID,
            "ChallengeResponses": srp.challenge_responses(init["ChallengeParameters"]),
            "ClientMetadata": {},
        },
    )
    result = verified.get("AuthenticationResult")
    if not isinstance(result, dict) or not result.get("IdToken") or not result.get("RefreshToken"):
        raise AuthenticationError("login did not return token result")
    _save_token(http, result["RefreshToken"])  # sets __r — recorded before IdToken
    _save_token(http, result["IdToken"])  # sets __i
    return AuthSession.from_cookies({c.name: c.value or "" for c in http.cookies.jar})


def http_refresh(http: httpx.Client, session: AuthSession) -> AuthSession:
    """Cheap single-call re-authentication from the session's refresh token.

    Mimics the SPA's REFRESH_TOKEN_AUTH flow (recorded exchange): no SRP round
    needed, and Cognito does NOT rotate the refresh token — only the IdToken is
    re-saved (``__i``), ``__r`` stays valid.
    """
    refresh_token = session.cookies.get("__r")
    if not refresh_token:
        raise AuthenticationError("no __r (RefreshToken) cookie to refresh the session")
    response = _authc(
        http,
        "InitiateAuth",
        {
            "AuthFlow": "REFRESH_TOKEN_AUTH",
            "ClientId": _CLIENT_ID,
            "AuthParameters": {"REFRESH_TOKEN": refresh_token},
        },
    )
    result = response.get("AuthenticationResult")
    if not isinstance(result, dict) or not result.get("IdToken"):
        raise AuthenticationError("refresh did not return a token result")
    _save_token(http, result["IdToken"])
    return AuthSession.from_cookies({c.name: c.value or "" for c in http.cookies.jar})


def _authc(http: httpx.Client, target: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = http.post(
        "/apic/authc",
        json=payload,
        headers={"x-amz-target": f"AWSCognitoIdentityProviderService.{target}", **_AUTHC_HEADERS},
    )
    try:
        body = response.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict) or not (body.get("ChallengeName") or body.get("AuthenticationResult")):
        raise AuthenticationError(_authc_error(response, body))
    return body


def _authc_error(response: httpx.Response, body: Any) -> str:
    # Loud failure path for an adaptive WAF.
    if any("x-amzn-waf" in name.lower() for name in response.headers):
        return f"blocked by AWS WAF challenge (unexpected): {response.status_code}"
    message = body.get("message", "") if isinstance(body, dict) else ""
    return f"Cognito auth failed: {response.status_code} {message}".strip()


def _save_token(http: httpx.Client, token: str) -> None:
    """POST the raw token as text/plain; the server answers with Set-Cookie."""
    response = http.post(
        "/api/auth/save",
        content=token.encode(),
        headers={"content-type": "text/plain", "x-xsrf-token": _xsrf_token(http)},
    )
    response.raise_for_status()


def _xsrf_token(http: httpx.Client) -> str:
    """The SPA's XSRF-TOKEN cookie is set client-side (no Set-Cookie in the
    captured traffic), so an httpx client self-issues one and sends the
    matching header. shortcut: unverified whether the server cross-checks
    cookie and header — revisit if /api/auth/save starts rejecting."""
    token = http.cookies.get("XSRF-TOKEN")
    if not token:
        token = str(uuid.uuid4())
        http.cookies.set("XSRF-TOKEN", token, domain="mein.finanzen-zero.net", path="/")
    return token
