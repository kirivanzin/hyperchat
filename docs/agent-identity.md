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
| **The grant on this repository** | **Terraform** | `github_repository_collaborator` — the one part that would otherwise be a click. |

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

## 6. A token that cannot push code

A fine-grained personal access token, **scoped to `saxeum/hyperchat` only**, expiring in 90 days:

- `Metadata` — Read, mandatory
- `Pull requests` — Read and write, to review and approve
- `Issues` — Read and write, to comment on a pull request and on a ticket
- `Contents` — **not granted**

That last absence is the point. Git goes over SSH from step 5, so the token never needs to be usable
as a git password, and a token that cannot push code is a much smaller thing to leak than one that
can. It is also why the two credentials are worth having separately at all.

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
git clone git@github-agent:saxeum/hyperchat.git ~/soper/agent/hyperchat
```

```
# ~/.gitconfig
[includeIf "gitdir:~/soper/agent/"]
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
worktree's is `<main>/.git/worktrees/<name>`, not its working path — a rule for `~/soper/agent/`
will not fire there. Worktrees need `extensions.worktreeConfig true` and `git config --worktree`
instead, which is more parts for the same result.

### Prove it before committing, not after

```sh
git -C ~/soper/agent/hyperchat var GIT_AUTHOR_IDENT
git -C ~/soper/hyperchat var GIT_AUTHOR_IDENT
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

## 8. Let Terraform record the grant

```sh
echo 'agent_username = "<the login>"' >> terraform/github/terraform.tfvars
```

```sh
make github-apply
```

That writes an invitation — and a written invitation is the *third* state in issue #4, not a grant.
`github_repository_collaborator` reports success when the invite is sent, so `terraform plan` saying
no changes cannot tell you the agent has access. The two lists that can:

```sh
gh api /repos/saxeum/hyperchat/invitations --jq '.[] | {id, invitee: .invitee.login, permissions}'
```

names the outstanding invitation, and

```sh
gh api '/repos/saxeum/hyperchat/collaborators?affiliation=outside' --jq '[.[].login]'
```

stays empty until it is accepted. Accept it as the agent, which is the first thing the token from
step 6 is for:

```sh
GH_TOKEN=<the token> gh api -X PATCH /user/repository_invitations/<id>
```

Accepting through the email link does the same thing.

## 9. Prove it, in the places that matter

```sh
GH_TOKEN=<the token> gh api /user --jq .login
```

prints the agent's login, not yours.

```sh
GH_TOKEN=<the token> gh pr comment 1 --body "identity check"
```

is attributed to the agent — `gh pr view 1 --json comments --jq '.comments[-1].author.login'` shows
which account posted it. That is the check that matters: an approval or an evidence comment from
this account is what makes a review something other than a note to self.

```sh
gh api /orgs/saxeum --jq .plan.filled_seats
```

still prints `1`. An outside collaborator on a public repository does not consume a seat; if this
ever reads `2`, the account was added as an organisation member instead and the invitation should be
withdrawn.

## What stays local: `.secret/`

Everything about this machine that is not for publication lives in `.secret/`, which both
`.gitignore` and `.dockerignore` name. Two files today: `agent.identity`, and a README describing the
convention.

`agent.identity` holds the agent account's own details from `gh api /user` — login, numeric id, and
the public half of the SSH key. It is **not** a credential; there is no token in it.

**Credentials do not go in there.** They live outside every checkout, in `~/.config/hyperchat/` and
`~/.ssh/`. A checkout gets copied, cleaned, archived and indexed by tools that were never asked to
keep a secret, `git clean -xdf` removes ignored files, and a `tar` of the directory carries whatever
is in it. A directory called `.secret` is a drawer that is not locked: right for an identity, wrong
for a key.

**Neither does anything the project needs.** If the repository cannot be built or run without a
file, that file belongs in the repository or in `docs/`.

If a file in there ever turns up as *untracked*, check `core.excludesfile` before doubting
`.gitignore`: on the machine this was set up on it pointed at a macOS path that has never existed, so
the global ignore file had applied to nothing at all.

## What this costs

- **Two static credentials and a 2FA seed.** None of them can be rotated by Terraform.
- **A second identity you are accountable for**, in your name, on a public repository.
- **One more thing that can drift** from what `terraform/github` declares, which is why the grant is
  the part Terraform owns and `make github-status` does not show it — the collaborator list is a
  different call, and it is not yet in the status target.

## Teardown

Reverse order, and it is worth doing deliberately rather than by deleting the account first:

```sh
terraform -chdir=terraform/github apply -var 'agent_username='
```

destroys the collaborator resource. Then revoke the token, delete the SSH key from the account, and
only then delete the account, which revokes anything left.
