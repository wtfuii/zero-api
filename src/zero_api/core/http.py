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

from zero_api.auth.models import AuthSession
from zero_api.core.exceptions import AuthenticationError

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
    ) -> None:
        self._http = http or httpx.Client(base_url=BASE_URL, headers=_static_headers())
        self._relogin = relogin
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
        return response.json()

    def post_json(self, path: str, params: dict[str, str] | None = None, json: Any = None) -> Any:
        response = self._request("POST", path, params, json)
        response.raise_for_status()
        return response.json()

    def delete_json(self, path: str, params: dict[str, str] | None = None) -> Any:
        response = self._request("DELETE", path, params)
        response.raise_for_status()
        return response.json()

    def _request(
        self, method: str, path: str, params: dict[str, str] | None, json: Any = None
    ) -> httpx.Response:
        if self._session is None:
            self._login()
        response = self._http.request(method, path, params=params, json=json, headers=self._auth_headers())
        if response.status_code in (401, 403) and self._relogin is not None:
            # session rejected/expired → re-authenticate once, then retry
            self._login()
            response = self._http.request(method, path, params=params, json=json, headers=self._auth_headers())
        return response

    def _login(self) -> None:
        if self._relogin is None:
            raise AuthenticationError("not authenticated and no login method configured")
        self.apply_session(self._relogin())

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


def _static_headers() -> dict[str, str]:
    return {
        "user-agent": _USER_AGENT,
        "x-fz-appversion": "0.0.0",
        "x-fz-client": "finzero",
        "x-fz-os": "web",
        "x-tabid": f"{int(time() * 1000)}#{datetime.now(UTC).strftime('%Y-%m-%d')}",
    }
