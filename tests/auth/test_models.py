"""Tests for the auth slice models."""

import base64
import json
import time

import pytest

from zero_api.auth.models import AuthSession
from zero_api.core.exceptions import AuthenticationError


def _b64(obj: object) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()


def _id_token(customer_id: str) -> str:
    prm = json.dumps({customer_id: {"acc": f"{customer_id}007", "owner": True}})
    return f"{_b64({'alg': 'RS256'})}.{_b64({'prm': prm, 'aud': 'zero'})}.signature"


class TestAuthSessionFromCookies:
    def test_extracts_customer_id_from_id_token(self) -> None:
        session = AuthSession.from_cookies({"__i": _id_token("4917975"), "__r": "refresh", "XSRF-TOKEN": "xsrf"})
        assert session.customer_id == "4917975"
        assert session.cookies["__r"] == "refresh"

    def test_missing_id_token_raises(self) -> None:
        with pytest.raises(AuthenticationError, match="__i"):
            AuthSession.from_cookies({"XSRF-TOKEN": "xsrf"})

    def test_unreadable_token_raises(self) -> None:
        with pytest.raises(AuthenticationError, match="JWT"):
            AuthSession.from_cookies({"__i": "not-a-jwt"})

    def test_multi_customer_prm_raises(self) -> None:
        prm = json.dumps({"1": {}, "2": {}})
        token = f"{_b64({'alg': 'RS256'})}.{_b64({'prm': prm})}.sig"
        with pytest.raises(AuthenticationError, match="prm"):
            AuthSession.from_cookies({"__i": token})


class TestIdTokenExpired:
    def test_expired_token_is_true(self) -> None:
        token = f"{_b64({'alg': 'RS256'})}.{_b64({'prm': '{}', 'exp': time.time() - 1})}.sig"
        session = AuthSession(cookies={"__i": token}, customer_id="1")
        assert session.id_token_expired() is True

    def test_future_expiry_is_false(self) -> None:
        token = f"{_b64({'alg': 'RS256'})}.{_b64({'prm': '{}', 'exp': time.time() + 3600})}.sig"
        session = AuthSession(cookies={"__i": token}, customer_id="1")
        assert session.id_token_expired() is False

    def test_near_expiry_within_margin_is_true(self) -> None:
        token = f"{_b64({'alg': 'RS256'})}.{_b64({'prm': '{}', 'exp': time.time() + 10})}.sig"
        session = AuthSession(cookies={"__i": token}, customer_id="1")
        assert session.id_token_expired(margin_seconds=30) is True

    def test_opaque_token_is_not_expired(self) -> None:
        # fake/unparseable __i: let the server decide via the 401 path
        session = AuthSession(cookies={"__i": "fake-token"}, customer_id="1")
        assert session.id_token_expired() is False
