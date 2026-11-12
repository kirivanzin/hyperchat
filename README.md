# hyperchat

A containerised FastAPI service on Cloud Run, provisioned by Terraform, that makes one model call
honestly: a Google ID token is verified, an allowlist of subjects decides who may spend, and the
provider key is never in the image, the state file or the plan.

Two things make it worth reading. The platform is small enough to check by hand, and the agent that
contributed to it is an untrusted principal — its own account, a fork instead of write access, and a
review on every pull request.

## Read this in 60 seconds, 10 minutes, or an hour

- **60 seconds:** the bullets below, and the layout table.
- **10 minutes:** `docs/authentication.md` — the security model — then the decisions.
- **An hour:** the pull requests. The arguments in this repository live there, against the SHA each
  one was made on.

`make help` lists every target; the order is `github → registry → login → build → push → deploy`.
`make lint` and `make test` need no credentials. The merge path is `.github/workflows/deploy.yml`,
whose header and `terraform/registry/identity.tf` are its documentation.

## Layout

| Path | What it is |
|---|---|
| `src/hyperchat/main.py` | The routes. `/`, `/livez` and `/readyz` are open; everything under `/api` is behind the router's dependency. |
| `src/hyperchat/auth.py` | The trust boundary: token verification, the allowlist, and a rate-limited key cache. |
| `src/hyperchat/agent.py` | The model call as a one-node graph — the same object `/api/chat` awaits and `make chat` runs. |
| `src/hyperchat/config.py` | What the process refuses to start without, and what it reads on demand. |
| `Dockerfile` | Installs from `uv.lock`, runs as uid 10001, base images pinned by digest. |
| `Makefile` | Every operation, in the order it happens. |
| `terraform/github` | The repository, its settings and its branch protection. Applied once, in two steps. |
| `terraform/registry` | Artifact Registry, the three project APIs, and the deployer identity. Applied once, by hand. |
| `terraform/app` | Cloud Run, the runtime identity, the provider secret. Applied by a merge. |
| `.github/workflows/ci.yml` | Lint, tests, `terraform validate`, and a build that is discarded. |
| `.github/workflows/deploy.yml` | Deploy a successful `main` build; prove a non-main ref cannot authenticate. |
| `docs/` | `authentication.md`, `repository-as-code.md`, `agents.md`. The agent's setup steps are `agent-runbook.md`. |

## The decisions worth knowing

**The tag is the commit, and tags are immutable.** A tag that can be overwritten is not provenance:
two images answer to the same name and the only record of which one was pushed is a shell history.
Re-pushing a commit fails on purpose; a rebuild is `make push TAG=<sha>-2`.

**Docker authenticates as you, via `gcloud auth configure-docker`.** No service-account key, no
static credential in a file, nothing to rotate. The token is minted by the credential helper.

**No IAM bindings on the registry.** It is reachable by the project's own identities and nobody else.
`make registry-status` prints the policy, so the absence is observed rather than assumed.

**The runtime identity has one grant.** The Cloud Run service account is not the default compute
account, which holds `roles/editor` on this project. It has `secretmanager.secretAccessor` on the
provider secret and nothing else — attached to the secret, so `gcloud secrets get-iam-policy` is
where it shows up.

**The provider key is a Secret Manager reference, not a revision env value.** A revision's
environment is readable by a viewer, so the runtime identity reads the secret instead. The value
arrives as the write-only `TF_VAR_provider_api_key`: never in state, never in the plan file, and the
revision pins the version rather than `latest`.

**`allUsers` may invoke the service.** The allowlist is the gate, not IAM, which is the consequence
of the audience choice in `docs/authentication.md`. The per-subject bound is the only thing between a
public endpoint and spend, so concurrency is pinned at four per instance and scale-out at one
instance: four simultaneous provider calls, not four hundred.

**The deployer is an identity the workflow can only *be*.** `terraform/registry/identity.tf` conditions
the Workload Identity provider on this repository, `refs/heads/main`, this workflow file and the two
events that run it — so a new workflow on `main` cannot inherit it. Five scoped grants, no project
admin. Nothing in CI can widen it.

**Only the root a merge applies keeps its state in a bucket.** `terraform/app` is remote and
prefix-scoped, so a runner reaches it and two deploys take turns. `terraform/registry` and
`terraform/github` are applied once by a human and keep local state. The project id is not in the
repository: it lives in a git-ignored `terraform.tfvars` and the Makefile passes the bucket to
`terraform init`. `docs/repository-as-code.md` records both, and what they cost.

**The build is pinned by digest, and that costs something.** A mutable tag means a clone-and-build
today runs a different binary than last week's, as root, with nothing recording which. The trade is
that "silently new" becomes "silently old": the base stops taking Debian and CPython rebuilds until a
digest moves. Dependabot is what moves it — weekly, grouped, and ignoring interpreter minor versions
because those move four pins at once.

**CI reports, and is not required.** A required check that has never been seen to fail can block
every merge without saying why, so it is watched green and red first and required afterwards. Every
action is pinned by commit SHA and the job runs with `contents: read`.

**The agent is an untrusted principal.** A real account, but not a collaborator: it joins by fork,
its push URL for this repository fails locally, and it has never merged anything. `docs/agents.md`
argues the shape; `docs/agent-runbook.md` is the setup.

**The graph runtime is the largest dependency here, and it is deliberate.** `langgraph`,
`langchain-core` and `langchain-deepseek` take the lock from 30 packages to 62. It is worth paying
because everything after this — streaming, the run bounds, tools, eval — is work *on* the graph
rather than work to introduce one.

**Nothing secret is in the image.** The provider key is a runtime reference, not a build input, and
`.dockerignore` and `.gitignore` both name `*.env`, `*.pem` and `*.key`.

## Asking the model something

`/api/chat` sends one message through the graph and returns the reply. The input is capped at 4000
characters before any provider call, and the output cap goes on the wire by name:

```sh
MESSAGE='Reply with exactly: ok' make chat
PROVIDER_MAX_TOKENS=5 MESSAGE='Count from one to fifty.' make chat
```

`make chat` runs the same `graph` object the endpoint awaits — no HTTP, no token — so a debugging
tool cannot drift from the service. The endpoint answers **503** when no key is configured (the model
is off, not broken) and **502** when the provider fails, with the reason logged and not returned. An
empty reply is a 502 as well, and the log carries `finish_reason`.

## Not here yet

Rate limits, streaming, the run bounds, and tracing. The next phase is the agent itself: tools, a
dataset, an eval harness, and the security testing that goes with them.

## Where to go deeper

- `docs/authentication.md` — who can call a protected route, and what each check is for.
- `docs/repository-as-code.md` — provisioning the repository with Terraform, and the token it costs.
- `docs/agents.md` — the contributor agent as an untrusted principal.
