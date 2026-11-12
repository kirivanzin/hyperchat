"""`make github-status` names who can reach the repository, including when the answer is nobody.

The live command needs a token with admin on the repository, and the answers that matter here —
"none", "one outside collaborator", "an invitation nobody accepted" — are not answers a token
without that access can ask for. A stub `gh` supplies all three, so the property this ticket is
about is testable without write access to the repository: **the line appears when the answer is
nothing**, because a check that omits the line cannot tell nothing from not-asked.

Delete any of the `report` lines from the target and these fail. That is the point.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MAKE = shutil.which("make")

pytestmark = pytest.mark.skipif(
    MAKE is None, reason="make is not installed: github-status is a Makefile target"
)

# `gh api <path> [--jq <expr>]`, answered from $FAKE_GH. `<key>.json` is the response body,
# `<key>.error` is a refusal printed to stderr with status 1, and no file at all is gh's 404.
# `jq -c -r` is what `gh api --jq` does with the expression, so the stub prints what gh prints.
STUB = """#!/bin/sh
path="$2"
expr="$4"
key=$(printf '%s' "$path" | tr '/?=' '___')
if [ -f "$FAKE_GH/$key.error" ]; then
	cat "$FAKE_GH/$key.error" >&2
	exit 1
fi
if [ ! -f "$FAKE_GH/$key.json" ]; then
	printf 'gh: Not Found (HTTP 404)\\n' >&2
	exit 1
fi
if [ -n "$expr" ]; then jq -c -r "$expr" "$FAKE_GH/$key.json"; else cat "$FAKE_GH/$key.json"; fi
"""

# The endpoints that do not change between the cases, so a test names only the part it is about.
READABLE = {
    "/repos/acme/hyperchat": (
        '{"full_name":"acme/hyperchat","visibility":"public",'
        '"security_and_analysis":{"secret_scanning":{"status":"enabled"},'
        '"secret_scanning_push_protection":{"status":"enabled"}}}'
    ),
    "/repos/acme/hyperchat/branches/main/protection": (
        '{"enforce_admins":{"enabled":true},"allow_force_pushes":{"enabled":false},'
        '"allow_deletions":{"enabled":false},"required_pull_request_reviews":null,'
        '"required_status_checks":null}'
    ),
    "/orgs/acme": '{"login":"acme","plan":{"filled_seats":1}}',
}

OUTSIDE = "/repos/acme/hyperchat/collaborators?affiliation=outside"
INVITATIONS = "/repos/acme/hyperchat/invitations"


def run_status(tmp_path, answers, refusals=None):
    """Run the target with a stub gh, and return what a reader would see."""
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    (stub_dir / "gh").write_text(STUB)
    (stub_dir / "gh").chmod(0o755)

    answers_dir = tmp_path / "answers"
    answers_dir.mkdir()
    for path, body in (READABLE | answers).items():
        (answers_dir / f"{key(path)}.json").write_text(body)
    for path, message in (refusals or {}).items():
        (answers_dir / f"{key(path)}.error").write_text(message)

    env = dict(os.environ, PATH=f"{stub_dir}:{os.environ['PATH']}", FAKE_GH=str(answers_dir))
    env.pop("MAKEFLAGS", None)
    return subprocess.run(
        [MAKE, "-f", str(REPO / "Makefile"), "github-status", "FULL_NAME=acme/hyperchat"],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
    )


def key(path):
    return path.replace("/", "_").replace("?", "_").replace("=", "_")


def value_of(output, label):
    """The value printed on `label`'s line, and a failure if the line is absent entirely."""
    match = re.search(rf"^  {re.escape(label)} +(.*)$", output, re.MULTILINE)
    assert match, f"no {label!r} line was printed:\n{output}"
    return match.group(1).strip()


def test_the_absence_case_names_none_instead_of_saying_nothing(tmp_path):
    result = run_status(tmp_path, {OUTSIDE: "[]", INVITATIONS: "[]"})

    assert result.returncode == 0, result.stdout + result.stderr
    assert value_of(result.stdout, "outside collaborators") == "none"
    assert value_of(result.stdout, "pending invitations") == "none"
    assert value_of(result.stdout, "org filled seats") == "1"


def test_a_grant_and_an_unaccepted_invitation_are_both_named(tmp_path):
    result = run_status(
        tmp_path,
        {
            OUTSIDE: '[{"login":"agent","role_name":"push"}]',
            INVITATIONS: '[{"id":42,"invitee":{"login":"agent"}}]',
            "/orgs/acme": '{"login":"acme","plan":{"filled_seats":2}}',
        },
    )

    assert result.returncode == 0, result.stdout + result.stderr
    # `role_name` is what distinguishes a read grant from one that can publish.
    assert value_of(result.stdout, "outside collaborators") == "agent (push)"
    # The id, because an invitation that has not been accepted has no collaborator to name.
    assert value_of(result.stdout, "pending invitations") == "42 (agent)"
    # A seat going 1 -> 2 is the grant that is membership rather than a collaborator.
    assert value_of(result.stdout, "org filled seats") == "2"


def test_a_refusal_is_unreadable_and_not_none(tmp_path):
    refused = "gh: Must have push access to view repository collaborators. (HTTP 403)"
    result = run_status(
        tmp_path,
        {OUTSIDE: "[]", INVITATIONS: "[]"},
        refusals={OUTSIDE: refused},
    )

    assert result.returncode != 0, "a blind read reported success"
    assert value_of(result.stdout, "outside collaborators") == f"unreadable: {refused}"
    # And the refusal did not stop the lines after it: that is what "prints in every case" needs.
    assert value_of(result.stdout, "pending invitations") == "none"
