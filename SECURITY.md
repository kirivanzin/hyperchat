# Reporting a vulnerability

Use **GitHub's private vulnerability reporting** on this repository — Security → Report a
vulnerability, or <https://github.com/saxeum/hyperchat/security/advisories/new>. It is enabled, so a
report reaches the maintainer without becoming public first.

Please do not open an issue for a vulnerability, and do not include a working exploit in a pull
request.

## What this repository is

A containerised hello-world and the registry it is pushed to. There is no user data here and no
service running, so the interesting classes are: anything that lets a commit reach `main` without
review, anything that gets a third-party binary into the image, and anything that publishes a
credential that was not meant to be public.

Three things are deliberately accepted, and each is written down where it is argued rather than here:

- **a public repository**, and the one static credential that provisions it —
  `docs/repository-as-code.md`
- **agent work arriving through a fork** rather than a collaborator grant — `docs/agent-identity.md`

A report naming one of those will be answered with a pointer rather than a fix.
