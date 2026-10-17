"""Browser-based login (Playwright).

shortcut: login runs through a real browser because the Cognito SRP flow is
wrapped in AWS WAF's JS challenge (aws-waf-token is set by JS, never by
Set-Cookie — see docs/explorations/login.md). Port to plain httpx SRP
(`/apic/authc`) once the WAF token proves optional.
"""

import time

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import BrowserContext, sync_playwright

from zero_api.auth.models import AuthSession
from zero_api.core.exceptions import AuthenticationError

_BASE = "https://mein.finanzen-zero.net"


def browser_login(email: str, password: str, *, headless: bool = True, timeout: float = 60.0) -> AuthSession:
    """Log in with email + password in a real browser and return the session.

    Waits until the app has stored the Cognito tokens (``__i`` cookie), then
    transfers the whole cookie jar (``__i``, ``__r``, XSRF-TOKEN, WAF token)
    into an in-memory AuthSession.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        try:
            context = browser.new_context()
            page = context.new_page()
            page.goto(f"{_BASE}/login", wait_until="networkidle", timeout=timeout * 1000)
            page.fill("#userNameId", email)
            page.fill("#passwordId", password)
            page.click("button[type=submit]")
            _wait_for_id_token(context, timeout)
            raw_cookies = context.cookies(_BASE)
        except PlaywrightTimeoutError as exc:
            raise AuthenticationError(f"login page did not load: {exc}") from exc
        finally:
            browser.close()
    return AuthSession.from_cookies({c["name"]: c["value"] for c in raw_cookies})


def _wait_for_id_token(context: BrowserContext, timeout: float) -> None:
    """Poll the cookie jar until the SPA has persisted the IdToken after login."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(c["name"] == "__i" for c in context.cookies(_BASE)):
            return
        time.sleep(0.5)
    # no __i within the timeout: wrong credentials, WAF block or flow change
    raise AuthenticationError("login did not produce a session (wrong credentials or challenge failed)")
