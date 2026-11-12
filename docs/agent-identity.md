# Giving the agent its own account

A real account, not an App: an account is what a person has, which is the experiment — the agent
should be indistinguishable from a colleague in the record, with its own login, its own commits and
its own approval on a pull request it did not write.

`docs/repository-as-code.md` explains why the App was the plan and stopped being one. The short
version is that an App cannot create a repository and cannot bootstrap itself, so a user credential
was needed anyway.

## What Terraform owns, and what it cannot

| Piece | Owner | Why |
|---|---|---|
| Creating the account | **you, in a browser** | No API exists. Signup is a web form with email verification. |
| Enabling 2FA | **you** | Required for any account that pushes code, and recovery codes are a credential. |
| The SSH key | **you** | Registering it needs the account's own token, which is circular. |
| The access token | **you** | No PAT resource exists in any provider, and no PAT API. |
| Access to this repository | **Nothing**, deliberately | There is no collaborator resource, so nothing drifts — and equally, nothing would notice one added by hand. `make github-status` is how you find out: it names outside collaborators by role and pending invitations by id. |

Everything below marked *you* is out of band, once. Everything else is `make github-apply`.

## 1. A mailbox

The account needs its own address, and it becomes the recovery address, so use one you will still
have in a year. A `+` alias on a mailbox you already own is usually accepted — `you+agent@…` — and
if GitHub refuses it, a separate mailbox is the fallback.

Do not use the address of the account that owns this repository. Two accounts on one mailbox is how
a password reset for one becomes a password reset for both.

## 2. Create the account

Sign up at `https://github.com/signup`, verify the email, and choose a login that reads the way the
experiment intends. The login is the actor in every comment and every commit it makes, so it is the
one thing here that is worth ten seconds of thought.

GitHub documents machine users as a supported pattern for automation. The account is yours, it
answers to GitHub's terms, and you are responsible for what it does.

## 3. Two-factor, before anything else

GitHub requires 2FA for accounts that contribute code. Enable TOTP and **store the recovery codes in
the password manager**, because they are the only way back in if the device is lost and they are a
credential in their own right.

Skipping this is not a soft failure: the first push is refused, and the message is about
authentication, which reads like a broken key.

## 4. Note the numeric account id

```sh
gh api /users/<the-new-login> --jq .id
```

prints a number. It is half of the noreply address in step 7, and looking it up later is easier than
finding it now.

## 5. An SSH key, kept apart from your own

```sh
ssh-keygen -t ed25519 -f ~/.ssh/hyperchat-agent -C hyperchat-agent -N ''
```

No passphrase, because the agent cannot type one — which means **the file is the credential**: `chmod 600`,
and it lives outside every repository.

Two accounts on `github.com` cannot both offer their keys by default, so give the agent its own host
alias in `~/.ssh/config`:

```
Host github-agent
  HostName github.com
  User git
  IdentityFile ~/.ssh/hyperchat-agent
  IdentitiesOnly yes
  IdentityAgent none
```

`IdentitiesOnly` and `IdentityAgent none` are what stop your own ssh-agent offering the human key
first and getting the wrong account.

Register the public key on the new account — Settings → SSH and GPG keys — and check which account
answers:

```sh
ssh -T git@github-agent
```

says `Hi <the-new-login>!` followed by a refusal of shell access, which is the success case.

## 6. A token for the API, not for the repository

A **classic** personal access token, `repo` scope only, expiring in 90 days.

Not fine-grained, and not by preference. A fine-grained token can only be scoped to repositories its
owner reaches through their own account or an organisation **membership**, and this account is
deliberately not a member — `saxeum/hyperchat` will not appear in that picker at all.

`repo` is wider than it sounds and narrower than it sounds, for the same reason: it grants what the
account can already reach, and after step 8 that is one public fork and nothing else. It is the scope
to stop trusting the moment this account is involved with a second repository.

Git goes over SSH from step 5, so the token is never a git password. It exists for forking, opening
pull requests, and commenting.

`gh` refuses to *store* a classic token that lacks `read:org` — `gh auth login --with-token` fails
with `missing required scope`. If the token should live in the keyring rather than in a file, add
`read:org`; on an account in no organisation it reads nothing at all.

## 7. Three identities, and only one of them is `user.name`

This is where the two accounts clash, and configuring the obvious one looks like solving it. There
are three separate things, and they are set in three separate places:

| What | Set by | When it is wrong |
|---|---|---|
| Commit author and committer | `user.name` / `user.email` | commits attributed to you — visible, embarrassing |
| Transport, i.e. who pushes | SSH key or remote URL | the agent's branch pushed with your credential |
| API, i.e. who opens pull requests and comments | `GH_TOKEN` / `gh auth` | the pull request or the approval is yours — **silent**, and it defeats the point |

A repository-local `user.name` already beats `~/.gitconfig`, so the commit identity is not a
precedence problem; it is a remembering problem, and the fix is to make the identity a property of
the directory. Give the agent its own checkout and let the path decide:

```sh
git clone git@github-agent:saxeum/hyperchat.git ~/agent/hyperchat
```

```
# ~/.gitconfig
[includeIf "gitdir:~/agent/"]
	path = ~/.gitconfig-agent
```

```
# ~/.gitconfig-agent
[user]
	name = <the login>
	email = <the id>+<the login>@users.noreply.github.com
[core]
	sshCommand = ssh -i ~/.ssh/hyperchat-agent -o IdentitiesOnly=yes -o IdentityAgent=none
[credential]
	helper =
```

The trailing slash makes `gitdir:` a prefix match. The `sshCommand` line means a clone made with the
wrong remote URL still pushes as the agent. The empty `credential.helper` **clears** an inherited
helper — if `~/.gitconfig` sets `helper = store`, a plaintext credential cache holding the other
account's token would otherwise be offered on this one's behalf, successfully and silently.

**A separate clone, not a linked worktree.** `gitdir:` matches the *git* directory, and a linked
worktree's is `<main>/.git/worktrees/<name>`, not its working path — a rule for `~/agent/`
will not fire there. Worktrees need `extensions.worktreeConfig true` and `git config --worktree`
instead, which is more parts for the same result.

### Prove it before committing, not after

```sh
git -C ~/agent/hyperchat var GIT_AUTHOR_IDENT
git -C ~/hyperchat var GIT_AUTHOR_IDENT
```

print the two different identities, with every include resolved. That is the command to reach for
whenever a commit lands under the wrong name.

### What no configuration can fix

- **A stale `GH_TOKEN` in the shell.** It overrides `gh auth`, so `gh` can act as the agent while git
  acts as you. Nothing in git config can see it.
- **`git commit --author=`** changes the author and leaves you as committer.
- **One directory.** If both identities commit in the same tree, no config can tell them apart; only
  per-command `GIT_AUTHOR_*` and `GIT_COMMITTER_*` can, which is the answer that depends on never
  forgetting.

## 8. No collaborator. A fork.

Terraform adds this account to nothing, and `terraform/github` says so by having no resource for it.

A collaborator with `write` is the only thing in this repository capable of publishing to it
irreversibly, and a push to a public repository cannot be retracted: forks keep it, GH Archive keeps
it, and the object stays fetchable by SHA after any force-push. So the account joins the way an
untrusted contributor joins — with a fork.

```sh
gh repo fork saxeum/hyperchat --clone=false
```

The checkout it works in then points at that fork, with the project read-only:

```sh
git remote set-url origin git@github-agent:<the login>/hyperchat.git
git remote add upstream https://github.com/saxeum/hyperchat.git
git remote set-url --push upstream DISABLED
git config remote.pushDefault origin
```

`DISABLED` is not a magic word. It is a URL that cannot resolve, so a mistaken `git push upstream`
fails locally, before a server has to refuse it.

**What this buys.** The project's refs are unreachable from this account: it cannot create a branch
on `saxeum/hyperchat`, force-push one, or change a setting. What enters the project is what a human
merged from a pull request.

**What it does not buy.** A leak is still a leak — the fork is public, because forks of public
repositories are. What changes is where it lands: in `<the login>/hyperchat`, which can be deleted,
rather than in the project, where everybody else's fork keeps a copy.

## 9. Prove it

```sh
GH_TOKEN=<the token> gh api /user --jq .login
```

prints the agent's login, not yours.

```sh
GH_TOKEN=<the token> gh api /repos/saxeum/hyperchat --jq .permissions.push
```

prints `false`. That is the design in one line: the account can read the project and cannot write to
it.

```sh
git -C ~/agent/hyperchat remote -v
```

shows the fork as `origin`, and `DISABLED` as upstream's push URL.

```sh
git -C ~/agent/hyperchat push --dry-run upstream HEAD:refs/heads/probe
```

fails locally, before any network call. The same command against `origin` succeeds, because that is
the fork.

```sh
gh api /orgs/saxeum --jq .plan.filled_seats
```

prints `null` with this token, so the seat count is a field the owner reads, and `make github-status`
is where it appears. Which of two reasons makes it unreadable is not something this measurement
separates: the account is not a member, and the `repo`-only scope from step 6 leaves `gh auth status`
reporting `Missing required token scopes: 'read:org'`. The account being neither a member nor a
collaborator is what the two lines above it in that output are for.

And the thing the experiment is actually about — a comment on a pull request, from an account that is
not its author. Anyone can comment on a public repository, so this needs no permission at all:

```sh
GH_TOKEN=<the token> gh pr comment <number> --body "identity check"
```

**One consequence to know before enabling required reviews.** An approval from an account without
write access does not count toward required approving reviews. Here that is fine: no reviews are
required, and the invariant is about a *comment*, which anyone may write. The day required approvals
appear, this account has to become a collaborator, and this section stops being true.

## Where the notes go

The login, the numeric id and the public half of the SSH key are local notes, not repository
content: `.gitignore` keeps them out, and none of it belongs in a public file. The token is a
credential and lives outside every checkout, in `~/.config/hyperchat/`.

One lesson worth carrying even though it cost a detour: a **global** ignore file pointed at a path
that did not exist on this platform, so it had applied to nothing. An ignore rule in the repository
beats one in a home directory precisely because it can be read, diffed and tested.

## What this costs

- **Two static credentials and a 2FA seed.** None of them can be rotated by Terraform.
- **A second identity you are accountable for**, in your name, on a public repository.
- **A public fork.** Anything pushed there is public too. It is deletable; the project's history is
  not, and that difference is the entire reason for the fork.
- **No approvals.** The agent can comment and review, and an approval from it will not count toward a
  required review, because that needs write access. Its verification is a comment.

## Teardown

Delete the fork, revoke the token, remove the SSH key from the account, and only then delete the
account, which revokes whatever is left.

There is no collaborator to remove and no Terraform resource to destroy. That absence is the design,
and it is worth noticing that teardown here means "throw away the contributor's own half" rather than
"revoke access to ours".
