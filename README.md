# hyperchat

A containerised hello-world that exists to get one image into a private registry, in a way that is
still true when there are fifty of them. No service runs yet.

## Layout

| Path | What it is |
|---|---|
| `src/hyperchat/` | The app: `main.py` the routes, `auth.py` the trust boundary, `agent.py` the model call as a graph, `config.py` what it needs to start and what it needs to think. |
| `src/hyperchat/chat.py` | `make chat`: one message through the same graph, no HTTP. |
| `Dockerfile` | Installs from `uv.lock`, runs as uid 10001, no cache reused across builds. |
| `Makefile` | Every operation, in the order it happens. Start with `make help`. |
| `terraform/github` | The repository, its settings and its branch protection. Applied once, in two steps. |
| `terraform/registry` | The Artifact Registry repository. Applied once. |
| `.github/workflows/ci.yml` | The tests and `terraform validate`, on every pull request. Reports; is not required. |
| `scripts/token.sh` | `make token`: a Google ID token for the configured audience, from your own `gcloud` login. |
| `docs/repository-as-code.md` | What provisioning the repository costs, and why the token is not in CI. |
| `docs/authentication.md` | Who can call a protected route, and why the allowlist — not the audience — is the gate. |
| `docs/agent-identity.md` | The out-of-band steps for giving the agent its own account, and the two credentials that costs. |

## Creating the repository

```sh
make github                 # create, point origin, push, protect
```

which is the four steps below, in order, each printed as it runs so a failure says which half you
are in:

```sh
make github-create          # the repository itself, so there is somewhere to push
make github-remote          # point origin at it
git push -u origin main
make github-apply           # branch protection, now that main exists
```

Two applies and not one, because protection attaches to a branch and a branch is a pointer to a
commit that does not exist until something is pushed. `docs/repository-as-code.md` records that,
and the credential it needs.

## Getting an image into the registry

```sh
make registry-plan          # what would be created, and nothing created
make registry-apply         # the repository itself; once, by hand
make login                  # docker, authorised as your own gcloud identity
make build                  # the image, tagged with the commit
make push                   # into the repository
make registry-status        # what exists, and who was granted access to it
```

`make info` prints the project, region, tag and destination before anything uses them, because a
push into the wrong project succeeds.

The project id is not in this repository. `terraform/registry/terraform.tfvars` holds it and is
git-ignored; the Makefile reads it back out of the applied state, so there is one copy of it rather
than two that can disagree.

## Calling it as a human

`/api/me` is protected and `/` is not. A token is a Google ID token whose audience is the `gcloud` SDK
client id, so your own login can mint one:

```sh
make token                                        # valid for about an hour, printed, never written
curl -H "Authorization: Bearer $(make token)" http://127.0.0.1:8080/api/me
```

`make token` reads the `aud` back out of the token before printing it, so a wrong login fails in the
shell with a claim rather than in the service with a 401.

The app reads `EXPECTED_AUDIENCE` and `ALLOWED_SUBS` and **refuses to start without them**; `.env` is
read by the `Makefile`, not by the application, so a missing value is a crash at import naming what is
missing. `docs/authentication.md` has the checks, the status codes, and the cost.

## Asking the model something

`/api/chat` sends one message through a `langgraph` graph with one node, and returns the reply. The
output cap is the only bound on what a call costs, so it is a number in `.env` and it goes on the wire
by name:

```sh
MESSAGE='Reply with exactly: ok' make chat
PROVIDER_MAX_TOKENS=5 MESSAGE='Count from one to fifty.' make chat
```

`make chat` runs the same `graph` object the endpoint awaits — no HTTP, no token — because a debugging
tool that is a second implementation is a tool that drifts. It needs `PROVIDER_API_KEY` in `.env`; the
endpoint answers **503** when that is absent (the model is off, not broken) and **502** when the
provider fails, with the reason logged and not returned.

## The decisions worth knowing

**The tag is the commit, and tags are immutable.** A tag that can be overwritten is not provenance:
two different images answer to the same name and the only record of which one was pushed is a shell
history. Pushing the same commit twice therefore fails, on purpose.

**Docker authenticates as you, via `gcloud auth configure-docker`.** No service account key, no
static credential in a file, nothing to rotate. The token is minted by the credential helper and
expires.

**No IAM bindings on the repository.** It is reachable by the project's own identities and nobody
else. `make registry-status` prints the policy, so the absence is observed rather than assumed.

**Nothing secret is in the image.** There are no secrets yet, and `.dockerignore` and `.gitignore`
both name `*.env`, `*.pem` and `*.key` so that the day one appears it is not baked in or committed
by accident.

**The build is pinned by digest, and that costs something.** `Dockerfile` names
`python:3.13-slim@sha256:…` and `ghcr.io/astral-sh/uv:0.13.7@sha256:…`, because a mutable tag means a
clone-and-build today runs a different binary than last week's, as root, with nothing recording which.
The trade is that "silently new" becomes "silently old": the base stops taking Debian and CPython
security rebuilds until someone bumps the digest, and nothing bumps it yet. That is #2, and it is why
the ticket exists.

**CI reports, and is not required.** `.github/workflows/ci.yml` runs `uv run pytest -q` and
`terraform validate` on every pull request, with `contents: read` and every action pinned by commit
SHA rather than by tag. It is deliberately not a required check: a check that has never been seen to
fail can block every merge without saying why, so it is added first and required only once it has a
history. `docs/repository-as-code.md` records why those two arrive in that order.

**The graph runtime is the largest dependency here, and it is deliberate.** `langgraph`,
`langchain-core` and `langchain-deepseek` take `uv.lock` from 30 packages to 62, and the image from 18
to 55. #13 removed 41 by dropping a telemetry subtree; this adds a runtime back. It is worth paying
because everything after this — streaming, the run bounds, tools — is work *on* the graph rather than
work to introduce one.

**The allowlist is the gate, not the audience.** The audience is the client id a `gcloud` user token
carries, so anyone with a Google account can mint a token that verifies. Verification narrows that to
"Google vouched for somebody"; `ALLOWED_SUBS` is the only thing deciding who gets through, and the
only thing bounding cost. It is keyed on `sub`, never email: a subject survives a rename, an address
stays with whoever takes it next.

## Not here yet

Cloud Run, a probe, rate limits. Each of them adds a mechanism that this repository does not need in
order to do the one thing it does.
