"""The configuration a score was produced under, as one object and one hash.

A record that carries the prompt and the case and not the model, the temperature, the caps or the
bounds cannot say what a moved score moved *because of*. Two runs at different `PROVIDER_MAX_TOKENS`,
or on a different model, produce records that look identical.

So: nine resolved values, one hash over them, and one greppable line. The key is deliberately not a
field — a configuration that can carry a credential is a configuration that ends up in an issue body
with the rest of it.

**Resolved, not requested.** These are the values the process uses: defaults applied, and anything
`bounds()` would refuse already refused. A block copied from the environment would be a claim about
the shell rather than about the run.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from hashlib import sha256
from typing import Any

from hyperchat import corpus
from hyperchat.agent import prompt_sha256
from hyperchat.config import bounds, provider_settings


@dataclass(frozen=True)
class Configuration:
    """Everything that can change an answer, and nothing that identifies a caller."""

    model: str
    max_tokens: int
    temperature: float
    timeout_seconds: float
    max_steps: int
    budget_seconds: float
    rate_per_minute: int
    prompt_sha256: str
    corpus_sha256: str

    def as_dict(self) -> dict[str, Any]:
        """Field order is the dataclass's, which is the order the hash and the line use."""
        return {field.name: getattr(self, field.name) for field in fields(self)}

    def sha256(self) -> str:
        """One string over nine values, so "at this configuration" is one comparison.

        Lengths are hashed with each value: `a=1,b=23` and `a=12,b=3` concatenate to the same bytes
        otherwise, and two different configurations would share a hash.
        """
        digest = sha256()
        for name, value in self.as_dict().items():
            for part in (name, str(value)):
                digest.update(part.encode("utf-8"))
                digest.update(b"\0")
        return digest.hexdigest()

    def attributes(self) -> str:
        """The nine values as `k=v` pairs, in the same order as the hash."""
        return " ".join(f"{name}={value}" for name, value in self.as_dict().items())


def resolved() -> Configuration:
    """What the process is actually running with, read from the same two functions that enforce it."""
    provider = provider_settings()
    limits = bounds()
    return Configuration(
        model=provider.model,
        max_tokens=provider.max_tokens,
        temperature=provider.temperature,
        timeout_seconds=provider.timeout_seconds,
        max_steps=limits.max_steps,
        budget_seconds=limits.budget_seconds,
        rate_per_minute=limits.rate_limit_per_minute,
        prompt_sha256=prompt_sha256(),
        corpus_sha256=corpus.corpus_sha256(),
    )


def line(prefix: str, **extra: Any) -> str:
    """`<prefix> config model=… … config_sha256=…`, with any run-specific keys after the nine.

    One format for the service and the harness, so one `grep` answers "what is running" and "what did
    this score measure" — and `parse` reads either.
    """
    block = resolved()
    parts = [f"{prefix} config", block.attributes(), f"config_sha256={block.sha256()}"]
    parts += [f"{name}={value}" for name, value in extra.items()]
    return " ".join(parts)


def parse(line_text: str) -> dict[str, str]:
    """Split a configuration line into its `k=v` pairs, whichever prefix wrote it.

    Everything before the first pair is the prefix — `eval config` or `service config` — and is not
    returned, which is what lets a test read both with the same function.
    """
    pairs: dict[str, str] = {}
    for token in line_text.split():
        name, separator, value = token.partition("=")
        if separator:
            pairs[name] = value
    return pairs
