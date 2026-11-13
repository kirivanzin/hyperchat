"""The corpus and the one tool: what is reachable, and what is refused before the filesystem is asked.

The review of this ticket attacks the tool directly rather than through the model, because a tool
that refuses through the prompt is a tool that does not refuse. So these ask for the three shapes
the ticket names — traversal, an absolute path, a symlink — plus the documents that are deliberately
out of the corpus, and check the hash moves when a document does.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

import pytest

from hyperchat import agent, corpus

REPO = Path(__file__).resolve().parents[1]


def test_every_document_in_the_manifest_reads():
    for name in corpus.DOCUMENTS:
        text = corpus.read(name)
        assert text.strip(), f"{name} is in the manifest and empty"


@pytest.mark.parametrize(
    "name",
    [
        "../.secret/x",
        "../../etc/passwd",
        "/etc/passwd",
        "~/.config/hyperchat/agent-deepseek.token",
        # In the checkout, deliberately not in the corpus: a person's machine and their keys.
        "docs/contributor-runbook.md",
        # A different corpus with a different maintenance cost.
        "src/hyperchat/agent.py",
        "pyproject.toml",
    ],
)
def test_a_name_outside_the_manifest_is_refused(name):
    with pytest.raises(corpus.NotInCorpus, match="not in the corpus"):
        corpus.read(name)


def test_a_symlink_is_refused_rather_than_followed(tmp_path, monkeypatch):
    """A link is a name that can point anywhere, including outside the image's copy of the corpus."""
    target = tmp_path / "outside.txt"
    target.write_text("the token is in here\n")
    (tmp_path / "README.md").symlink_to(target)

    monkeypatch.setattr(corpus, "ROOT", tmp_path)
    monkeypatch.setitem(corpus.DOCUMENTS, "README.md", "a symlink, for this test")

    with pytest.raises(corpus.NotInCorpus, match="symlink"):
        corpus.read("README.md")


def test_the_hash_is_over_the_names_and_the_bytes(tmp_path, monkeypatch):
    """A changed document has to move the hash, or the record cannot say what the agent knew."""
    (tmp_path / "README.md").write_text("first version\n")
    monkeypatch.setattr(corpus, "ROOT", tmp_path)
    monkeypatch.setattr(corpus, "DOCUMENTS", {"README.md": "the only document, for this test"})
    before = corpus.corpus_sha256()

    (tmp_path / "README.md").write_text("second version\n")
    after = corpus.corpus_sha256()

    assert before != after
    assert len(before) == 64


def test_the_model_is_told_the_names_and_is_not_offered_a_path():
    """The tool's description is the manifest, so the model's vocabulary is the corpus's."""
    description = agent.read_document.description
    for name in corpus.DOCUMENTS:
        assert name in description
    # The tool takes exactly one argument, and it is a name.
    assert list(agent.read_document.args) == ["name"]


def test_the_tool_itself_refuses_a_path_without_help_from_the_model():
    """Called directly, not through the graph: the refusal has to be in the tool, not in the prompt."""
    with pytest.raises(corpus.NotInCorpus):
        agent.read_document.invoke({"name": "../.secret/x"})


def test_every_document_in_the_manifest_reaches_the_image():
    """A corpus that is not in the image passes every local test and helps nobody.

    This is the check that runs where there is no docker: the manifest, the `COPY` lines and the
    ignore file have to agree, and the failure they prevent is a document that exists in the checkout
    and not in `/app` — which no test in the checkout can otherwise see.
    """
    dockerfile = (REPO / "Dockerfile").read_text()
    copies = " ".join(line for line in dockerfile.splitlines() if line.startswith("COPY "))
    rules = [
        line.strip()
        for line in (REPO / ".dockerignore").read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    ignored = [rule for rule in rules if not rule.startswith("!")]
    unignored = {rule[1:] for rule in rules if rule.startswith("!")}

    def excluded(name: str) -> bool:
        for rule in ignored:
            pattern = rule.rstrip("/")
            if rule.endswith("/") and name.startswith(pattern + "/"):
                return True
            if fnmatch.fnmatch(name, pattern):
                return True
        return False

    for name in corpus.DOCUMENTS:
        assert (REPO / name).is_file(), f"{name} is in the manifest and not in the checkout"
        if name.startswith("src/"):
            assert "COPY src ./src" in copies, "the source tree is not copied into the image"
            continue
        assert name in copies, f"{name} is in the manifest and no COPY line puts it in the image"
        assert not excluded(name) or name in unignored, (
            f"{name} is copied and .dockerignore keeps it out of the context: "
            f"docker would fail, or ship a corpus with a hole in it"
        )


def test_the_manifest_is_not_empty_and_the_tool_description_matches_it():
    """The model's vocabulary is the manifest: a name it cannot ask for is a document it cannot read."""
    description = agent.read_document.description
    assert len(corpus.DOCUMENTS) == sum(
        1 for line in description.splitlines() if line.startswith("- ")
    )


def test_the_tool_and_the_hash_see_the_same_bytes():
    """One corpus, not two: what the agent can quote is exactly what the hash covers.

    The generated README line is excluded from the hash — otherwise regenerating the report moves the
    corpus — and if `read()` did not go through the same function, the agent could quote a line the
    hash does not account for, and a moved hash would mean "a moved document, except that line".
    """
    for name in corpus.DOCUMENTS:
        assert corpus.read(name).encode("utf-8") == corpus.stable_content(name), name
    # And the generated line is genuinely absent from both, where it exists at all.
    if "**Latest eval:**" in (REPO / "README.md").read_text():
        assert "**Latest eval:**" not in corpus.read("README.md")
        assert b"**Latest eval:**" not in corpus.stable_content("README.md")
