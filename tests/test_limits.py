"""The two run bounds and the one caller bound, without a provider or a request.

`bounds()` is read at the edge the first time a run or a request needs it, so the refusals here are
what stop a service booting into an endpoint that can never answer: a step ceiling of zero and a time
budget shorter than one provider call both fail every run, for a reason that reads like a code
defect.
"""

from __future__ import annotations

import pytest

from hyperchat.config import Bounds, bounds, provider_settings
from hyperchat.limits import WINDOW_SECONDS, RateLimiter


class Clock:
    """A clock a test can move, so the window is exercised without sleeping a minute."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_a_caller_gets_the_window_and_then_a_retry_after():
    clock = Clock()
    limiter = RateLimiter(per_minute=3, clock=clock)

    assert [limiter.check("sub-1") for _ in range(3)] == [None, None, None]
    refused = limiter.check("sub-1")
    assert refused == int(WINDOW_SECONDS), "the window has just started, so the wait is all of it"


def test_the_window_moves_on_so_the_refusal_does_not_become_permanent():
    clock = Clock()
    limiter = RateLimiter(per_minute=1, clock=clock)

    assert limiter.check("sub-1") is None
    clock.now += WINDOW_SECONDS
    assert limiter.check("sub-1") is None, "a limiter that never forgets is an outage with a delay"


def test_retry_after_is_never_zero():
    """`Retry-After: 0` says "ask again now", and the caller does, and is refused again."""
    clock = Clock()
    limiter = RateLimiter(per_minute=1, clock=clock)

    limiter.check("sub-1")
    clock.now += WINDOW_SECONDS - 0.1
    refused = limiter.check("sub-1")

    assert refused is not None and refused >= 1


def test_two_subjects_do_not_share_a_window():
    """The key is the subject, not the token: one caller minting a second token is the same budget."""
    clock = Clock()
    limiter = RateLimiter(per_minute=1, clock=clock)

    assert limiter.check("sub-1") is None
    assert limiter.check("sub-1") is not None
    assert limiter.check("sub-2") is None


def test_the_defaults_are_the_ones_the_table_names():
    assert bounds() == Bounds(max_steps=6, budget_seconds=60.0, rate_limit_per_minute=10)
    assert provider_settings().timeout_seconds == 20.0


def test_a_step_ceiling_of_zero_is_refused(monkeypatch):
    monkeypatch.setenv("AGENT_MAX_STEPS", "0")
    with pytest.raises(RuntimeError, match="not a positive number"):
        bounds()


def test_a_budget_shorter_than_one_provider_call_is_refused(monkeypatch):
    """It would hit its budget during the first call, every time, which is a broken endpoint."""
    monkeypatch.setenv("PROVIDER_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("AGENT_BUDGET_SECONDS", "10")
    with pytest.raises(RuntimeError, match="shorter than PROVIDER_TIMEOUT_SECONDS"):
        bounds()


def test_a_rate_of_zero_is_refused(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "0")
    with pytest.raises(RuntimeError, match="not a positive number"):
        bounds()


def test_a_provider_timeout_of_zero_is_refused(monkeypatch):
    monkeypatch.setenv("PROVIDER_TIMEOUT_SECONDS", "0")
    with pytest.raises(RuntimeError, match="not a positive number of seconds"):
        provider_settings()


def test_a_provider_timeout_that_is_not_a_number_is_named(monkeypatch):
    monkeypatch.setenv("PROVIDER_TIMEOUT_SECONDS", "soon")
    with pytest.raises(RuntimeError, match="is not a number"):
        provider_settings()
