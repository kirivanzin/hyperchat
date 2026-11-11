# hyperchat

A containerised hello-world that exists to get one image into a private registry, in a way that is
still true when there are fifty of them. No service runs yet.

## Layout

| Path | What it is |
|---|---|
| `src/hyperchat/main.py` | The app: `uvicorn hyperchat.main:api`. One route. |
| `Dockerfile` | Installs from `uv.lock`, runs as uid 10001, no cache reused across builds. |
| `Makefile` | Every operation, in the order it happens. Start with `make help`. |
| `terraform/registry` | The Artifact Registry repository. Applied once. |

## Getting an image into the registry

```sh
make registry-plan          # what would be created, and nothing created
make registry-apply         # the repository itself; once, by hand
make login                  # docker, authorised as your own gcloud identity
make build                  # the image, tagged with the commit
make push                   # into the repository
make registry-verify        # what exists, and who was granted access to it
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
else. `make registry-verify` prints the policy, so the absence is observed rather than assumed.

**Nothing secret is in the image.** There are no secrets yet, and `.dockerignore` and `.gitignore`
both name `*.env`, `*.pem` and `*.key` so that the day one appears it is not baked in or committed
by accident.

## Not here yet

Cloud Run, a probe, authentication, rate limits, CI. Each of them adds a mechanism that this
repository does not need in order to do the one thing it does.
