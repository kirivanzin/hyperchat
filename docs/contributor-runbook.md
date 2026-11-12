# The contributor's account: setup steps

The argument for this shape is `docs/contributor.md`. This is the procedure, and it is consulted
rather than read.

Everything marked *you* is out of band, once, because no API exists for it.

| Piece | Owner |
|---|---|
| Creating the account, enabling 2FA, registering the SSH key | **you, in a browser** |
| The access token | **you** |
| Access to this repository | Nothing, deliberately — the account joins by fork |
| Everything else | `make github-apply` |

## 1. A mailbox

The account needs its own address, and it becomes the recovery address, so use one that will still
exist in a year. A `+` alias on a mailbox you own is usually accepted; a separate mailbox is the
fallback.

Do not use the address of the account that owns the repository. Two accounts on one mailbox is how a
password reset for one becomes a password reset for both.

## 2. Create the account

Sign up, verify the email, and choose a login that reads the way the experiment intends. The login is
the actor in every comment and every commit, so it is worth ten seconds of thought. GitHub documents
machine users as a supported pattern; the account is yours and you are responsible for what it does.

## 3. Two-factor, before anything else

GitHub requires 2FA for accounts that contribute code. Enable TOTP and store the recovery codes in
the password manager — they are the only way back in if the device is lost, and they are a credential
in their own right. Skipping this is not a soft failure: the first push is refused, and the message
reads like a broken key.

## 4. Note the numeric account id

```sh
gh api /users/<the-new-login> --jq .id
```

It is half of the noreply address in step 7, and looking it up later is harder than noting it now.

## 5. An SSH key, kept apart from your own

```sh
ssh-keygen -t ed25519 -f ~/.ssh/hyperchat-agent -C hyperchat-agent -N ''
```

No passphrase, because the coding agent cannot type one — which means **the file is the
credential**: `chmod 600`, and it lives outside every repository.

Two accounts on `github.com` cannot both offer their keys by default, so give the contributor its
own host alias:

```
Host github-agent
  HostName github.com
  User git
  IdentityFile ~/.ssh/hyperchat-agent
  IdentitiesOnly yes
  IdentityAgent none
```

`IdentitiesOnly` and `IdentityAgent none` are what stop your own ssh-agent offering the human key
first and getting the wrong account. Register the public key on the new account, then check which
account answers:

```sh
ssh -T git@github-agent
```

`Hi <the-new-login>!` followed by a refusal of shell access is the success case.

## 6. A token for the API, not for the repository

A **classic** personal access token, `repo` scope only, expiring in 90 days.

Not fine-grained, and not by preference: a fine-grained token can only be scoped to repositories its
owner reaches through their own account or an organisation membership, and this account is
deliberately not a member, so this repository will not appear in that picker at all.

`repo` is wider than it sounds and narrower than it sounds, for the same reason: it grants what the
account can already reach, which after step 8 is one public fork. It is the scope to stop trusting
the moment this account touches a second repository.

Git goes over SSH, so the token is never a git password. It exists for forking, opening pull requests
and commenting. If it should live in the keyring rather than a file, add `read:org`, which reads
nothing on an account in no organisation.

## 7. Three identities, and only one of them is `user.name`

The three are listed with their failure modes in `docs/contributor.md`. What sets them:

Give the contributor its own checkout and let the path decide:

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

The trailing slash makes `gitdir:` a prefix match. `sshCommand` means a clone made with the wrong
remote URL still pushes as the contributor. The empty `credential.helper` **clears** an inherited
helper: if `~/.gitconfig` sets `helper = store`, a plaintext credential cache holding the other
account's token would otherwise be offered on this one's behalf, successfully and silently.

### Prove it before committing, not after

```sh
git -C ~/agent/hyperchat var GIT_AUTHOR_IDENT
git -C ~/hyperchat var GIT_AUTHOR_IDENT
```

Two different identities, with every include resolved. That is the command to reach for whenever a
commit lands under the wrong name.

### What no configuration can fix

- **A stale `GH_TOKEN` in the shell.** It overrides `gh auth`, so `gh` can act as the contributor
  while git acts as you. Nothing in git config can see it.
- **`git commit --author=`** changes the author and leaves you as committer.
- **One directory.** If both identities commit in one tree, no config can tell them apart.

## 8. No collaborator. A fork.

Terraform adds this account to nothing, and `terraform/github` says so by having no resource for it.
`docs/contributor.md` has the argument.

```sh
gh repo fork saxeum/hyperchat --clone=false
```

```sh
git remote set-url origin git@github-agent:<the login>/hyperchat.git
git remote add upstream https://github.com/saxeum/hyperchat.git
git remote set-url --push upstream DISABLED
git config remote.pushDefault origin
```

## 9. Prove it

```sh
GH_TOKEN=<the token> gh api /user --jq .login
```

The contributor's login, not yours.

```sh
GH_TOKEN=<the token> gh api /repos/saxeum/hyperchat --jq .permissions.push
```

`false` — the design in one line.

```sh
git -C ~/agent/hyperchat remote -v
```

The fork as `origin`, and `DISABLED` as upstream's push URL.

```sh
git -C ~/agent/hyperchat push --dry-run upstream HEAD:refs/heads/probe
```

Fails locally, before any network call. The same command against `origin` succeeds.

And the thing the experiment is about — a comment on a pull request, from an account that is not its
author:

```sh
GH_TOKEN=<the token> gh pr comment <number> --body "identity check"
```

**One consequence to know before enabling required reviews.** An approval from an account without
write access does not count toward required approving reviews. Here that is fine: no reviews are
required, and the invariant is about a *comment*. The day required approvals appear, this account has
to become a collaborator, and this document stops being true.

## Teardown

Delete the fork, revoke the token, remove the SSH key from the account, and only then delete the
account, which revokes whatever is left.

There is no collaborator to remove and no Terraform resource to destroy. That absence is the design.
