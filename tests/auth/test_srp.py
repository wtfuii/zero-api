"""Tests for the Cognito SRP math (auth/srp.py).

The pipeline is locked by fixed-input regression vectors; the derived key was
cross-verified byte-for-byte against pycognito's independent implementation
(``AWSSRP.get_password_authentication_key``) on 2026-10-19.
"""

import base64
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import zero_api.auth.srp as srp_module
from zero_api.auth.srp import CognitoSrp, _now_string, _pad_hex
from zero_api.core.exceptions import AuthenticationError

_POOL_ID = "eu-central-1_W7ZDh3Al1"
_FIXED_RANDOM = bytes([0x11]) * 128  # patched os.urandom → deterministic SRP_A
_FIXED_TIMESTAMP = "Sat Oct 17 00:37:13 UTC 2026"

# Reference values for _FIXED_RANDOM + the login_flow.json challenge, agreed
# with pycognito's implementation.
_SRP_A = (
    "67679d4fea151663de3276050085a1ce5853766f826eac11fa1c80daaca0a3a4d7fd4348e3f4ad478e6b049c41a44b5fa8f19"
    "ad5983c28abfaa9f49b094a85cd6ef47f50ea73adcd254c5f107f2970b5ac8d163698dfc0f57e6e58cb1ac1d868fc8170f0b1"
    "6976ea4879067a90f89659969c61a674966ecc37872550511549df4b2840b21c4f97e4b6bbc5390ceb889db94c51c7038da45"
    "cca2745913e0fbc05cd86d30b18533e07f7933650b8d360a22548e426b9f0b97b9aaa95c654ae5ce38d74c98c05182f2a12e9"
    "df7983dd06d2867fe8a5351f5b2f8998f7292047c5b13ee276832b0b0b6166dcb71c119598ac6b77b91afd7b9dbdeece8aad5"
    "15ebaff3e98a0034945b56af5cd9b390ce0b0180bd88db2d2c25390aacee43ac5576d35000d495b8e2b67f41aa82365ba6a2b"
    "bf7535edf398798a38ff53b0038025ab1723b6910bdba9e54753b7dc38243f54f3d92b3859e22f80d579b7170adc799598749"
    "9764c8ed7d9a2088abd0092ba68e553618bb63368912e373a857c32e03a6c"
)
_SIGNATURE = "6SwpgBjt7WNY+8uuv/x5RvHVpBMqJD4XW4UKsPx2zZk="


@pytest.fixture(autouse=True)
def deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("os.urandom", lambda n: _FIXED_RANDOM)
    monkeypatch.setattr(srp_module, "_now_string", lambda now: _FIXED_TIMESTAMP)


def _challenge() -> dict[str, str]:
    path = Path(__file__).parent / "fixtures" / "login_flow.json"
    fixture: dict[str, Any] = json.loads(path.read_text())
    challenge: dict[str, str] = fixture["challenge"]["ChallengeParameters"]
    return challenge


class TestSrpA:
    def test_matches_reference_implementation(self) -> None:
        srp = CognitoSrp("probe@example.com", "pw", _POOL_ID)
        assert srp.srp_a_hex == _SRP_A


class TestChallengeResponses:
    def test_shape_and_signature(self) -> None:
        srp = CognitoSrp("probe@example.com", "pw", _POOL_ID)
        challenge = _challenge()
        responses = srp.challenge_responses(challenge)
        assert responses["USERNAME"] == challenge["USER_ID_FOR_SRP"]
        assert responses["PASSWORD_CLAIM_SECRET_BLOCK"] == challenge["SECRET_BLOCK"]
        assert responses["TIMESTAMP"] == _FIXED_TIMESTAMP
        assert responses["PASSWORD_CLAIM_SIGNATURE"] == _SIGNATURE

    def test_signature_is_32_base64_bytes(self) -> None:
        srp = CognitoSrp("probe@example.com", "pw", _POOL_ID)
        signature = base64.b64decode(srp.challenge_responses(_challenge())["PASSWORD_CLAIM_SIGNATURE"])
        assert len(signature) == 32

    def test_invalid_srp_b_rejected(self) -> None:
        srp = CognitoSrp("probe@example.com", "pw", _POOL_ID)
        challenge = dict(_challenge(), SRP_B="0")
        with pytest.raises(AuthenticationError, match="SRP_B"):
            srp.challenge_responses(challenge)


class TestTimestampFormat:
    def test_matches_recorded_login_format(self) -> None:
        assert _now_string(datetime(2026, 10, 17, 0, 37, 13, tzinfo=UTC)) == "Sat Oct 17 00:37:13 UTC 2026"

    def test_day_of_month_not_padded(self) -> None:
        # as in the deployed bundle's getNowString (DateHelper), unlike pycognito
        assert _now_string(datetime(2026, 10, 7, 5, 4, 3, tzinfo=UTC)) == "Wed Oct 7 05:04:03 UTC 2026"


class TestPadHex:
    def test_pad_cases(self) -> None:
        # odd length → one zero; leading 8-f → two zeros (JS padHex behaviour)
        assert _pad_hex(0x8) == "08"
        assert _pad_hex(0x89) == "0089"
        assert _pad_hex(0x1) == "01"
        assert _pad_hex(0x7A) == "7a"
