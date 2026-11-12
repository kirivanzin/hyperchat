"""What it takes to be accepted as a caller.

The token is a Google ID token, and the audience is the id a gcloud *user* token carries. That
choice decides the security model, so it is worth stating where the code is rather than only in a
document: verification proves Google vouched for
somebody, and **anybody with a Google account and `gcloud` can obtain such a token**. The allowlist
on `sub` is therefore the actual gate, and the only thing bounding cost.

Two consequences that shape the checks below:

- The allowlist is keyed on `sub`, never `email`. A subject survives a rename and is never reissued;
  an email-keyed list silently hands access to whoever takes the address next.
- `email_verified` is compared with `is not True`. The string `"false"` is truthy, and a check
  written as `if not claims["email_verified"]` accepts it. A missing claim fails for the same reason.

The key cache is the third: **an unknown `kid` is attacker-controlled**, so a refresh it triggers is
a rate-limited operation and not a lookup. See `KeySet`.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
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

# How long a fetched key set may be used, and the minimum gap between *forced* refreshes. The second
# is the security-relevant one: anything that refreshes on demand can be driven by a caller who sends
# a well-formed token with a made-up `kid`, one fetch each. A real rotation arrives long after the
# floor has passed, so the floor costs nothing that matters.
LIFESPAN_SECONDS = 3600.0
REFRESH_FLOOR_SECONDS = 60.0
FETCH_TIMEOUT_SECONDS = 10.0


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


def fetch_published_keys() -> list[dict[str, Any]]:
    """Google's current key set. A failure here is upstream's fault, so it is not an `AuthError`."""
    try:
        with urllib.request.urlopen(CERTS_URL, timeout=FETCH_TIMEOUT_SECONDS) as response:
            return json.load(response)["keys"]
    except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
        raise UpstreamError(f"could not fetch {CERTS_URL}: {exc}") from exc


class KeySet:
    """Google's published keys, cached, with a floor on how often an unknown `kid` can force a fetch.

    `PyJWKClient` refreshes whenever a `kid` is missing from its cache, and `lifespan` governs
    staleness rather than a minimum interval -- so a well-formed token carrying any `kid` costs one
    outbound request, every request. A rate limiter does not help: it sits behind the allowlist and
    this path runs before the caller is known. So the refresh is throttled here, and a token whose
    key id is unknown inside the floor gets a 401 instead of a round trip.

    This is not only a local lesson. The sibling service's `auth.py` documents refreshing on an
    unknown `kid` as what a rotation looks like, and also refreshes unconditionally; the comment
    there guards against a wider trigger, not this one. The floor is the part that bounds it.
    """

    def __init__(
        self,
        fetch: Callable[[], list[dict[str, Any]]],
        *,
        floor_seconds: float = REFRESH_FLOOR_SECONDS,
        lifespan_seconds: float = LIFESPAN_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetch = fetch
        self._floor = floor_seconds
        self._lifespan = lifespan_seconds
        self._clock = clock
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at: float | None = None

    def _refresh(self) -> None:
        # Built completely before it is swapped in: a failed fetch or a malformed response leaves
        # the previous set in place rather than emptying the cache on the way to an exception.
        #
        # A failure here is `UpstreamError` and not `AuthError`. The catch is broad on purpose --
        # anything the fetch raises is an upstream problem, and it is converted into a *different*
        # answer, 503, rather than swallowed into the 401 that a wide catch would hide behind.
        try:
            raw_keys = self._fetch()
        except UpstreamError:
            raise
        except (OSError, ValueError, KeyError) as exc:
            raise UpstreamError(f"could not fetch the published keys: {exc}") from exc
        fetched = {key.key_id: key for key in (jwt.PyJWK(raw) for raw in raw_keys)}
        if not fetched:
            raise UpstreamError("the published key set was empty")
        self._keys = fetched
        self._fetched_at = self._clock()

    def _may_refresh(self, now: float) -> bool:
        return self._fetched_at is None or now - self._fetched_at >= self._floor

    def _is_fresh(self, now: float) -> bool:
        return self._fetched_at is not None and now - self._fetched_at < self._lifespan

    def get(self, token: str) -> SigningKey:
        try:
            kid = jwt.get_unverified_header(token).get("kid")
        except jwt.InvalidTokenError as exc:
            # Reading the header parses it, so a malformed token fails here, before any signature
            # check. It is still the caller's problem, so it is still an `AuthError`.
            raise AuthError(f"not a JWT: {exc}") from exc
        if not isinstance(kid, str) or not kid:
            raise AuthError("token names no key id")

        now = self._clock()
        key = self._keys.get(kid)
        if key is not None and self._is_fresh(now):
            return key

        if not self._may_refresh(now):
            raise AuthError(
                f"key id {kid!r} is not published and a refresh happened less than "
                f"{self._floor:.0f}s ago"
            )

        self._refresh()
        key = self._keys.get(kid)
        if key is None:
            raise AuthError(f"no published key matches key id {kid!r}")
        return key


published = KeySet(fetch_published_keys)
"""The process-wide key set. Patched in tests; reading it at call time is what makes that work."""


def verify_token(
    token: str,
    *,
    settings: Settings,
    key_resolver: Callable[[str], SigningKey] | None = None,
) -> Caller:
    """Return the caller this token names, or raise.

    `key_resolver` exists so the suite can sign with a key it generated, or count fetches, rather
    than reaching Google for every case. It is resolved at call time rather than bound as a default,
    so patching the module-level key set is enough.
    """
    resolver = key_resolver or published.get
    key = resolver(token)

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
