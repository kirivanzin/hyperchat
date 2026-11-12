"""The two values this service refuses to start without.

Read at import, and failing here is the point. A process that boots with an empty allowlist and
answers 403 to everybody looks exactly like a healthy one to a readiness probe, so the mistake
becomes a support ticket instead of a crash. There are no defaults for either value: a default is an
allowlist somebody inherits without deciding.

Nothing here reads a file. `.env` is read by the `Makefile`, which exports it into the environment of
`make dev` and hands it to `make run` as `--env-file`; the application only ever sees the environment
it was given. That keeps a missing value a crash rather than something a file could quietly fill in.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DOCS = "docs/authentication.md"


@dataclass(frozen=True)
class Settings:
    """What the trust boundary needs, and nothing else."""

    expected_audience: str
    allowed_subs: frozenset[str]


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"{name} is unset and has no default. The service refuses to start without it: an "
            f"empty allowlist serves 403 to everybody while readiness stays green. Set it in "
            f".env (see {DOCS}) or in the environment of whatever is starting this process."
        )
    return value


def load_settings() -> Settings:
    audience = _required("EXPECTED_AUDIENCE")
    configured = _required("ALLOWED_SUBS")
    subs = frozenset(part.strip() for part in configured.split(",") if part.strip())
    if not subs:
        raise RuntimeError(
            f"ALLOWED_SUBS={configured!r} contains no subject: it is comma-separated and every "
            f"entry is blank, which is the empty allowlist by another name. See {DOCS}."
        )
    return Settings(expected_audience=audience, allowed_subs=subs)


settings = load_settings()
