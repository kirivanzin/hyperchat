"""`scripts/deployable.sh` — does this merge queue a deploy, or not?

The deployed artefact is made of the source, the image definition, the dependency pin, the
interpreter, `terraform/app` and this workflow. Everything else can land without queuing a build, a
push under a new tag, and a request for a human to approve the production environment.

The case that matters is `false`: a filter that says `true` for everything passes its own tests and
changes nothing (issue #75). So most of this file is the not-deployable half, including the paths
that look like they should deploy and do not — `Makefile` above all, because a change to
`make deploy` is not a change to what is deployed.

These run against a temporary repository rather than this one's history: `actions/checkout` is
shallow, so the commits the ticket names are not in a CI clone. The two real commits are checked
where they happen to be present, and skipped where they are not.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "deployable.sh"


def git(cwd: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def commit(cwd: Path, path: str) -> str:
    """One commit that creates `path`, and its sha."""
    target = cwd / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x\n")
    git(cwd, "add", "--", path)
    git(cwd, "commit", "-q", "-m", f"add {path}")
    return git(cwd, "rev-parse", "HEAD")


def verdict(cwd: Path, base: str, head: str) -> tuple[str, int, str]:
    done = subprocess.run(
        [str(SCRIPT), base, head], cwd=cwd, capture_output=True, text=True, check=False
    )
    return done.stdout.strip(), done.returncode, done.stderr


@pytest.fixture
def base_commit(tmp_path: Path) -> tuple[Path, str]:
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "test")
    return tmp_path, commit(tmp_path, "base.txt")


@pytest.mark.parametrize(
    "path",
    [
        "src/hyperchat/main.py",
        "src/hyperchat/agent.py",
        "Dockerfile",
        "pyproject.toml",
        "uv.lock",
        ".python-version",
        "terraform/app/main.tf",
        ".github/workflows/deploy.yml",
    ],
)
def test_a_change_to_what_runs_deploys(base_commit, path):
    cwd, base = base_commit
    assert verdict(cwd, base, commit(cwd, path))[0] == "true"


@pytest.mark.parametrize(
    "path",
    [
        # A change to `make deploy` is not a change to what is deployed.
        "Makefile",
        "README.md",
        "SECURITY.md",
        "docs/authentication.md",
        "evals/cases.jsonl",
        "scripts/smoke.sh",
        "tests/test_chat.py",
        ".github/workflows/ci.yml",
        # Applied by hand, once; a merge does not touch them.
        "terraform/github/main.tf",
        "terraform/registry/main.tf",
    ],
)
def test_a_change_to_something_else_does_not_deploy(base_commit, path):
    cwd, base = base_commit
    assert verdict(cwd, base, commit(cwd, path))[0] == "false"


def test_several_paths_where_one_is_deployable_deploys(base_commit):
    cwd, base = base_commit
    (cwd / "README.md").write_text("docs\n")
    (cwd / "src").mkdir()
    (cwd / "src" / "hyperchat").mkdir()
    (cwd / "src" / "hyperchat" / "main.py").write_text("x\n")
    git(cwd, "add", "-A")
    git(cwd, "commit", "-q", "-m", "docs and source")
    head = git(cwd, "rev-parse", "HEAD")

    out, code, err = verdict(cwd, base, head)
    assert (out, code) == ("true", 0)
    # The diff is on stderr, so a terminal run says what it saw and not only what it concluded.
    assert "README.md" in err and "src/hyperchat/main.py" in err


def test_no_change_is_false_rather_than_an_error(base_commit):
    cwd, base = base_commit
    out, code, err = verdict(cwd, base, base)
    assert (out, code) == ("false", 0)
    assert "no files changed" in err


def test_a_revision_that_does_not_resolve_is_not_a_verdict(base_commit):
    """ "Cannot tell" must never read as "false": the workflow would skip a deploy it could not check."""
    cwd, base = base_commit
    out, code, err = verdict(cwd, "deadbeefdeadbeef", base)
    assert code == 2
    assert out == ""
    assert "cannot resolve" in err


def test_usage_needs_two_revisions(base_commit):
    cwd, base = base_commit
    done = subprocess.run([str(SCRIPT), base], cwd=cwd, capture_output=True, text=True, check=False)
    assert done.returncode == 2
    assert "usage:" in done.stderr


def test_the_two_commits_the_ticket_names(tmp_path):
    """The acceptance pair, against this repository's own history when the clone has it."""
    for revision in ("ad5fe91", "eb37b42"):
        found = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
        )
        if found.returncode != 0:
            pytest.skip(f"{revision} is not in this clone (shallow checkout)")

    # ad5fe91 is the eval harness, which touched nothing deployable ...
    assert verdict(REPO, "ad5fe91^1", "ad5fe91")[0] == "false"
    # ... and eb37b42 changed `src/`, which is the whole of what deploys.
    assert verdict(REPO, "eb37b42^1", "eb37b42")[0] == "true"


def test_the_workflow_asks_the_script_and_bypasses_it_on_dispatch():
    """The filter is only worth its tests if the workflow is wired to it.

    Read as text rather than parsed: YAML is not a declared dependency here, and this is the one
    claim in the file that a reader can check by opening the workflow beside it.
    """
    workflow = (REPO / ".github/workflows/deploy.yml").read_text()
    assert "needs: changed" in workflow, "the deploy job does not wait for the filter"
    assert "scripts/deployable.sh" in workflow, "the filter job does not run the script"
    assert "needs.changed.outputs.deployable == 'true'" in workflow, "the filter is not the gate"
    assert "workflow_dispatch: the filter is bypassed on purpose" in workflow
