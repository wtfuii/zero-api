"""Shared HTTP transport: cookie session against mein.finanzen-zero.net with
automatic (re-)login. All slices send their requests through AppHttp, so the
"authenticate once, re-login when rejected" flow lives in exactly one place.
"""

import base64
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from time import time

import httpx

from zero_api.auth.api import http_login, http_refresh
from zero_api.auth.models import AuthSession
from zero_api.core.exceptions import AuthenticationError, BrokerApiError

BASE_URL = "https://mein.finanzen-zero.net"

# Chrome UA as captured (2026-10-16, docs/explorations/login.md).
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"
)


class AppHttp:
    """Cookie-based HTTP session for the ZERO app APIs.

    Sends no Authorization header — auth is purely the session cookies
    (``__i``, ``__r``, see login.md). On 401/403 the session is re-established
    once and the request retried.
    """

    def __init__(
        self,
        http: httpx.Client | None = None,
        relogin: Callable[[], AuthSession] | None = None,
        email: str | None = None,
        password: str | None = None,
    ) -> None:
        self._http = http or httpx.Client(base_url=BASE_URL, headers=_static_headers())
        self._relogin = relogin
        # credentials used only when no explicit relogin callable is given
        self._credentials = (email, password) if email is not None and password is not None else None
        self._session: AuthSession | None = None
        self._fz_build: str | None = None

    @property
    def customer_id(self) -> str:
        if self._session is None:
            self._login()
        assert self._session is not None
        return self._session.customer_id

    def apply_session(self, session: AuthSession) -> None:
        """Install an authenticated session's cookies, replacing any previous ones."""
        self._session = session
        self._http.cookies.clear()
        for name, value in session.cookies.items():
            self._http.cookies.set(name, value, domain="mein.finanzen-zero.net")

    def get_json(self, path: str, params: dict[str, str] | None = None) -> Any:
        response = self._request("GET", path, params)
        response.raise_for_status()
        return _json_or_none(response)

    def get_text(self, path: str, params: dict[str, str] | None = None) -> str:
        """GET whose body is a plain-text literal (e.g. sessiontan probes, order_2fa.md)."""
        response = self._request("GET", path, params)
        response.raise_for_status()
        return response.text

    def post_json(self, path: str, params: dict[str, str] | None = None, json: Any = None) -> Any:
        response = self._request("POST", path, params, json)
        response.raise_for_status()
        return _json_or_none(response)

    def post_text(self, path: str, params: dict[str, str] | None = None, content: str = "") -> str:
        """POST with a text/plain body and a plain-text response (order_2fa.md)."""
        response = self._request("POST", path, params, content=content, text_body=True)
        response.raise_for_status()
        return response.text

    def delete_json(self, path: str, params: dict[str, str] | None = None) -> Any:
        response = self._request("DELETE", path, params)
        response.raise_for_status()
        return _json_or_none(response)

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, str] | None,
        json: Any = None,
        content: str | None = None,
        text_body: bool = False,
    ) -> httpx.Response:
        self._ensure_session()
        response = self._http.request(
            method, path, params=params, json=json, content=content,
            headers={**self._auth_headers(), **({"content-type": "text/plain"} if text_body else {})},
        )
        if response.status_code in (401, 403) and self._can_reauth():
            # session rejected/expired → re-authenticate once, then retry
            self._ensure_session(force=True)
            response = self._http.request(
                method, path, params=params, json=json, content=content,
                headers={**self._auth_headers(), **({"content-type": "text/plain"} if text_body else {})},
            )
        if response.is_error:
            self._raise_api_error(response)
        return response

    @staticmethod
    def _raise_api_error(response: httpx.Response) -> None:
        """Non-2xx with the broker's apiError body ({type:"apiError", ...}) →
        BrokerApiError; any other error shape falls through to raise_for_status."""
        try:
            body = response.json()
        except ValueError:
            return
        if isinstance(body, dict) and body.get("type") == "apiError":
            raise BrokerApiError(
                status=response.status_code,
                message=body.get("message", ""),
                error_code=body.get("errorCode"),
                errors=body.get("errors") or [],
            )

    def _login(self) -> None:
        self.apply_session(self._reauth())

    def _ensure_session(self, *, force: bool = False) -> None:
        """Have a session whose IdToken is still valid; establish/refresh otherwise."""
        if self._session is not None and not force and not self._session.id_token_expired():
            return
        self.apply_session(self._reauth())

    def _can_reauth(self) -> bool:
        return (
            self._relogin is not None
            or self._credentials is not None
            or (self._session is not None and "__r" in self._session.cookies)
        )

    def _reauth(self) -> AuthSession:
        """New session: refresh from ``__r`` when possible, else full login."""
        if self._session is not None and "__r" in self._session.cookies:
            try:
                return http_refresh(self._http, self._session)
            except (AuthenticationError, httpx.HTTPError):
                pass  # refresh token dead/rejected → fall through to a full login
        if self._relogin is not None:
            return self._relogin()
        if self._credentials is not None:
            # http-first login (SRP via /apic/authc) on this same session —
            # the browser fallback was retired once SRP existed (waf_probe.md).
            return http_login(self._http, *self._credentials)
        raise AuthenticationError("not authenticated and no login method configured")

    def _auth_headers(self) -> dict[str, str]:
        headers = {"x-xsrf-token": self._http.cookies.get("XSRF-TOKEN", "") or ""}
        build = self._fz_build_header()
        if build:
            headers["x-fz-build"] = build
        return headers

    def _fz_build_header(self) -> str:
        """x-fz-build = the SPA's git sha, fetched once from the public /version.info
        (base64 release string, sha is the last ``_`` segment) so a broker redeploy
        never leaves us sending a stale constant. If that source breaks, we send no
        build header rather than failing — nothing was verified about the server
        actually requiring it.
        """
        if self._fz_build is None:
            self._fz_build = ""
            try:
                info = self._http.get("/version.info").text
                sha = base64.b64decode(info).decode().strip().split("_")[-1]
                if re.fullmatch(r"[0-9a-f]{40}", sha):
                    self._fz_build = sha
            except (httpx.HTTPError, ValueError):
                pass
        return self._fz_build


def _json_or_none(response: httpx.Response) -> Any:
    """Parse the body, or None when empty — e.g. /api/trading/order/verify
    answers 200 with an empty body (docs/explorations/order.md)."""
    return response.json() if response.content else None


def _static_headers() -> dict[str, str]:
    return {
        "user-agent": _USER_AGENT,
        "x-fz-appversion": "0.0.0",
        "x-fz-client": "finzero",
        "x-fz-os": "web",
        "x-tabid": f"{int(time() * 1000)}#{datetime.now(UTC).strftime('%Y-%m-%d')}",
    }
