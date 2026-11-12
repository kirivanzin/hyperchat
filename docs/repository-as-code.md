# The repository is provisioned, and that is bought with a token

`terraform/github` creates this repository, its settings and its branch protection. That is worth
its cost or it is not; this records which cost, so the next person can disagree with a fact instead
of guessing at one.

## What it creates

- the repository, public, with an empty history so that the history in this working directory is
  the history of the repository
- secret scanning and secret scanning push protection, both free on a public repository and both
  the mechanism that makes "no secret has been near this" a property rather than an intention
- branch protection on `main`: `enforce_admins`, no force-push, no deletion, and deliberately
  **no** required reviewers and **no** required checks

Two absences are choices, not gaps. One account cannot approve its own pull request, so a required
approval blocks every merge. And `.github/workflows/ci.yml` reports on every pull request but is
deliberately not required: a check that has never been seen to fail can block every merge without
saying why, so it is added first, watched green and red on a real pull request, and required
afterwards. Required reviewers become correct on the day there is a second identity; a required check
becomes correct the day it has a history. Both are follow-ups with a reason, not omissions.

`github_branch_protection` rather than a `github_repository_ruleset`: a ruleset is the newer
mechanism and has more of them — `conditions`, `bypass_actors`, and one block per rule — where the
protection resource is four attributes that map one-to-one onto the settings already argued about.
If rulesets become the reason for something, the swap is a ticket, not a rewrite.

## The tradeoff

Terraform needs a credential that can create a repository and change its settings. There is no way
to configure the repository before there is a repository, so the credential cannot be scoped to the
repository.

| | |
|---|---|
| What it is | `GITHUB_TOKEN` in the environment, during `make github-plan` and `make github-apply` |
| What it can do | everything the account can: change settings, add collaborators, delete the repository |
| Where it lives | the shell's environment for the length of one command |
| How long | as long as the token is valid — this is the static credential the project otherwise avoids |
| Where it is not | not in the repository, not in the image, not in `terraform.tfvars`, not in CI |

**Not in `.env`.** `.env` is read now — `make dev` sources it and `make run` passes it with
`--env-file` — but by the `Makefile`, never by the application, and it holds the audience and the
allowlist rather than this token. See `docs/authentication.md` for what those do. The reason the token
lives outside the checkout is not that it would reach a container; it is that a checkout gets copied,
cleaned, archived and indexed by tools that were never asked to keep a secret.

**Not in CI.** A workflow that can change repository settings is a workflow whose next modification
can change repository settings. Nothing here needs it: `make push` uses the registry credential and
`make github-status` uses the ordinary `gh` auth, neither of which is this token. The workflow that
now exists runs the tests and `terraform validate` with `contents: read`, so it can be read to confirm
this rather than trusted: no step in it names a secret.

**Not needed to look.** `make github-status` reads the live API through `gh`, so seeing what the
repository is actually set to does not depend on the powerful credential being present, or on
Terraform having run.

## Why not an App

An App is the better credential and it was the plan for a while: an installation token lives an
hour, is scoped to one repository, and cannot touch settings it was not granted. The blocker is
that **an App cannot create a repository**, and it cannot bootstrap itself either — creating one
needs a browser and a private key on disk, which is exactly the out-of-band step this root exists
to remove.

So the token that creates the repository is a user credential by necessity. When a second
repository makes the bootstrap worthwhile, the App is the answer for everything except this one
act, and the honest shape then is: a user token for the creation, an App for the rest.

Until then, if this is to be tightened: a fine-grained token scoped to this one repository with
`Administration: write` and `Contents: read`, expiring in 90 days, minted when a change is needed
and revoked after. That is strictly better than the classic `repo`-scoped token `gh auth token`
returns, which is admin on every repository the account can administer.

## The ordering trap

Branch protection attaches to a branch, and a branch does not exist until something is pushed.

There is no ordering that avoids this: the rule that gates pushes to `main` cannot exist before the
first push to `main`. So the root is applied in two steps, and the first one targets the repository
alone:

```sh
make github-create
```

creates the repository and nothing else — `-target=github_repository.repo`. Then the remote is
pointed at it, the branch is pushed, and:

```sh
make github-apply
```

applies the protection, now that there is a `main` to protect.

Applying the whole root first would create the repository, fail on the protection with a 404, and
leave half a plan applied — which is the same shape as the secret-before-revision problem, and the
same answer: order the steps in the `Makefile` where they can be read, rather than in a sequence
someone remembers.

## The state is local, and that is the decision

All three Terraform roots use local state: `terraform/github/terraform.tfstate`,
`terraform/registry/terraform.tfstate` and `terraform/app/terraform.tfstate`. Each is git-ignored by
`*.tfstate`, exists only in the checkout that applied it, and has no locking and no spare copy. It
is the only copy.

Remote state is a bucket, and a bucket is another bootstrap: a fourth root applied by hand. That is
real work paid now to solve a problem nobody has yet — one operator, one laptop, and a destroy that
can only come from the checkout holding the state — so the decision is to keep local state and write
the consequence down.

What breaks without it is bounded, and worth stating exactly: the resources keep running — Cloud Run
does not need Terraform to stay up — but Terraform can no longer manage them, so recovery is an
import or a recreate. That is an afternoon, not a catastrophe, and it is the same afternoon a backend
bucket without versioning would cost. A machine that has the variables but not the state — this
checkout after losing the file, or the second operator below — plans to create everything, and
applying that plan fails against resources that already exist. A bare clone does not reach it: it
stops at `No value for required variable`, because the variables are in the git-ignored tfvars. That
is a louder failure and the wrong reason to rely on.

The trigger to revisit is named so it is not a matter of mood:

- a second operator who needs to `plan`
- CI performing deploys, which needs state it can reach and lock
- a resource whose recreate is not an afternoon — a database, or anything holding data

Settings, not the token: provider credentials are never written to Terraform state. The state file
holds the repository, its settings and its protection, alongside the `terraform.tfvars` that holds
the owner.

## What keeps this honest

Two commands, and neither requires trusting this document:

```sh
terraform -chdir=terraform/github plan
```

reports drift between the configuration and the live repository.

```sh
make github-status
```

prints what the API says, so the settings are observed rather than assumed. It does not judge the
values — that is what `plan` is for — but it does fail when it cannot read one: an `unreadable` line is
an absence nobody asked for, and a target that stopped at the first refusal could not report on the
rest.
