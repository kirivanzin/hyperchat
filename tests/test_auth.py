"""The deny paths, one case each.

A positive-only check passes when verification is a no-op, so the negative cases are the deliverable.
Each one fails for its own reason and the reason is asserted, not just the status code: two checks
that both answer 401 for the wrong cause are one check.

Two sets of cases exercise different layers on purpose. The claim checks are driven through
`verify_token` with a key this file generated, because a real Google token cannot be minted here.
The key cache is driven through `KeySet` with a counting fetch, because "one fetch per request" is a
property of the cache and not of any single token.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from hyperchat import auth
from hyperchat.auth import (
    AuthError,
    KeySet,
    NotAllowedError,
    UpstreamError,
    verify_token,
)
from hyperchat.config import settings
from hyperchat.main import api, protected, require_caller

AUDIENCE = settings.expected_audience
ALLOWED = min(settings.allowed_subs)

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)

KID = "test-key"


class Key:
    def __init__(self, key: object) -> None:
        self.key = key


def jwks(*keys: tuple[str, object]) -> list[dict[str, object]]:
    """A published key set, shaped the way Google's endpoint serves one."""
    published_: list[dict[str, object]] = []
    for kid, key in keys:
        entry = json.loads(RSAAlgorithm.to_jwk(key.public_key()))  # type: ignore[attr-defined]
        entry["kid"] = kid
        published_.append(entry)
    return published_


def resolver(_token: str) -> Key:
    """Stands in for the key set. The public half, because that is what a verifier may hold."""
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


def token(*, kid: str = KID, **overrides: object) -> str:
    return jwt.encode(claims(**overrides), KEY, algorithm="RS256", headers={"kid": kid})


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
    forged = jwt.encode(claims(), OTHER_KEY, algorithm="RS256", headers={"kid": KID})

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


def test_an_unlisted_subject_is_not_allowed():
    with pytest.raises(NotAllowedError):
        verify_token(token(sub="sub-someone-else"), settings=settings, key_resolver=resolver)


# --- the key cache: untrusted input must not buy outbound requests ------------------------------


def test_a_token_that_is_not_a_jwt_never_reaches_the_network():
    def fetch() -> list[dict[str, object]]:
        raise AssertionError("a malformed token caused a fetch")

    with pytest.raises(AuthError) as caught:
        KeySet(fetch).get("not.a.token")

    assert caught.value.reason.startswith("not a JWT")


def test_a_token_with_no_key_id_is_rejected_without_a_fetch():
    def fetch() -> list[dict[str, object]]:
        raise AssertionError("a token with no kid caused a fetch")

    header = base64.urlsafe_b64encode(b'{"alg":"RS256","typ":"JWT"}').rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(json.dumps(claims()).encode()).rstrip(b"=").decode()

    with pytest.raises(AuthError) as caught:
        KeySet(fetch).get(f"{header}.{payload}.sig")

    assert "key id" in caught.value.reason


def test_an_unknown_key_id_cannot_force_a_fetch_per_request():
    """Five unknown kids inside the floor cost one fetch, not five, and not one per request.

    The shape matters: asserting "fewer than five" would pass a resolver that is never called at
    all. This asserts exactly one, then advances the clock past the floor and asserts exactly two.
    """
    now = [1000.0]
    fetches: list[float] = []

    def fetch() -> list[dict[str, object]]:
        fetches.append(now[0])
        return jwks((KID, KEY))

    keys = KeySet(fetch, clock=lambda: now[0])

    for kid in ("a", "b", "c", "d", "e"):
        with pytest.raises(AuthError):
            verify_token(token(kid=kid), settings=settings, key_resolver=keys.get)

    assert len(fetches) == 1, "an unknown kid bought one outbound request per token"

    # Rotation still works: a real one arrives long after the floor has passed.
    now[0] += 61
    with pytest.raises(AuthError):
        verify_token(token(kid="f"), settings=settings, key_resolver=keys.get)

    assert len(fetches) == 2


def test_the_same_unknown_key_id_is_also_throttled():
    fetches: list[float] = []

    def fetch() -> list[dict[str, object]]:
        fetches.append(time.monotonic())
        return jwks((KID, KEY))

    keys = KeySet(fetch, clock=lambda: 1000.0)

    for _ in range(5):
        with pytest.raises(AuthError):
            keys.get(token(kid="repeated"))

    assert len(fetches) == 1


def test_a_known_key_is_served_from_cache_without_refetching():
    fetches: list[float] = []

    def fetch() -> list[dict[str, object]]:
        fetches.append(time.monotonic())
        return jwks((KID, KEY))

    keys = KeySet(fetch, clock=lambda: 1000.0)

    for _ in range(5):
        assert keys.get(token()) is not None

    assert len(fetches) == 1


def test_a_certificate_outage_is_not_a_401():
    def unreachable() -> list[dict[str, object]]:
        raise urllib.error.URLError("connection refused")

    with pytest.raises(UpstreamError):
        KeySet(unreachable).get(token())


# --- over HTTP, where the status codes are the contract ------------------------------------------

client = TestClient(api)


@pytest.fixture
def google_keys(monkeypatch):
    """Make the app resolve the key this file generated instead of Google's."""
    keys = KeySet(lambda: jwks((KID, KEY)))
    monkeypatch.setattr(auth, "published", keys)
    return keys


def test_the_open_route_needs_nothing():
    assert client.get("/").status_code == 200


def test_a_valid_token_reaches_the_protected_route(google_keys):
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


def test_a_rejected_token_is_401_and_says_which_check_only_to_us(google_keys):
    response = client.get(
        "/api/me", headers={"Authorization": f"Bearer {token(exp=int(time.time()) - 1)}"}
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "token is not acceptable"
    assert response.headers["www-authenticate"] == 'Bearer error="invalid_token"'


def test_a_malformed_token_is_401_not_500(google_keys):
    # This answered 500 until the key lookup's parse error was caught: reading the header to find the
    # `kid` parses it, and that raises a `DecodeError`, which is not what the resolver was assumed to
    # raise. The live check is what caught it; this keeps it caught.
    assert client.get("/api/me", headers={"Authorization": "Bearer not.a.token"}).status_code == 401


def test_a_certificate_outage_is_503(google_keys, monkeypatch):
    def unreachable(_token: str) -> Key:
        raise UpstreamError("could not fetch the published keys")

    monkeypatch.setattr(auth, "published", type("K", (), {"get": staticmethod(unreachable)})())

    response = client.get("/api/me", headers={"Authorization": f"Bearer {token()}"})

    assert response.status_code == 503


def test_an_unlisted_subject_is_403_not_401(google_keys):
    # The token is good; the subject is not on the list. Collapsing this into 401 would tell a caller
    # with a valid token that their token is broken.
    response = client.get("/api/me", headers={"Authorization": f"Bearer {token(sub='other')}"})

    assert response.status_code == 403


# --- the boundary is a property of the router, not of remembering ---------------------------------


def test_the_protected_router_carries_the_verifying_dependency():
    assert protected.dependencies, "the /api router has no dependency: a new route could forget one"
    assert any(dep.dependency is require_caller for dep in protected.dependencies)


def test_a_route_that_forgets_the_dependency_is_still_protected():
    # Declared with no dependency at all. If the rule lived on each route this would answer 200.
    @protected.get("/forgotten")
    def forgotten() -> dict[str, str]:
        return {"ok": "yes"}

    # `api` copied the router's routes at import, so this route exists only in the app built here.
    app = FastAPI()
    app.include_router(protected)

    assert TestClient(app).get("/api/forgotten").status_code == 401
