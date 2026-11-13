"""The values this service needs, and the difference between the ones it refuses to start without.

Two groups, deliberately not one:

- **The trust boundary** — `EXPECTED_AUDIENCE` and `ALLOWED_SUBS`. `load_settings()` fails without
  them, and the app calls it at import. A process that booted with an empty allowlist would answer
  403 to everybody without looking broken, so the failure has to be a crash rather than a
  healthy-looking process nobody can use.
- **The model** — `provider_settings()`. The key is optional: absent means "the model is off",
  answered 503, not a refusal to start.
- **The bounds** — `bounds()`. A run has a step ceiling and a wall-clock budget, and one caller has a
  rate. Two values are read together because they constrain each other: a time budget shorter than a
  single provider call is a budget that always fails, which is a permanently broken endpoint rather
  than a strict one. Read on demand rather than at import, because the graph has
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
    # `ChatDeepSeek`'s own default is the provider's, not ours: pinning it is what makes a run
    # repeatable and a change to the default a change this repository makes.
    temperature: float = 0.0
    # Per attempt. The client retries twice by default, so the worst case is three of these and the
    # platform's request cap is 300s: a timeout that cannot fire first is not a timeout.
    timeout_seconds: float = 20.0


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"{name} is unset and has no default. The service refuses to start without it: an "
            f"empty allowlist serves 403 to everybody without looking broken. Set it in "
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


def _temperature(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise RuntimeError(f"{name}={raw!r} is not a number.") from None
    if not 0.0 <= value <= 2.0:
        raise RuntimeError(
            f"{name}={value} is outside 0.0-2.0, which is what the provider accepts."
        )
    return value


@dataclass(frozen=True)
class Bounds:
    """What stops a run, and what stops one caller. Read together because they constrain each other."""

    max_steps: int
    budget_seconds: float
    rate_limit_per_minute: int


def _positive_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise RuntimeError(f"{name}={raw!r} is not a number.") from None
    if value <= 0:
        raise RuntimeError(f"{name}={value} is not a positive number of seconds.")
    return value


def bounds() -> Bounds:
    """The run bounds. Two of these refusals are judgement, not tidiness.

    A step ceiling of zero and a time budget shorter than one provider call both present as a
    permanently broken endpoint: the first run fails, every time, for a reason that reads like a code
    defect. Refusing to start names the value instead.
    """
    provider = provider_settings()
    max_steps = _positive_int("AGENT_MAX_STEPS", 6)
    budget_seconds = _positive_float("AGENT_BUDGET_SECONDS", 60.0)
    if budget_seconds < provider.timeout_seconds:
        raise RuntimeError(
            f"AGENT_BUDGET_SECONDS={budget_seconds} is shorter than PROVIDER_TIMEOUT_SECONDS="
            f"{provider.timeout_seconds}: every run would hit its budget during the first call, "
            f"which is a broken endpoint rather than a strict one."
        )
    return Bounds(
        max_steps=max_steps,
        budget_seconds=budget_seconds,
        rate_limit_per_minute=_positive_int("RATE_LIMIT_PER_MINUTE", 10),
    )


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
    decision; the key has none, because whether the model is on at all is one. `PROVIDER_TEMPERATURE`
    has one, and it is 0.0: the point is that the value is written here rather than inherited from the
    provider's default."""
    return Provider(
        model=os.environ.get("PROVIDER_MODEL", "").strip() or "deepseek-chat",
        max_tokens=_positive_int("PROVIDER_MAX_TOKENS", 640),
        api_key=os.environ.get("PROVIDER_API_KEY", "").strip() or None,
        temperature=_temperature("PROVIDER_TEMPERATURE", 0.0),
        timeout_seconds=_positive_float("PROVIDER_TIMEOUT_SECONDS", 20.0),
    )
