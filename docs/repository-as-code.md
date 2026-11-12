# The repository is provisioned, and that is bought with a token

`terraform/github` creates this repository, its settings and its branch protection. That is worth its
cost or it is not; this records which cost.

## What it creates

- the repository, public, with an empty history, so the history in the checkout is the history of the
  repository
- secret scanning and secret scanning push protection, both free on a public repository, and both the
  mechanism that makes "no secret has been near this" a property rather than an intention
- Dependabot security updates, in Terraform so the switch is configuration rather than whoever
  remembers clicking it; `.github/dependabot.yml` is what makes the pins movable at all
- branch protection on `main`: `enforce_admins`, no force-push, no deletion, one required check
  (`ci`), and deliberately **no** required reviewers

The required check is the one that only arrived after it had a history: `ci.yml` reported on every
pull request for a day, green and red on real ones, before `terraform/github` named it. A check that
has never been seen to fail can block every merge without saying why, so it is watched first and
required afterwards.

The absence that remains is required reviewers. One account cannot approve its own pull request, so a
required approval that nobody can give would block every merge; that becomes correct the day there is
a second identity.

## The tradeoff

Terraform needs a credential that can create a repository and change its settings. There is no way to
configure the repository before there is a repository, so the credential cannot be scoped to it.

| | |
|---|---|
| What it is | `GITHUB_TOKEN` in the environment, during `make github-plan` and `make github-apply` |
| What it can do | everything the account can: change settings, add collaborators, delete the repository |
| Where it lives | the shell's environment, for the length of one command |
| How long | as long as the token is valid — the static credential this project otherwise avoids |
| Where it is not | not in the repository, not in the image, not in `terraform.tfvars`, not in CI |

**Not in CI.** A workflow that can change repository settings is a workflow whose next modification
can change repository settings. Nothing here needs it: `make push` uses the registry credential and
`make github-status` uses ordinary `gh` auth. The workflow that exists runs tests and
`terraform validate` with `contents: read`, so it can be read to confirm this rather than trusted.

**Not needed to look.** `make github-status` reads the live API through `gh`, so seeing what the
repository is actually set to does not depend on the powerful credential being present.

## Why not an App

An App is the better credential, and it was the plan for a while: an installation token lives an
hour, is scoped to one repository, and cannot touch settings it was not granted. The blocker is that
**an App cannot create a repository**, and cannot bootstrap itself — creating one needs a browser and
a private key on disk, which is exactly the out-of-band step this root exists to remove.

When a second repository makes the bootstrap worthwhile, the App is the answer for everything except
that one act. Until then, the tighter version of this token is a fine-grained one scoped to this
repository with `Administration: write` and `Contents: read`, minted when a change is needed and
revoked after — strictly better than the classic `repo`-scoped token `gh auth token` returns.

## The ordering trap

Branch protection attaches to a branch, and a branch does not exist until something is pushed. There
is no ordering that avoids this: the rule that gates pushes to `main` cannot exist before the first
push to `main`.

So the root is applied in two steps, and the first targets the repository alone. `make github` runs
the four in order, each printed as it runs so a failure says which half you are in:

```sh
make github-create          # the repository, via -target=github_repository.repo
make github-remote          # point origin at it
git push -u origin main
make github-apply           # branch protection, now that main exists
```

Applying the whole root first would create the repository, fail on the protection with a 404, and
leave half a plan applied — the same shape as any bootstrap that assumes its next step succeeded.

## State lives where the thing that writes it lives

Only `terraform/app` keeps its state in a bucket: `gs://<project>-tfstate/hyperchat/app`. That root is
the one a merge applies, so a runner has to reach it and two deploys have to take turns — the bucket
gives both, and it is versioned, so a bad apply has rollbacks rather than one copy.

`terraform/registry` and `terraform/github` keep local state, in the checkout that applied them. That
is a distinction rather than an inconsistency: they are applied once, by a human, and CI changes
neither. Making them remote would buy nothing and cost the same network dependency for a `plan`
nobody runs from a runner.

The bucket name is not in the backend block. The Makefile derives `$(PROJECT)-tfstate` and passes it
as `-backend-config`, which keeps the project id out of the repository. The bucket itself is owned by
the sibling project's bootstrap root: this repository takes a prefix and does not create or manage
it. A project without that bucket fails honestly, at `make deploy`, with `storage: bucket doesn't
exist`.

What the app root gives up is a network dependency — `plan` needs credentials even to read. What it
gains is the failure going away: a machine with the variables and not the state cannot exist for
this root, so it cannot plan to create what already exists. A lost bucket object is a 20-generation
rollback; a lost local file was an afternoon of imports.

Because the service has `deletion_protection = true`, a lost state leaves it undeletable by Terraform
and by `gcloud run services delete`. The escape is
`gcloud run services update <service> --region=<region> --no-deletion-protection` first, then delete.

## The one-time root owns the project-level facts

`terraform/registry` owns three `google_project_service` resources — Artifact Registry, Secret
Manager and Cloud Run — besides the repository. They live here because the app root is the root a
merge applies, and a deploy should not be able to change project-level API state; that is also what
let the deployer drop its one project-wide admin grant.

`disable_on_destroy = false` on all three, so losing them from state leaves the APIs on rather than
disabling a project. They are also enabled by the sibling project's bootstrap root, which is two
owners of one fact — deliberate for now, because a new project needs an owner inside this
repository, and otherwise `make registry-apply` cannot provision one. Two states managing one API is
harmless while neither destroys it.

## What keeps this honest

```sh
terraform -chdir=terraform/github plan
```

reports drift between the configuration and the live repository.

```sh
make github-status
```

prints what the API says, so the settings are observed rather than assumed. It does not judge the
values — that is what `plan` is for — but it does fail when it cannot read one: an `unreadable` line
is an absence nobody asked for, and a target that stopped at the first refusal could not report on
the rest.
