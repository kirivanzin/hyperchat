#!/bin/sh
# Can this commit change what runs? `true` or `false`, from the diff and nothing else.
#
#   scripts/deployable.sh BASE HEAD
#
# A merge to `main` queues a deploy, and that deploy asks a human to approve the production
# environment before it can start. That is right for a change to the service and wrong for the rest:
# `ad5fe91` (the eval harness) touched no `src/`, no `Dockerfile`, no dependency and no
# `terraform/app`, and it still queued a build, a push under a new tag, and an approval request. The
# deploy was cancelled; the queue was still the defect.
#
# The set below is the judgement this repository reserves. It is what the deployed artefact is made
# of: the source, the image definition, the dependency pin, the interpreter, the Terraform that
# describes the service, and this workflow — which is not copied into the image but does decide what
# the deploy does once it runs.
#
# Deliberately NOT deployable, each with the argument for its absence:
#
#   Makefile        a change to `make deploy` is not a change to what is deployed, and the next
#                   deployable merge exercises it
#   README.md       documentation
#   docs/           documentation
#   evals/          a case set that runs on a laptop; `.dockerignore` keeps it out of the image
#   scripts/        developer commands, also kept out of the image
#   tests/          kept out of the image
#   SECURITY.md     documentation
#   terraform/github, terraform/registry
#                   applied by hand, once, and untouched by a merge
#
# If a path is missing from that list, adding it is a change to this list and should say which merge
# it would have wrongly skipped.
#
# Exit 0 with `true` or `false` on stdout either way, because the verdict is the answer and not the
# status. The diff is printed on stderr so `$(scripts/deployable.sh a b)` still reads one word, and a
# terminal run says what it saw rather than only what it concluded. A base or head that does not
# resolve is exit 2 with git's message, never a verdict: "cannot tell" must not read as "false".
set -eu

if [ "$#" -ne 2 ]; then
	echo "usage: scripts/deployable.sh BASE HEAD" >&2
	exit 2
fi
# Resolved first, and with `--end-of-options`, so a revision that happens to start with a dash is a
# revision and not a flag, and so a name that does not exist fails here rather than through git's
# 128 — the callers check this exit code.
for rev in "$1" "$2"; do
	if ! git rev-parse --verify --quiet --end-of-options "$rev^{commit}" >/dev/null; then
		echo "cannot resolve $rev: nothing is claimed about this diff" >&2
		exit 2
	fi
done
base=$(git rev-parse --verify --end-of-options "$1^{commit}")
head=$(git rev-parse --verify --end-of-options "$2^{commit}")

files=$(git diff --name-only "$base" "$head")
if [ -n "$files" ]; then
	printf '%s\n' "$files" >&2
else
	echo "no files changed between $base and $head" >&2
fi

# One case pattern per deployable path, in the same order as the comment above, so the two cannot
# drift without the diff being obvious.
verdict=false
while IFS= read -r file; do
	case "$file" in
	src/* | Dockerfile | pyproject.toml | uv.lock | .python-version | terraform/app/* | \
		.github/workflows/deploy.yml)
		verdict=true
		;;
	esac
done <<EOF
$files
EOF

echo "$verdict"
