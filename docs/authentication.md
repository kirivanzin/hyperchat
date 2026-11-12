# Who can call a protected route

`/api/me` is the template every protected route follows: verify the token, check the allowlist,
serve. `/` is open because it returns a constant and spends nothing.

## What is checked, and why each one

| Check | Why it is not optional |
|---|---|
| Signature, against Google's published keys | Without it the other fields are a string anyone can type. |
| `exp` | A token that never expires is a credential nobody rotates. |
| `iss` is Google, either spelling | `accounts.google.com` and `https://accounts.google.com` are both in circulation. |
| `aud` is `EXPECTED_AUDIENCE` | A token for another service is a valid token presented in the wrong place. |
| `email_verified is not True` | **Not a truthiness test.** The string `"false"` is truthy, so `if not claims["email_verified"]` accepts it. Missing fails for the same reason. |
| `sub` is on `ALLOWED_SUBS` | The only check that bounds *who*, rather than whether the token is real. |

An unknown `kid`, a malformed token and an unsigned (`alg: none`) token are all rejected before or
during verification. Each case has a test, and each test asserts the reason: two checks that answer
401 for the wrong cause are one check.

## The key cache is a rate-limited operation, not a lookup

Google rotates its signing keys, so an unknown `kid` has to be able to trigger a refresh — that is
what a rotation looks like. It is also attacker-controlled: a well-formed token carrying any `kid` is
trivial to construct, and the key lookup happens before any caller is known, so a rate limiter behind
the allowlist does not apply to it.

`PyJWKClient` refreshes whenever a `kid` is missing, and its `lifespan` governs staleness rather than
a minimum interval, so it costs one outbound request per request. `KeySet` puts a floor on *forced*
refreshes: an unknown `kid` inside the floor gets a 401 and no fetch, and a real rotation — which
arrives long after the floor — is picked up unchanged. The code and its argument are in `auth.py`.

The test asserts **exactly one** fetch for five unknown key ids, then advances the clock past the
floor and asserts **exactly two**. "Fewer than five" would pass a resolver that was never called at
all, which proves nothing.

## The audience is the gcloud SDK client, and that decides the security model

`EXPECTED_AUDIENCE` is the client id a `gcloud` *user* token carries. There is no browser sign-in and
no OAuth client to own, which is a deliberate trade: a login page adds a session, CSRF, CORS and a
client to maintain, and none of that is platform work.

What that means, said plainly, because a reader who assumes the audience is private gets the model
backwards:

- **Anyone with a Google account and `gcloud` can obtain a token the signature check accepts.** A
  Google account is free and needs no card; `gcloud auth login` never asks for billing.
- Verification narrows that to "Google vouched for someone with that account".
- **The allowlist on `sub` is the actual gate**, and it is the only thing bounding cost. Subjects are
  free to create; membership of this list is not.

The audience is not a secret and not a permission. It is a filter that says "this token was minted by
Google for this purpose", and it does not distinguish a colleague from a stranger.

## `sub`, not email

The allowlist is keyed on the token's `sub`. A subject survives a rename and is never reissued; an
email-keyed list silently hands access to whoever takes the address next. `email` is returned from
`/api/me` for display and never compared to decide anything.

## Where the key goes, and why

`.env` holds `EXPECTED_AUDIENCE` and `ALLOWED_SUBS` — not credentials: an audience is a public client
id and a subject is an identifier the service already logs. The **provider key** is read from
`~/.config/hyperchat/agent-deepseek.token`, mode 600, outside every checkout, or from an exported
`PROVIDER_API_KEY`.

A `.env` that sets `PROVIDER_API_KEY` is **refused**, not preferred. The reason is not that `.env`
would be committed — it is git-ignored. It is that a checkout gets copied, cleaned, archived and
indexed by tools that were never asked to keep a secret, and a rule with one exception is a rule that
gets forgotten. `Makefile`'s `LOCAL_ENV` is the enforcement.

## Config refuses to start

`EXPECTED_AUDIENCE` and `ALLOWED_SUBS` are read at import and have no defaults. A process that boots
with an empty allowlist answers 403 to everybody without looking broken, so the mistake becomes a
support ticket instead of a crash. `ALLOWED_SUBS` is comma-separated, and a value of commas and
spaces is rejected the same way an empty one is. `.env` is read by the Makefile, never by the
application: a missing value is a crash that names it rather than something a file quietly filled in.

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
| `401` | The token is not acceptable, for any of the reasons above. The caller is not told which check failed; the reason is in the tests and the logs. |
| `403` | The token is good and the subject is not on the list. Deliberately not 401 — telling a caller with a valid token that their token is broken sends them to fix the wrong thing. |
| `503` | Google's keys could not be fetched, so nothing was decided. "We could not ask" is not "you are not allowed". |

## The cost

A reviewer cannot try this without `gcloud` installed and authenticated; `make token` is the cheap
mitigation. The agent has no `gcloud`, so it verifies the deny paths, which need no token at all. And
`pyjwt[crypto]` puts RSA in the image, which is not avoidable without dropping signature
verification — a decoder that believes whatever it is handed.
