"""What it takes to be accepted as a caller.

The token is a Google ID token, and the audience is the id a gcloud *user* token carries
(`32555940559.apps.googleusercontent.com`). That choice decides the security model, so it is worth
stating where the code is rather than only in a document: verification proves Google vouched for
somebody, and **anybody with a Google account and `gcloud` can obtain such a token**. The allowlist
on `sub` is therefore the actual gate, and the only thing bounding cost.

Two consequences that shape the checks below:

- The allowlist is keyed on `sub`, never `email`. A subject survives a rename and is never reissued;
  an email-keyed list silently hands access to whoever takes the address next.
- `email_verified` is compared with `is not True`. The string `"false"` is truthy, and a check
  written as `if not claims["email_verified"]` accepts it. A missing claim fails for the same reason.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import jwt

from hyperchat.config import Settings

# Google publishes the keys the tokens are signed with, and rotates them.
CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
# Both spellings are in circulation and Google has not committed to one.
ISSUERS = ("https://accounts.google.com", "accounts.google.com")
# RS256 only. Accepting a list here is how `alg` confusion gets in.
ALGORITHMS = ["RS256"]
# Claims without which nothing below can be reasoned about.
REQUIRED_CLAIMS = ["exp", "iat", "iss", "aud", "sub"]


class AuthError(Exception):
    """The token is not acceptable. `reason` is for tests and logs, never for the caller."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class NotAllowedError(Exception):
    """The token is valid and its subject is not on the allowlist."""


class UpstreamError(Exception):
    """The keys could not be fetched, so the token could not be checked.

    Kept apart from `AuthError` on purpose: "we could not ask" is not "you are not allowed", and
    answering 401 for a certificate outage tells the caller to fix a token that was fine.
    """


@dataclass(frozen=True)
class Caller:
    sub: str
    email: str | None


class SigningKey(Protocol):
    key: Any


_client: jwt.PyJWKClient | None = None


def signing_key(token: str) -> SigningKey:
    """The published key for this token's `kid`, fetched once and cached.

    Lazy on purpose: importing the app must not need the network. The first request that needs a key
    pays for the fetch, and `PyJWKClient` caches by `kid` and refreshes on rotation.
    """
    global _client
    if _client is None:
        _client = jwt.PyJWKClient(CERTS_URL, cache_keys=True, lifespan=3600)
    return _client.get_signing_key_from_jwt(token)


def verify_token(
    token: str,
    *,
    settings: Settings,
    key_resolver: Callable[[str], SigningKey] | None = None,
) -> Caller:
    """Return the caller this token names, or raise.

    `key_resolver` exists so the suite can sign with a key it generated, rather than reaching Google
    for every case. It is resolved at call time, not bound as a default, so patching the module
    attribute is enough.
    """
    resolver = key_resolver or signing_key
    try:
        key = resolver(token)
    except jwt.PyJWKClientConnectionError as exc:
        raise UpstreamError("could not fetch the published keys") from exc
    except jwt.PyJWKClientError as exc:
        raise AuthError("no published key matches this token's kid") from exc
    except jwt.InvalidTokenError as exc:
        # Reading the header to find the key parses it, so a malformed token fails here, before any
        # signature is checked. It is still the caller's problem, so it is still a 401.
        raise AuthError(f"not a JWT: {exc}") from exc

    try:
        claims = jwt.decode(
            token,
            key.key,
            algorithms=ALGORITHMS,
            audience=settings.expected_audience,
            options={"require": REQUIRED_CLAIMS},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("expired") from exc
    except jwt.InvalidAudienceError as exc:
        raise AuthError("audience is not the expected one") from exc
    except jwt.InvalidSignatureError as exc:
        raise AuthError("signature does not match the published key") from exc
    except jwt.MissingRequiredClaimError as exc:
        raise AuthError(f"missing required claim: {exc.claim}") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError(f"invalid token: {exc}") from exc

    if claims.get("iss") not in ISSUERS:
        raise AuthError(f"issuer is not Google: {claims.get('iss')!r}")
    # `is not True`, not falsiness: "false" is a truthy string.
    if claims.get("email_verified") is not True:
        raise AuthError(f"email_verified is {claims.get('email_verified')!r}, not true")

    sub = claims["sub"]
    if sub not in settings.allowed_subs:
        raise NotAllowedError(sub)

    email = claims.get("email")
    return Caller(sub=sub, email=email if isinstance(email, str) else None)
