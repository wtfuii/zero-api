"""Cognito custom SRP client math (RFC 5054 + Cognito variants).

Port of amazon-cognito-identity-js (AuthenticationHelper.js + DateHelper.js),
cross-checked against the deployed amplify bundle on mein.finanzen-zero.net
and pycognito's aws_srp.py; see docs/explorations/login.md (open question 1):

- k = SHA256(bytes(padHex(N) + padHex(g)))
- u = SHA256(bytes(padHex(A) + padHex(B)))
- x = SHA256(bytes(padHex(salt) + SHA256(poolName + userIdForSRP + ":" + password)))
- S = (B - k*g^x)^(a + u*x) mod N
- key = HKDF-SHA256(ikm=bytes(padHex(S)), salt=bytes(padHex(u)),
                    info=b"Caldera Derived Key" + 0x01)[:16]
- PASSWORD_CLAIM_SIGNATURE = base64(HMAC-SHA256(key, poolName + userId
                    + secret_block + timestamp))

The TIMESTAMP uses the SPA's ``getNowString`` format, ``Sat Oct 17 00:37:13
UTC 2026`` — hours/minutes/seconds zero-padded, day-of-month NOT padded.
"""

import base64
import hashlib
import hmac
import os
from datetime import UTC, datetime

from zero_api.core.exceptions import AuthenticationError

# RFC 5054, 3072-bit group — the N used by amazon-cognito-identity-js.
_N_HEX = (
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD1"
    "29024E088A67CC74020BBEA63B139B22514A08798E3404DD"
    "EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245"
    "E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED"
    "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3D"
    "C2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F"
    "83655D23DCA3AD961C62F356208552BB9ED529077096966D"
    "670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B"
    "E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9"
    "DE2BCBF6955817183995497CEA956AE515D2261898FA0510"
    "15728E5A8AAAC42DAD33170D04507A33A85521ABDF1CBA64"
    "ECFB850458DBEF0A8AEA71575D060C7DB3970F85A6E1E4C7"
    "ABF5AE8CDB0933D71E8C94E04A25619DCEE3D2261AD2EE6B"
    "F12FFA06D98A0864D87602733EC86A64521F2B18177B200C"
    "BBE117577A615D6C770988C0BAD946E208E24FA074E5AB31"
    "43DB5BFCE0FD108E4B82D120A93AD2CAFFFFFFFFFFFFFFFF"
)
_G_HEX = "2"
_INFO_BITS = b"Caldera Derived Key\x01"

_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _pad_hex(value: int) -> str:
    """JS AuthenticationHelper.padHex: minimal hex, '0'/'00' before odd length
    or when the top digit would read as negative."""
    hex_str = format(value, "x")
    if len(hex_str) % 2 == 1:
        return "0" + hex_str
    if hex_str[0] in "89ABCDEFabcdef":
        return "00" + hex_str
    return hex_str


def _hash_hex(hex_str: str) -> str:
    """JS hexHash: SHA256 over the *bytes* the hex string represents."""
    return hashlib.sha256(bytes.fromhex(hex_str)).hexdigest()


def _now_string(now: datetime) -> str:
    """JS DateHelper.getNowString: ``ddd MMM d HH:mm:ss 'UTC' yyyy``."""
    return (
        f"{_WEEKDAYS[now.weekday()]} {_MONTHS[now.month - 1]} {now.day} "
        f"{now.hour:02d}:{now.minute:02d}:{now.second:02d} UTC {now.year}"
    )


class CognitoSrp:
    """SRP state for one login attempt (fresh ``a``/``A`` per instance).

    Round 1: send ``srp_a_hex`` as ``AuthParameters.SRP_A``. Round 2: feed the
    ``PASSWORD_VERIFIER`` challenge parameters into ``challenge_responses``
    and send the result back as ``ChallengeResponses``.
    """

    def __init__(self, username: str, password: str, pool_id: str) -> None:
        self._pool_name = pool_id.split("_")[1]
        self._password = password
        self._n = int(_N_HEX, 16)
        self._g = int(_G_HEX, 16)
        self._k = int(_hash_hex(_pad_hex(self._n) + _pad_hex(self._g)), 16)
        # JS: 128 random bytes mod N
        self._small_a = int(os.urandom(128).hex(), 16) % self._n
        self._big_a = pow(self._g, self._small_a, self._n)
        if self._big_a % self._n == 0:  # JS safety check; ~0 probability
            raise AuthenticationError("SRP client error: A mod N == 0")

    @property
    def srp_a_hex(self) -> str:
        return format(self._big_a, "x")

    def challenge_responses(self, challenge: dict[str, str]) -> dict[str, str]:
        """Build the round-2 ``ChallengeResponses`` from the round-1 result."""
        user_id = challenge.get("USER_ID_FOR_SRP") or challenge["USERNAME"]
        server_b = int(challenge["SRP_B"], 16)
        if server_b % self._n == 0:  # JS: B cannot be zero
            raise AuthenticationError("Cognito sent an invalid SRP_B (B mod N == 0)")
        salt = int(challenge["SALT"], 16)
        secret_block = base64.standard_b64decode(challenge["SECRET_BLOCK"])
        timestamp = _now_string(datetime.now(UTC))
        key = self._password_auth_key(user_id, server_b, salt)
        # signed message: pool name, user id, secret block, timestamp
        # (CognitoUser.js, verified against the deployed bundle)
        message = (
            self._pool_name.encode() + user_id.encode() + secret_block + timestamp.encode()
        )
        signature = hmac.new(key, message, hashlib.sha256).digest()
        return {
            "USERNAME": user_id,
            "PASSWORD_CLAIM_SECRET_BLOCK": challenge["SECRET_BLOCK"],
            "TIMESTAMP": timestamp,
            "PASSWORD_CLAIM_SIGNATURE": base64.standard_b64encode(signature).decode(),
        }

    def _password_auth_key(self, user_id: str, server_b: int, salt: int) -> bytes:
        u = int(_hash_hex(_pad_hex(self._big_a) + _pad_hex(server_b)), 16)
        if u == 0:  # JS: U cannot be zero
            raise AuthenticationError("SRP client error: u == 0")
        # x = H(padHex(salt) | H(poolName + userId + ':' + password)) — note the
        # missing separator between pool name and user id (JS behaviour).
        username_password = f"{self._pool_name}{user_id}:{self._password}"
        password_hash_hex = hashlib.sha256(username_password.encode()).hexdigest()
        x = int(_hash_hex(_pad_hex(salt) + password_hash_hex), 16)
        s = pow(server_b - self._k * pow(self._g, x, self._n), self._small_a + u * x, self._n)
        # HKDF-SHA256 with salt=u, ikm=S; the derived key is truncated to 16 bytes.
        prk = hmac.new(bytes.fromhex(_pad_hex(u)), bytes.fromhex(_pad_hex(s)), hashlib.sha256).digest()
        return hmac.new(prk, _INFO_BITS, hashlib.sha256).digest()[:16]
