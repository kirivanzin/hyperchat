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
leave half a plan applied — which is the same shape as any bootstrap that assumes its next step
succeeded, and the same answer: order the steps in the `Makefile` where they can be read, rather than
in a sequence someone remembers.

## The state is in a bucket, and the bucket is not ours

All three roots keep their state in `gs://saxeum-b9b1f-tfstate/`, under a prefix each:
`hyperchat/github`, `hyperchat/registry`, `hyperchat/app`. No checkout holds a state file any more,
so a plan is the same plan on this laptop, on a second operator's machine, and on a runner — and two
applies cannot interleave, because the bucket locks.

The bucket is owned by the sibling project's bootstrap root (`helpdesk/bootstrap`), which declares
what state storage needs and has applied it: versioning on, and a lifecycle rule that deletes a
noncurrent version once 20 newer ones exist. This repository takes a prefix of that bucket and does
not manage it. A second owner of a bucket that already has one is the duplication this repository
keeps refusing, and the same argument that keeps `google_project_service` out of a bootstrap root
(#38) applies in reverse here: the resource already has an owner, and it is not this repository.

**This reverses the decision of #23, on the trigger #23 named.** It listed three events that would
change the answer, the second being "CI performing deploys, which needs state it can reach and lock".
That is #42: a merge to `main` should build and deploy its own commit, and a runner has no laptop.
The decision was right when it was made and it is the obstacle now; the reversal is recorded here
rather than in a commit message, because this is where the next person looks.

What the earlier decision gave up is worth keeping in view, because it is what the bucket buys back:
state is now a network dependency — `plan` needs credentials even to read — and it leaves the
machine. What it no longer costs is the case that paragraph described: a machine with the variables
and not the state cannot exist, so it cannot plan to create what already exists. A lost state file
was an afternoon of imports; a lost bucket object is a 20-generation rollback.

The migration was `terraform init -migrate-state` per root, once, from the checkout that held the
files. Each root's `terraform.tfstate.backup` is the pre-migration copy and stays until that root's
first post-migration plan has been checked; the local `terraform.tfstate` files are zero bytes and
are not read. They are deleted once that is confirmed, deliberately rather than by tidying.

Settings, not the token: provider credentials are never written to Terraform state. That matters
more now than when state lived on one disk — the bucket is shared, and any workflow able to write to
its prefix can read the state this repository puts there.

Because the service has `deletion_protection = true`, a lost state leaves it undeletable by Terraform
and by `gcloud run services delete`; the escape is
`gcloud run services update <service> --region=<region> --no-deletion-protection` first, then delete.

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
