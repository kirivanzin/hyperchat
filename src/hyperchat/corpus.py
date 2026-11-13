"""The documents the agent may read, and the only way it may read them.

One manifest, no path argument. The model asks for a document by name from this list and cannot
construct a path, which is why traversal is refused before the filesystem is consulted at all:
`../.secret/x` is not in the manifest, and neither is `/etc/passwd`.

What is in the list and what is not is a judgement with a cost either way — `docs/contributor-runbook.md`
is about a person's machine and their keys, so it is out; the source code is a different corpus with a
different maintenance cost, and answering from code is a different claim from answering from prose.
`tests/test_corpus.py` holds both halves.

The documents are shipped with the image, so the answer and the file it came from are the same
artefact: `.dockerignore` no longer excludes the four files under `docs/` and the `Dockerfile` copies
these paths to the same relative places, which is why `ROOT` is the package's grandparent in both the
checkout and `/app`.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# The corpus, in hash order, with what each document is for. The keys are the only names the tool
# accepts, and the docstring the model sees is built from them, so the two cannot drift.
DOCUMENTS: dict[str, str] = {
    "README.md": "what the service is, and the platform it runs on",
    "SECURITY.md": "what is deliberately accepted, and where each acceptance is argued",
    "docs/authentication.md": "who may call a protected route, and what each check is for",
    "docs/repository-as-code.md": "how the repository is provisioned, and the token that costs",
    "docs/contributor.md": "the contributor principal: a fork, not a collaborator",
    "src/hyperchat/README.md": "the agent's own contract: what it does and does not do",
}


class NotInCorpus(Exception):
    """The requested name is not one of the documents, or the document is not a plain file."""


def read(name: str) -> str:
    """The text of one document, by manifest name. Nothing else is reachable."""
    if name not in DOCUMENTS:
        raise NotInCorpus(
            f"{name!r} is not in the corpus. The documents are: {', '.join(DOCUMENTS)}."
        )
    path = ROOT / name
    # A symlink inside the corpus is refused rather than followed: the manifest names files, and a
    # link is a name that can point anywhere, including outside the image's copy of the corpus.
    if path.is_symlink():
        raise NotInCorpus(f"{name!r} is a symlink, and the corpus is plain files.")
    resolved = path.resolve()
    if not resolved.is_relative_to(ROOT.resolve()):
        raise NotInCorpus(f"{name!r} resolves outside the corpus.")
    # What the hash covers is what the tool returns: one corpus, not two.
    return stable_content(name).decode("utf-8")


# Lines a generator writes into a corpus document. They are excluded from the hash because they are
# a pointer to a report rather than documentation: including them makes the hash change every time
# the report is regenerated, which is the one thing the hash must not do — a moved hash has to mean a
# moved document.
GENERATED = ("**Latest eval:**",)


def stable_content(name: str) -> bytes:
    """The bytes of one document, without any generated line. The one view of the corpus.

    Both the tool and the hash read through this. Otherwise there are two corpora — the one the agent
    reads and the one the hash covers — and the agent could quote a line the hash does not account
    for, leaving "a moved hash means a moved document" as "…except that line".
    """
    raw = (ROOT / name).read_bytes()
    if not any(marker in raw.decode("utf-8", "replace") for marker in GENERATED):
        return raw
    kept = [
        line
        for line in raw.decode("utf-8").splitlines(keepends=True)
        if not line.startswith(GENERATED)
    ]
    return "".join(kept).encode("utf-8")


def corpus_sha256() -> str:
    """One hash over every document's name and bytes, in manifest order.

    Recorded at startup and on every eval record beside `prompt_sha256`, because a score against a
    different corpus is a different subject in exactly the way a score against a different prompt is.
    """
    digest = hashlib.sha256()
    for name in DOCUMENTS:
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(stable_content(name))
        digest.update(b"\0")
    return digest.hexdigest()


def listing() -> str:
    """The manifest as the model sees it: one `name - what it is for` per line."""
    return "\n".join(f"- {name} - {purpose}" for name, purpose in DOCUMENTS.items())
