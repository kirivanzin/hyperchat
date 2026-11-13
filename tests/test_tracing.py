"""Tracing: what the line says, and what a run carries so a trace can be attributed.

Every failure this file is about is silent in production — a service answering 200 with an empty
project view — so the tests are about the sentence and not about the network. Nothing here constructs
a langsmith client, which is why the suite passes with no key, no network, and no `LANGSMITH_*` in the
environment at all.

langsmith memoises the environment (`get_env_var` is `lru_cache`d), so without clearing that cache
every case after the first reads the first case's environment and the tests pass by agreeing with each
other. `tracing_env` clears it before and after, and every case goes through it.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from hyperchat import chat, configuration, main, tracing
from hyperchat.auth import Caller
from hyperchat.main import api, require_caller

REPO = Path(__file__).resolve().parents[1]
TERRAFORM = REPO / "terraform" / "app" / "main.tf"


@pytest.fixture
def tracing_env(monkeypatch):
    """A clean tracing environment, and a busted langsmith cache."""

    def clear() -> None:
        import langsmith.utils

        langsmith.utils.get_env_var.cache_clear()
        # Cached too, and the trap is the same one: a project read from the previous case's
        # environment is a test that is about the fixture rather than about the code.
        langsmith.utils.get_tracer_project.cache_clear()

    clear()

    def configure(**values: str) -> None:
        for name in tracing.VARIABLES:
            monkeypatch.delenv(name, raising=False)
        for name, value in values.items():
            monkeypatch.setenv(name, value)
        clear()

    yield configure
    clear()


# --- the shapes on the line -------------------------------------------------


def test_tracing_is_reported_off_when_it_is_not_enabled(tracing_env):
    tracing_env(LANGSMITH_API_KEY="k", LANGSMITH_PROJECT="p")

    assert tracing.fields() == {"tracing": "off", "tracing_why": "LANGSMITH_TRACING-is-not-true"}


def test_tracing_names_the_project_runs_go_to(tracing_env):
    tracing_env(
        LANGSMITH_TRACING="true", LANGSMITH_API_KEY="k", LANGSMITH_PROJECT="hyperchat-local"
    )

    assert tracing.fields() == {
        "tracing": "on",
        "tracing_project": "hyperchat-local",
        "tracing_inputs": "visible",
        "tracing_outputs": "visible",
    }


def test_tracing_names_the_agent_address_and_lowercases_the_environment(tracing_env):
    tracing_env(
        LANGSMITH_TRACING="true",
        LANGSMITH_API_KEY="k",
        LANGSMITH_AGENT_ID="hyperchat",
        LANGSMITH_AGENT_ENVIRONMENT="LOCAL",
    )

    assert tracing.fields()["tracing_agent"] == "hyperchat/local"


def test_tracing_defaults_to_the_default_project(tracing_env):
    """No project named is not an absent destination: it is langsmith's `default`, and it is where
    the runs will be looking for."""
    tracing_env(LANGSMITH_TRACING="true", LANGSMITH_API_KEY="k")

    assert tracing.fields()["tracing_project"] == "default"


def test_a_missing_key_is_reported_although_tracing_is_on(tracing_env):
    """The shape that reads as healthy from every endpoint and leaves an empty project view."""
    tracing_env(LANGSMITH_TRACING="true", LANGSMITH_PROJECT="hyperchat-local")

    fields = tracing.fields()

    assert fields["tracing"] == "on"
    assert fields["tracing_api_key"] == "unset"
    assert fields["tracing_project"] == "hyperchat-local"


def test_two_destinations_are_reported_as_untraced(tracing_env):
    """The failure that is invisible without the line: both set, service up, nothing traced."""
    tracing_env(
        LANGSMITH_TRACING="true",
        LANGSMITH_API_KEY="k",
        LANGSMITH_PROJECT="hyperchat-local",
        LANGSMITH_AGENT_ID="hyperchat",
        LANGSMITH_AGENT_ENVIRONMENT="local",
    )

    assert tracing.fields() == {
        "tracing": "off",
        "tracing_why": "a-project-and-an-agent-address-are-both-set",
    }


def test_half_an_agent_address_is_reported_as_untraced(tracing_env):
    tracing_env(LANGSMITH_TRACING="true", LANGSMITH_API_KEY="k", LANGSMITH_AGENT_ID="hyperchat")

    assert tracing.fields() == {
        "tracing": "off",
        "tracing_why": "the-agent-address-is-incomplete-or-invalid",
    }


def test_an_environment_langsmith_does_not_accept_is_reported_as_untraced(tracing_env):
    tracing_env(
        LANGSMITH_TRACING="true",
        LANGSMITH_API_KEY="k",
        LANGSMITH_AGENT_ID="hyperchat",
        LANGSMITH_AGENT_ENVIRONMENT="prod",
    )

    assert tracing.fields()["tracing"] == "off"
    assert tracing.fields()["tracing_why"] == "the-agent-address-is-incomplete-or-invalid"


def test_the_hide_flags_are_part_of_what_the_line_reports(tracing_env):
    """A trace with the text kept out still has the run tree, the tool call and the tokens; the flags
    decide whether it has the caller's message, and that is a configuration fact."""
    tracing_env(
        LANGSMITH_TRACING="true",
        LANGSMITH_API_KEY="k",
        LANGSMITH_HIDE_INPUTS="true",
        LANGSMITH_HIDE_OUTPUTS="true",
    )

    fields = tracing.fields()

    assert fields["tracing_inputs"] == "hidden"
    assert fields["tracing_outputs"] == "hidden"


def test_the_client_compares_the_flag_with_the_string_true(tracing_env):
    """`LANGSMITH_HIDE_INPUTS=1` is not hidden to the client, so it is not hidden on the line either:
    a report that was truthier than the client would say traces are safe when they are not."""
    tracing_env(LANGSMITH_TRACING="true", LANGSMITH_API_KEY="k", LANGSMITH_HIDE_INPUTS="1")

    assert tracing.fields()["tracing_inputs"] == "visible"


# --- the deployment, and what the line would say about it -------------------


def test_the_deployed_revision_hides_the_caller_text(tracing_env):
    """The deployed configuration, as the Terraform declares it, resolves to hidden inputs and
    outputs. This cannot see the applied revision; it can see that the flags are still there and that
    the values the client reads are the ones that make the line say so."""
    declared = dict(
        re.findall(
            r'^\s*(LANGSMITH_[A-Z_]+)\s*=\s*"([^"]*)"\s*$', TERRAFORM.read_text(), re.MULTILINE
        )
    )

    assert declared == {
        "LANGSMITH_TRACING": "true",
        "LANGSMITH_HIDE_INPUTS": "true",
        "LANGSMITH_HIDE_OUTPUTS": "true",
    }

    tracing_env(LANGSMITH_API_KEY="k", **declared)
    fields = tracing.fields()

    assert fields["tracing"] == "on"
    assert fields["tracing_inputs"] == "hidden"
    assert fields["tracing_outputs"] == "hidden"


def test_the_deployed_key_is_a_secret_reference_not_a_value():
    """The same mechanism as the provider key: an env var is readable by anyone with run.viewer."""
    text = TERRAFORM.read_text()

    key = re.search(r'name = "LANGSMITH_API_KEY"([\s\S]*?)}\n        }', text)
    assert key, "the revision declares no LANGSMITH_API_KEY"
    assert "value_source" in key.group(1)
    assert "secret_key_ref" in key.group(1)


# --- on the configuration line, and not somewhere else ----------------------


def test_the_tracing_report_rides_on_the_configuration_line(tracing_env):
    """One line, not two: the fact is on the line `grep config` already finds."""
    tracing_env()

    line = configuration.line("service", overrides="", **tracing.fields())

    assert line.count("\n") == 0
    assert "tracing=off" in line
    assert configuration.parse(line)["tracing"] == "off"


@pytest.mark.parametrize(
    "configured",
    [
        {},
        {"LANGSMITH_TRACING": "true", "LANGSMITH_API_KEY": "k", "LANGSMITH_PROJECT": "p"},
        {
            "LANGSMITH_TRACING": "true",
            "LANGSMITH_API_KEY": "k",
            "LANGSMITH_AGENT_ID": "a",
            "LANGSMITH_AGENT_ENVIRONMENT": "local",
        },
        {"LANGSMITH_TRACING": "true", "LANGSMITH_PROJECT": "p"},
        {
            "LANGSMITH_TRACING": "true",
            "LANGSMITH_API_KEY": "k",
            "LANGSMITH_PROJECT": "p",
            "LANGSMITH_AGENT_ID": "a",
            "LANGSMITH_AGENT_ENVIRONMENT": "local",
        },
        {"LANGSMITH_TRACING": "true", "LANGSMITH_API_KEY": "k", "LANGSMITH_AGENT_ID": "a"},
    ],
)
def test_every_tracing_field_is_one_parseable_pair(tracing_env, configured):
    """A value with a space in it would split into tokens that look like configuration, so the fields
    are pairs. This is the test that fails the day somebody makes the report a sentence."""
    tracing_env(**configured)
    fields = tracing.fields()

    for name, value in fields.items():
        assert name.startswith("tracing"), name
        assert " " not in value and "=" not in value, (name, value)

    assert (
        configuration.parse(configuration.line("service", overrides="", **fields))["tracing"]
        == (fields["tracing"])
    )


def test_the_line_never_contains_the_key(tracing_env):
    """The line is greppable and pasted into issues; the key is the one thing that must not ride it."""
    key = "lsv2-test-3f9a2b7c1d4e5f60718293a4b5c6d7e8"
    tracing_env(LANGSMITH_TRACING="true", LANGSMITH_API_KEY=key, LANGSMITH_PROJECT="p")

    line = configuration.line("service", overrides="", **tracing.fields())

    assert key not in line
    assert not any(key[offset : offset + 8] in line for offset in range(len(key) - 8))
    assert "LANGSMITH_API_KEY=" not in line


# --- attribution: a trace of an unknown configuration is not evidence -------


def test_run_metadata_carries_the_configuration_and_the_caller():
    block = configuration.resolved()

    assert configuration.run_metadata("sub-1", block) == {
        "sub": "sub-1",
        "config_sha256": block.sha256(),
        "prompt_sha256": block.prompt_sha256,
        "corpus_sha256": block.corpus_sha256,
    }


def test_run_metadata_defaults_to_the_resolved_configuration():
    """For a caller that never resolved one — a test client that starts no lifespan."""
    assert configuration.run_metadata("cli")["config_sha256"] == configuration.resolved().sha256()


@pytest.fixture
def captured(monkeypatch):
    """A graph that records the config it was called with instead of calling a provider."""

    def install(module) -> dict:
        seen: dict = {}

        async def fake_run(_messages, config):
            seen.update(config)
            return {"messages": [AIMessage(content="ok")]}

        monkeypatch.setattr(module, "run_graph", fake_run)
        return seen

    return install


def test_the_service_puts_the_hashes_on_the_run_config(captured, monkeypatch):
    seen = captured(main)
    # One window per test, as test_chat does: a limiter shared across the suite is a test that passes
    # in isolation and 429s in a full run.
    monkeypatch.setattr(main, "_limiter", main.RateLimiter(per_minute=10_000))
    api.dependency_overrides[require_caller] = lambda: Caller(sub="sub-123", email=None)
    try:
        response = TestClient(api).post("/api/chat", json={"message": "hello"})
    finally:
        api.dependency_overrides.clear()

    assert response.status_code == 200
    metadata = seen["metadata"]
    assert metadata["sub"] == "sub-123"
    assert metadata["config_sha256"] == configuration.resolved().sha256()
    assert metadata["prompt_sha256"] == configuration.resolved().prompt_sha256
    assert metadata["corpus_sha256"] == configuration.resolved().corpus_sha256


def test_the_cli_puts_the_hashes_on_the_run_config(captured):
    seen = captured(chat)

    assert asyncio.run(chat.run("hello")) == "ok"
    assert seen["metadata"]["sub"] == "cli"
    assert seen["metadata"]["config_sha256"] == configuration.resolved().sha256()


def test_every_eval_case_puts_the_run_s_own_block_on_the_config(captured):
    """The block is the one the run printed and wrote into `evals/results.jsonl`, not a fresh read:
    a case that changes `PROVIDER_MAX_TOKENS` would otherwise trace under a hash nobody recorded."""
    from test_eval import harness

    seen = captured(harness)
    block = harness.configuration.resolved()
    case = harness.Case(
        id="x",
        input="hello",
        expects={"min_chars": 1},
        why="a case, so that the run has something to trace",
        max_tokens=8,
    )

    asyncio.run(harness.ask(case, block))

    assert seen["metadata"] == {
        "sub": "eval",
        "config_sha256": block.sha256(),
        "prompt_sha256": block.prompt_sha256,
        "corpus_sha256": block.corpus_sha256,
    }
