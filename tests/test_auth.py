"""The deny paths, one case each.

A positive-only check passes when verification is a no-op, so the negative cases are the deliverable.
Each one fails for its own reason and the reason is asserted, not just the status code: two checks
that both answer 401 for the wrong cause are one check.

The tokens here are signed with a key this file generates, because a real Google token cannot be
minted in this environment. That is the same substitution the ticket describes for the unlisted
subject: construct the claim set, then prove the check rejects it.
"""

from __future__ import annotations

import base64
import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from hyperchat import auth
from hyperchat.auth import AuthError, NotAllowedError, UpstreamError, verify_token
from hyperchat.config import settings
from hyperchat.main import api

AUDIENCE = settings.expected_audience
ALLOWED = min(settings.allowed_subs)

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class Key:
    def __init__(self, key: object) -> None:
        self.key = key


def resolver(_token: str) -> Key:
    """Stands in for the published key set. `kid` is ignored: one key, and the point is the checks.

    The public half, because that is what `PyJWKClient` hands back and what a verifier is allowed to
    hold. Returning the private key here failed with `RSAPrivateKey has no attribute verify`, which
    is the sort of thing only a test that actually verifies catches.
    """
    return Key(KEY.public_key())


def claims(**overrides: object) -> dict[str, object]:
    now = int(time.time())
    payload: dict[str, object] = {
        "iss": "https://accounts.google.com",
        "aud": AUDIENCE,
        "sub": ALLOWED,
        "email": "someone@example.com",
        "email_verified": True,
        "iat": now - 30,
        "exp": now + 600,
    }
    payload.update(overrides)
    return payload


def token(**overrides: object) -> str:
    return jwt.encode(claims(**overrides), KEY, algorithm="RS256", headers={"kid": "test-key"})


def reason(value: str) -> str:
    with pytest.raises(AuthError) as caught:
        verify_token(value, settings=settings, key_resolver=resolver)
    return caught.value.reason


# --- accepted -----------------------------------------------------------------------------------


def test_a_valid_token_names_the_caller():
    caller = verify_token(token(), settings=settings, key_resolver=resolver)

    assert caller.sub == ALLOWED
    assert caller.email == "someone@example.com"


# --- rejected, one reason each ------------------------------------------------------------------


def test_a_signature_from_another_key_is_rejected():
    forged = jwt.encode(claims(), OTHER_KEY, algorithm="RS256", headers={"kid": "test-key"})

    assert reason(forged) == "signature does not match the published key"


def test_an_expired_token_is_rejected():
    assert reason(token(exp=int(time.time()) - 1)) == "expired"


def test_another_audience_is_rejected():
    assert "audience" in reason(token(aud="someone-elses-client.apps.googleusercontent.com"))


def test_a_non_google_issuer_is_rejected():
    assert "issuer" in reason(token(iss="https://example.com"))


def test_the_bare_issuer_spelling_is_accepted():
    # Both spellings are in circulation; this is the one that would silently break if only the
    # https:// form were allowed.
    caller = verify_token(
        token(iss="accounts.google.com"), settings=settings, key_resolver=resolver
    )

    assert caller.sub == ALLOWED


@pytest.mark.parametrize("value", ["false", "true", False, None, 0])
def test_email_verified_must_be_the_boolean_true(value):
    # "false" is a truthy string, which is why the check is `is not True` rather than a truthiness
    # test. `False` and a missing claim are the same answer.
    assert "email_verified" in reason(token(email_verified=value))


def test_a_missing_email_verified_is_rejected():
    payload = claims()
    del payload["email_verified"]

    assert "email_verified" in reason(jwt.encode(payload, KEY, algorithm="RS256"))


def test_an_unsigned_token_is_rejected():
    def segment(value: dict[str, object]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    unsigned = f"{segment({'alg': 'none', 'typ': 'JWT'})}.{segment(claims())}."

    assert "invalid token" in reason(unsigned)


def test_a_token_that_is_not_a_jwt_is_rejected():
    assert "invalid token" in reason("not.a.token")


def test_a_header_the_resolver_cannot_parse_is_a_401_not_a_500():
    # The real resolver reads the header to find the key, so a malformed token fails there -- with a
    # `DecodeError`, which is an `InvalidTokenError` and not the `PyJWKClientError` that was assumed.
    # That mismatch answered 500 until this case existed.
    def parsing(_token: str) -> Key:
        raise jwt.DecodeError("Invalid header padding")

    with pytest.raises(AuthError) as caught:
        verify_token("not.a.token", settings=settings, key_resolver=parsing)

    assert caught.value.reason.startswith("not a JWT")


def test_a_certificate_outage_is_not_a_401():
    def unreachable(_token: str) -> Key:
        raise jwt.PyJWKClientConnectionError("connection refused")

    with pytest.raises(UpstreamError):
        verify_token("anything", settings=settings, key_resolver=unreachable)


def test_an_unlisted_subject_is_not_allowed():
    with pytest.raises(NotAllowedError):
        verify_token(token(sub="sub-someone-else"), settings=settings, key_resolver=resolver)


# --- over HTTP, where the status codes are the contract ------------------------------------------


client = TestClient(api)


@pytest.fixture
def published(monkeypatch):
    """Make the app resolve the key this file generated instead of Google's."""
    monkeypatch.setattr(auth, "signing_key", resolver)


def test_the_open_route_needs_nothing():
    assert client.get("/").status_code == 200


def test_a_valid_token_reaches_the_protected_route(published):
    response = client.get("/api/me", headers={"Authorization": f"Bearer {token()}"})

    assert response.status_code == 200
    assert response.json() == {"sub": ALLOWED, "email": "someone@example.com"}


def test_no_header_is_401():
    response = client.get("/api/me")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("header", ["", "Bearer", "Bearer   ", "Basic dXNlcjpwYXNz", "Token abc"])
def test_a_header_that_is_not_a_bearer_token_is_401(header):
    assert client.get("/api/me", headers={"Authorization": header}).status_code == 401


def test_a_rejected_token_is_401_and_says_which_check_only_to_us(published):
    response = client.get(
        "/api/me", headers={"Authorization": f"Bearer {token(exp=int(time.time()) - 1)}"}
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "token is not acceptable"
    assert response.headers["www-authenticate"] == 'Bearer error="invalid_token"'


def test_a_malformed_token_is_401_not_500(published, monkeypatch):
    def parsing(_token: str) -> Key:
        raise jwt.DecodeError("Invalid header padding")

    monkeypatch.setattr(auth, "signing_key", parsing)

    assert client.get("/api/me", headers={"Authorization": "Bearer not.a.token"}).status_code == 401


def test_a_certificate_outage_is_503(published, monkeypatch):
    def unreachable(token: str) -> Key:
        raise jwt.PyJWKClientConnectionError("connection refused")

    monkeypatch.setattr(auth, "signing_key", unreachable)

    response = client.get("/api/me", headers={"Authorization": f"Bearer {token()}"})

    assert response.status_code == 503


def test_an_unlisted_subject_is_403_not_401(published):
    # The token is good; the subject is not on the list. Collapsing this into 401 would tell a caller
    # with a valid token that their token is broken.
    response = client.get("/api/me", headers={"Authorization": f"Bearer {token(sub='other')}"})

    assert response.status_code == 403
