# The coding agent joins as a contributor

The contributor account `kirivanzin` is a real GitHub account with its own SSH key, its own
token and its own checkout. It is not a collaborator: it joins by fork, and every one of its pull
requests is reviewed and merged by the owner.

The point is not trust. It is that a principal which cannot publish to this repository cannot damage
it, whatever it decides to do.

## Why a fork, and not a collaborator

A collaborator with `write` is the only thing here that can publish to the repository, and a push to
a public repository cannot be retracted: forks keep it, GH Archive keeps it, and the object stays
fetchable by SHA after any force-push.

So the account joins the way an untrusted contributor joins — with a fork. That buys one thing and
one thing only: the project's refs are unreachable from this account. It cannot create a branch here,
force-push one, or change a setting. What enters the project is what a human merged from a pull
request.

What it does not buy: a leak is still a leak, because the fork is public. What changes is where it
lands — in `<the login>/hyperchat`, which can be deleted, rather than in the project, where every
other fork keeps a copy.

## The push URL fails locally

```sh
git remote set-url --push upstream DISABLED
```

`DISABLED` is not a magic word. It is a URL that cannot resolve, so a mistaken `git push upstream`
fails locally, before a server has to refuse it.

## Three identities, and only one of them is `user.name`

| What | Set by | When it is wrong |
|---|---|---|
| Commit author and committer | `user.name` / `user.email` | commits attributed to the owner — visible, embarrassing |
| Transport, i.e. who pushes | SSH key or remote URL | the contributor's branch pushed with the owner's credential |
| API, i.e. who opens PRs and comments | `GH_TOKEN` / `gh auth` | the pull request or the approval is the owner's — **silent**, and it defeats the point |

A repository-local `user.name` already beats `~/.gitconfig`, so commit identity is not a precedence
problem; it is a remembering problem. The fix is to make the identity a property of the directory: a
separate clone, an `includeIf "gitdir:"` rule, and an empty `credential.helper` that clears an
inherited one.

A separate clone, not a linked worktree: `gitdir:` matches the *git* directory, and a worktree's is
`<main>/.git/worktrees/<name>`, so a rule for the contributor's directory would not fire there.

The machine keeps the word in several places: `git@github-agent`, `~/.ssh/hyperchat-agent`,
`~/.gitconfig-agent`, `~/agent/hyperchat`, `~/.config/hyperchat/agent-deepseek.token` and its mount
at `/run/agent-deepseek`, and the ignored `.agent.identity`. Those are names on a machine rather
than claims about a principal, which is why this prose does not use them.

The misnamed one is `agent-deepseek.token`: it is the provider key, not an agent's.

## What the account can actually do

A capability test, not a trust test. It can open issues, push branches to its own fork, open pull
requests, comment and review. It cannot push to this repository, merge, or change a setting. That is
measured rather than assumed:

```sh
GH_TOKEN=<the token> gh api /repos/saxeum/hyperchat --jq .permissions.push
```
```
false
```

## What this costs

Two static credentials and a 2FA seed, none of which Terraform can rotate. A second identity the
owner is accountable for, in their name, on a public repository. A public fork. And no approvals: an
approval from an account without write access does not count toward required reviews, so the
contributor's verification is a comment that names the SHA it checked.

## The steps

`docs/contributor-runbook.md` — mailbox, account, 2FA, SSH key, token, the three identities, the
fork, and how to prove it. A runbook is consulted rather than read; the argument is above.
