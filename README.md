# hyperchat

A containerised hello-world that exists to get one image into a private registry, in a way that is
still true when there are fifty of them. No service runs yet.

## Layout

| Path | What it is |
|---|---|
| `src/hyperchat/main.py` | The app: `uvicorn hyperchat.main:api`. One route. |
| `Dockerfile` | Installs from `uv.lock`, runs as uid 10001, no cache reused across builds. |
| `Makefile` | Every operation, in the order it happens. Start with `make help`. |
| `terraform/github` | The repository, its settings and its branch protection. Applied once, in two steps. |
| `terraform/registry` | The Artifact Registry repository. Applied once. |
| `.github/workflows/ci.yml` | The tests and `terraform validate`, on every pull request. Reports; is not required. |
| `docs/repository-as-code.md` | What provisioning the repository costs, and why the token is not in CI. |
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

## Not here yet

Cloud Run, a probe, authentication, rate limits. Each of them adds a mechanism that this repository
does not need in order to do the one thing it does.
