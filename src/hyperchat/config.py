"""The values this service needs, and the difference between the ones it refuses to start without.

Two groups, deliberately not one:

- **The trust boundary** — `EXPECTED_AUDIENCE` and `ALLOWED_SUBS`. `load_settings()` fails without
  them, and the app calls it at import. A process that booted with an empty allowlist would answer
  403 to everybody while its readiness probe stayed green, so the failure has to be a crash rather
  than a healthy-looking process nobody can use.
- **The model** — `provider_settings()`. The key is optional: absent means "the model is off",
  answered 503, not a refusal to start. Read on demand rather than at import, because the graph has
  to be importable — `make chat`, `langgraph dev` and the acceptance command all do that — without an
  allowlist or a key, and a debugging tool that needs the production trust boundary configured is a
  tool nobody runs.

Nothing here reads a file. `.env` is read by the `Makefile`, which exports it into the environment of
`make dev` and `make chat`, and hands it to `make run` as `--env-file`; the application only ever sees
the environment it was given. That keeps a missing value a crash rather than something a file could
quietly fill in.
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


@dataclass(frozen=True)
class Provider:
    """What the one model call needs. Separate from `Settings` for the reason in the docstring."""

    model: str
    max_tokens: int
    api_key: str | None


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"{name} is unset and has no default. The service refuses to start without it: an "
            f"empty allowlist serves 403 to everybody while readiness stays green. Set it in "
            f".env (see {DOCS}) or in the environment of whatever is starting this process."
        )
    return value


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise RuntimeError(f"{name}={raw!r} is not a whole number.") from None
    if value <= 0:
        raise RuntimeError(f"{name}={value} is not a positive number of tokens.")
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


def provider_settings() -> Provider:
    """The model's configuration. `PROVIDER_MODEL` has a default because changing it is not a safety
    decision; the key has none, because whether the model is on at all is one."""
    return Provider(
        model=os.environ.get("PROVIDER_MODEL", "").strip() or "deepseek-chat",
        max_tokens=_positive_int("PROVIDER_MAX_TOKENS", 256),
        api_key=os.environ.get("PROVIDER_API_KEY", "").strip() or None,
    )
