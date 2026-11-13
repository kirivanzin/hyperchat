"""The configuration a score was produced under: one block, one hash, no key.

The defect this closes is the one the repository keeps finding — a number without its input. A record
that carries the prompt and the case, and not the model, the temperature, the caps or the bounds,
cannot say what a moved score moved because of.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from hyperchat import configuration
from hyperchat.config import bounds, provider_settings

REPO = Path(__file__).resolve().parents[1]


def test_a_different_cap_is_a_different_configuration(monkeypatch):
    """The hash is over the values, not over the file or the shell that named them."""
    monkeypatch.delenv("PROVIDER_MAX_TOKENS", raising=False)
    before = configuration.resolved().sha256()

    monkeypatch.setenv("PROVIDER_MAX_TOKENS", "8")
    after = configuration.resolved().sha256()

    assert before != after
    assert configuration.parse(configuration.line("eval"))["config_sha256"] == after


def test_the_block_is_the_resolved_values_not_the_environment(monkeypatch):
    """A setting whose default was applied is recorded at its default, not at the variable's absence."""
    monkeypatch.delenv("AGENT_MAX_STEPS", raising=False)
    resolved = configuration.resolved()

    assert resolved.max_steps == bounds().max_steps
    assert resolved.max_tokens == provider_settings().max_tokens

    monkeypatch.setenv("AGENT_MAX_STEPS", "3")
    assert configuration.resolved().max_steps == 3
    assert configuration.resolved().sha256() != resolved.sha256()


def test_no_part_of_the_provider_key_is_in_the_block(monkeypatch):
    """The shape that gets pasted into a log with the rest of it is the one that has everything in it."""
    key = "sk-test-3f9a2b7c1d4e5f60718293a4b5c6d7e8"
    monkeypatch.setenv("PROVIDER_API_KEY", key)
    monkeypatch.setenv("PROVIDER_MODEL", "deepseek-chat")

    block = configuration.line("service", overrides="")
    serialised = f"{block} {configuration.resolved().as_dict()}"

    assert key not in serialised
    # Not even a piece of it: a redacted-but-partial key is still a key, and 8 characters is where
    # an accidental log line becomes a guessable one.
    assert not any(key[offset : offset + 8] in serialised for offset in range(len(key) - 8))
    assert "PROVIDER_API_KEY" not in serialised


def test_the_service_line_and_the_eval_line_parse_with_the_same_parser(monkeypatch):
    """One grep has to answer "what is running" and "what did this score measure"."""
    service = configuration.parse(configuration.line("service", overrides=""))
    evaluated = configuration.parse(
        configuration.line("eval", cases=25, decided=20, grouped=5, overrides="p5-cap=x")
    )

    # The service has no cases, so the four run keys are the eval's alone; everything the hash covers
    # is in both, parsed by the same function.
    assert set(service) <= set(evaluated)
    assert set(evaluated) - set(service) == {"cases", "decided", "grouped"}
    assert "config_sha256" in service and "config_sha256" in evaluated
    assert service["overrides"] == ""
    assert evaluated["overrides"] == "p5-cap=x"
    assert service["config_sha256"] == evaluated["config_sha256"], "same config, same hash"


def test_the_hash_orders_the_same_values_the_same_way():
    """Two runs of one configuration agree, or `config_sha256` compares nothing."""
    assert configuration.resolved().sha256() == configuration.resolved().sha256()
    assert len(configuration.resolved().sha256()) == 64


def test_the_eval_writes_the_block_before_it_asks_the_provider(monkeypatch):
    """`make eval | head -1` is the configuration: a run that dies early still says how it was set up."""
    # The interpreter running the suite, not `uv`: the harness is importable from it in both the
    # checkout and CI, and a stripped PATH is how this test first failed in CI with "No such file or
    # directory: 'uv'".
    done = subprocess.run(
        [
            sys.executable,
            "scripts/eval.py",
            "--cases",
            "/dev/null",
            "--out",
            "/dev/null",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PROVIDER_MAX_TOKENS": "640"},
    )
    first = done.stdout.splitlines()[0] if done.stdout else ""

    assert first.startswith("eval config model="), (first, done.stderr[-400:])
    parsed = configuration.parse(first)
    for field in (
        "model",
        "max_tokens",
        "temperature",
        "timeout_seconds",
        "max_steps",
        "budget_seconds",
        "rate_per_minute",
        "prompt_sha256",
        "corpus_sha256",
        "config_sha256",
        "cases",
        "decided",
        "grouped",
        "overrides",
    ):
        assert field in parsed, f"{field} is not on the first line"
