# Who can call a protected route

`/api/me` is the template every protected route follows: verify the token, check the allowlist,
serve. `/` is open because it returns a constant and spends nothing.

## What is checked, and why each one

| Check | Why it is not optional |
|---|---|
| Signature, against Google's published keys | Without it the other fields are a string anyone can type. |
| `exp` | A token that never expires is a credential nobody rotates. |
| `iss` is Google, either spelling | `accounts.google.com` and `https://accounts.google.com` are both in circulation; allowing one would break silently. |
| `aud` is `EXPECTED_AUDIENCE` | A token for another service is a valid token presented in the wrong place. |
| `email_verified is not True` | **Not a truthiness test.** The string `"false"` is truthy, so `if not claims["email_verified"]` accepts it. Missing fails for the same reason. |
| `sub` is on `ALLOWED_SUBS` | The only check that bounds *who*, rather than *whether the token is real*. |

An unknown `kid`, a malformed token and an unsigned (`alg: none`) token are all rejected before or
during verification. Each case has a test, and each test asserts the reason: two checks that answer
401 for the wrong cause are one check.

## The key cache is a rate-limited operation, not a lookup

Google rotates the signing keys, so an unknown `kid` has to be able to trigger a refresh — that is
what a rotation looks like. It is also attacker-controlled: a well-formed token carrying any `kid`
is trivial to construct, and `/api/me` reaches the key lookup before any caller is known, so the
rate limiter behind the allowlist does not apply to it.

`PyJWKClient` refreshes whenever a `kid` is missing, and its `lifespan` governs staleness rather than
a minimum interval, so it costs one outbound request per request. `KeySet` puts a floor on *forced*
refreshes: an unknown `kid` inside the floor gets a 401 and no fetch, and a real rotation — which
arrives long after the floor — is picked up unchanged.

The test asserts **exactly one** fetch for five unknown key ids, then advances the clock past the
floor and asserts **exactly two**. "Fewer than five" would pass a resolver that was never called at
all, which proves nothing.

This one is not only a local lesson. The sibling service refreshes unconditionally on an unknown
`kid` as well, and the comment there guards a wider trigger — refreshing on any error, which would
fire for every expired token — not this one. Whoever fixes it there can copy the floor.

## The audience is the gcloud SDK client, and that decides the security model

`EXPECTED_AUDIENCE` is `32555940559.apps.googleusercontent.com` — the client id that a `gcloud` *user*
token carries. There is no browser sign-in and no OAuth client to own, which is a deliberate trade: a
login page adds a session, CSRF, CORS and a client to maintain, and none of that is platform work.

What that means, said plainly, because a reader who assumes the audience is private gets the model
backwards:

- **Anyone with a Google account and `gcloud` can obtain a token the signature check accepts.** A
  Google account is free and needs no card; `gcloud auth login` never asks for billing.
- Verification narrows that to "Google vouched for someone with that account".
- **The allowlist on `sub` is the actual gate**, and it is the only thing bounding cost. Subjects are
  free to create; membership of this list is not.

So the audience is not a secret and not a permission. It is a filter that says "this token was minted
by Google for this purpose", and it does not distinguish between your colleague and a stranger.

## `sub`, not email

The allowlist is keyed on the token's `sub`. A subject survives a rename and is never reissued; an
email-keyed list silently hands access to whoever takes the address next. The `email` claim is
returned from `/api/me` for display and is never compared to decide anything.

## Config refuses to start

`EXPECTED_AUDIENCE` and `ALLOWED_SUBS` are read at import and have no defaults. A process that boots
with an empty allowlist answers 403 to everybody while its readiness probe stays green, so the
mistake becomes a support ticket instead of a crash. `ALLOWED_SUBS` is comma-separated, and a value
of commas and spaces is rejected the same way an empty one is.

This is also why the suite sets the two values in `tests/conftest.py` rather than the application
defaulting them: a test value is stated where the test is, and cannot be inherited by a deployment.

## `.env` is read by the Makefile, not by the application

`make dev` sources `.env` and `make run` passes it with `--env-file`. The application only ever reads
the environment it was given, which keeps a missing value a crash that names what is missing rather
than something a file quietly filled in. It also keeps `.env` out of the image, where it must never
be read.

```sh
printf 'EXPECTED_AUDIENCE=%s\nALLOWED_SUBS=%s\n' '<client id>' '<your sub>' > .env
make dev
```

With `.env` absent, the same command fails at import and names the missing variable.

## Calling it

```sh
make token
curl -H "Authorization: Bearer $(make token)" http://127.0.0.1:8080/api/me
```

`make token` runs `gcloud auth print-identity-token`, which needs a **user** login rather than a
service account, and reads the `aud` back out of the token before printing it — an audience mismatch
is a claim, not a key, and failing in the shell names which.

| Response | Meaning |
|---|---|
| `200` | Verified, and the subject is on the allowlist. |
| `401` | The token is not acceptable, for any of the reasons above. The response says which check failed to nobody: the reason is in the tests and the logs. |
| `403` | The token is good and the subject is not on the list. Deliberately not 401 — telling a caller with a valid token that their token is broken sends them to fix the wrong thing. |
| `503` | Google's keys could not be fetched, so nothing was decided. Also not 401: "we could not ask" is not "you are not allowed". |

## The cost, written down rather than discovered

- **A reviewer cannot try this without `gcloud` installed and authenticated.** `make token` is the
  cheap mitigation and it is why the two commands exist.
- **The agent does not have `gcloud`.** It verifies the deny paths, which need no token at all, and
  `PYTHONPATH=src uv run python -c` with a key it generated covers the positive path's logic. A real
  Google token is the owner's to mint, and it is valid for about an hour.
- **`cryptography` is now in the image.** JWT verification needs RSA; that is the whole weight of
  `pyjwt[crypto]` and it is not avoidable without dropping signature verification, which would leave
  a decoder that believes whatever it is handed.
