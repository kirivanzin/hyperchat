"""The model call: what goes on the wire, and the three answers.

CI has no provider key and no network to one, so the model is injected. Two kinds of test do the
work: a real `ChatDeepSeek` pointed at an `httpx.MockTransport`, which is the only way to assert the
*wire* field rather than our intent, and a fake that fails, which is the only way to reach 502.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage
from langchain_deepseek import ChatDeepSeek

from hyperchat import agent, chat
from hyperchat.agent import (
    BudgetExceeded,
    NotConfigured,
    ProviderFailed,
    StepLimitExceeded,
    graph,
)
from hyperchat.auth import Caller
from hyperchat.config import Provider, load_settings, provider_settings
from hyperchat.main import api, require_caller

settings = load_settings()

client = TestClient(api)

REPLY = {
    "id": "chatcmpl-test",
    "object": "chat.completion",
    "created": 0,
    "model": "deepseek-chat",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}


@pytest.fixture(autouse=True)
def fresh_limiter(monkeypatch):
    """One window per test: a limiter that survives between tests is a test that passes in isolation.

    The app builds it lazily and keeps it, which is right for a process and wrong for a suite that
    posts more than `RATE_LIMIT_PER_MINUTE` times across its whole life.
    """
    from hyperchat import main

    monkeypatch.setattr(main, "_limiter", main.RateLimiter(per_minute=10_000))


@pytest.fixture
def caller():
    """The trust boundary is tested in `test_auth`; here it is stood in for so this file is about chat."""
    api.dependency_overrides[require_caller] = lambda: Caller(sub="sub-123", email=None)
    yield "sub-123"
    api.dependency_overrides.clear()


class Binds:
    """The one method `call_model` calls on the model before `ainvoke`.

    A fake that cannot be bound is a fake of a different object: the node binds the corpus tool on
    every call, so `bind_tools` returning `self` is what makes the rest of these fakes a model.
    """

    def bind_tools(self, _tools):
        return self


def recorded_model(seen: list[dict], **overrides) -> ChatDeepSeek:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=REPLY)

    transport = httpx.MockTransport(handler)
    settings = {"model": "deepseek-chat", "api_key": "test-key"}
    settings.update(overrides)
    return ChatDeepSeek(**settings, http_async_client=httpx.AsyncClient(transport=transport))


def wired_model(monkeypatch, seen: list[dict]) -> None:
    """Let `get_model()` build the real configured client, over a transport that records it.

    The point is to assert what `get_model` puts on the wire — the temperature it passes and the
    contract it is given — rather than what a test constructs by hand.
    """
    monkeypatch.setattr(agent, "_model", None)
    monkeypatch.setenv("PROVIDER_API_KEY", "test-key")
    monkeypatch.setattr(agent, "ChatDeepSeek", lambda **kwargs: recorded_model(seen, **kwargs))


def test_chat_is_behind_the_boundary_before_it_is_behind_the_model():
    # No dependency override here, which is the point: the router carries the check, so /api/chat
    # answers 401 without a token no matter what the model is configured to do.
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 401


# --- the graph is a graph ------------------------------------------------------------------------


def test_the_model_arrives_as_a_graph():
    """The acceptance command, as a test: `langgraph dev` and `make chat` both need this to hold."""
    assert sorted(graph.get_graph().nodes) == ["__end__", "__start__", "model", "tools"]


# --- what is actually sent ------------------------------------------------------------------------


def test_the_cap_comes_from_the_environment_and_lands_on_the_wire(caller, monkeypatch):
    # The whole path, because the acceptance is `PROVIDER_MAX_TOKENS=5 make chat`: env -> model
    # construction -> the JSON body. Asserting the constant instead would prove none of it.
    monkeypatch.setenv("PROVIDER_MAX_TOKENS", "5")
    assert provider_settings().max_tokens == 5

    seen: list[dict] = []
    monkeypatch.setattr(agent, "_model", recorded_model(seen, max_tokens=5))

    response = client.post("/api/chat", json={"message": "Count from one to fifty."})

    assert response.status_code == 200
    assert seen, "the provider was never called"
    # By name. The OpenAI client would rename this to `max_completion_tokens`, which DeepSeek ignores,
    # and a cap that is silently not applied looks exactly like one that is.
    assert seen[0]["max_tokens"] == 5
    assert "max_completion_tokens" not in seen[0]


def test_the_caller_subject_is_sent_as_the_end_user(caller, monkeypatch):
    seen: list[dict] = []
    monkeypatch.setattr(agent, "_model", recorded_model(seen, max_tokens=8))

    client.post("/api/chat", json={"message": "hi"})

    assert seen[0]["user"] == caller


def test_the_prompt_reaches_the_model_unchanged(caller, monkeypatch):
    seen: list[dict] = []
    monkeypatch.setattr(agent, "_model", recorded_model(seen, max_tokens=8))

    client.post("/api/chat", json={"message": "say ok"})

    assert seen[0]["messages"][-1] == {"content": "say ok", "role": "user"}


def test_a_tool_call_cut_in_half_is_named_rather_than_sent_back(monkeypatch):
    """The cap can end a turn mid-call; the next request would be one the provider refuses with a 400.

    Measured before it was fixed: `PROVIDER_MAX_TOKENS=128` made `p4-json-array` fail 3 times out of 3
    with `400 An assistant message with 'tool_calls' must be followed by ...` — a sentence about the
    payload for a cause in our configuration.
    """

    class Truncating(Binds):
        async def ainvoke(self, *_args: object, **_kwargs: object) -> AIMessage:
            return AIMessage(
                content="",
                tool_calls=[],
                invalid_tool_calls=[{"name": "read_document", "args": '{"name": "REA', "id": "x"}],
                response_metadata={"finish_reason": "length"},
            )

    monkeypatch.setattr(agent, "_model", Truncating())

    async def node():
        return await agent.call_model({"messages": [HumanMessage("hi")]}, {})

    with pytest.raises(agent.TruncatedToolCall, match="cut mid tool call"):
        asyncio.run(node())


# --- the contract, on the wire -------------------------------------------------------------------


def test_the_contract_is_the_first_message_on_the_wire(caller, monkeypatch):
    """The system prompt is prepended per call, so a caller cannot omit it."""
    seen: list[dict] = []
    wired_model(monkeypatch, seen)

    client.post("/api/chat", json={"message": "say ok"})

    assert seen[0]["messages"][0] == {"content": agent.SYSTEM_PROMPT, "role": "system"}
    assert [message["role"] for message in seen[0]["messages"]] == ["system", "user"]


def test_the_contract_is_not_written_into_state(caller, monkeypatch):
    """A system message in `MessagesState` is one a caller could append to, which is why it is not."""
    # The messages handed to the model, not the graph's state: the test is about what goes on the
    # wire, and calling that "state" is how a reader ends up checking the wrong half.
    wire: list = []

    class Recorder(Binds):
        async def ainvoke(self, messages, **_kwargs):
            wire[:] = list(messages)
            return AIMessage(content="ok")

    async def node():
        return await agent.call_model({"messages": [HumanMessage("hi")]}, {})

    monkeypatch.setattr(agent, "_model", Recorder())
    result = asyncio.run(node())

    assert wire[0].type == "system"
    assert [message.type for message in result["messages"]] == ["ai"]


def test_the_temperature_on_the_wire_is_pinned_not_inherited(caller, monkeypatch):
    """`temperature` is the provider's default unless this repository says otherwise; it says 0.0."""
    seen: list[dict] = []
    monkeypatch.delenv("PROVIDER_TEMPERATURE", raising=False)
    wired_model(monkeypatch, seen)

    client.post("/api/chat", json={"message": "say ok"})

    assert seen[0]["temperature"] == 0.0


def test_the_temperature_override_reaches_the_wire(caller, monkeypatch):
    seen: list[dict] = []
    monkeypatch.setenv("PROVIDER_TEMPERATURE", "0.7")
    wired_model(monkeypatch, seen)

    client.post("/api/chat", json={"message": "say ok"})

    assert seen[0]["temperature"] == 0.7


def test_a_temperature_outside_the_providers_range_is_refused(monkeypatch):
    monkeypatch.setenv("PROVIDER_TEMPERATURE", "3")
    with pytest.raises(RuntimeError, match="outside 0.0-2.0"):
        provider_settings()


def test_the_prompt_hash_is_logged_once_at_startup(monkeypatch, caplog):
    """One line at boot, carrying the same hash the eval records: the subject of the measurement."""
    from hyperchat.main import api as app
    from hyperchat.main import lifespan

    monkeypatch.delenv("PROVIDER_API_KEY", raising=False)
    monkeypatch.setattr(agent, "_model", None)
    caplog.set_level(logging.INFO, logger="hyperchat")

    async def start() -> None:
        async with lifespan(app):
            pass

    asyncio.run(start())

    lines = [r.getMessage() for r in caplog.records if "prompt_sha256" in r.getMessage()]
    assert len(lines) == 1, lines
    assert agent.prompt_sha256() in lines[0]


def test_the_package_readme_states_the_prompt_the_code_sends():
    """The contract is in two places on purpose; a copy that drifts is the defect this repository
    treats as documentation. Changing the prompt means changing both, in one commit."""
    readme = (Path(__file__).resolve().parents[1] / "src/hyperchat/README.md").read_text()
    assert agent.SYSTEM_PROMPT.strip() in readme


def test_the_prompt_hash_is_computed_from_the_prompt():
    assert agent.prompt_sha256() == hashlib.sha256(agent.SYSTEM_PROMPT.encode()).hexdigest()
    assert len(agent.prompt_sha256()) == 64


# --- the bounds -----------------------------------------------------------------------------------


def test_the_rate_limit_is_429_with_retry_after(caller, monkeypatch):
    """RATE_LIMIT_PER_MINUTE is per subject, and the refusal says how long to wait."""
    from hyperchat import main

    monkeypatch.setattr(main, "_limiter", main.RateLimiter(per_minute=2))
    monkeypatch.setattr(agent, "_model", recorded_model([], max_tokens=8))

    assert client.post("/api/chat", json={"message": "hi"}).status_code == 200
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 200
    refused = client.post("/api/chat", json={"message": "hi"})

    assert refused.status_code == 429
    assert 1 <= int(refused.headers["Retry-After"]) <= 60


def test_the_run_bounds_are_504_and_say_which(caller, monkeypatch):
    """Two bounds, two sentences: "did it finish?" is the one thing a caller cannot work around."""
    from hyperchat import main

    async def out_of_steps(*_args, **_kwargs):
        raise StepLimitExceeded("the run exceeded its 6 steps")

    async def out_of_time(*_args, **_kwargs):
        raise BudgetExceeded("the run exceeded its 60s budget")

    monkeypatch.setattr(main, "run_graph", out_of_steps)
    steps = client.post("/api/chat", json={"message": "hi"})
    assert steps.status_code == 504
    assert "step limit" in steps.json()["detail"]

    monkeypatch.setattr(main, "run_graph", out_of_time)
    budget = client.post("/api/chat", json={"message": "hi"})
    assert budget.status_code == 504
    assert "time budget" in budget.json()["detail"]


def test_the_provider_timeout_is_sent_to_the_client(monkeypatch):
    """The bound that stops a stalled call living to the platform's 300s cap."""
    seen: list[dict] = []
    recorded = {}

    def build(**kwargs):
        recorded.update(kwargs)
        return recorded_model(seen, **kwargs)

    monkeypatch.setattr(agent, "_model", None)
    monkeypatch.setenv("PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("PROVIDER_TIMEOUT_SECONDS", "7.5")
    monkeypatch.setattr(agent, "ChatDeepSeek", build)

    agent.get_model()

    assert recorded["timeout"] == 7.5


# --- the three answers ----------------------------------------------------------------------------


def test_a_reply_is_200(caller, monkeypatch):
    monkeypatch.setattr(agent, "_model", recorded_model([], max_tokens=8))

    response = client.post("/api/chat", json={"message": "hi"})

    assert response.status_code == 200
    assert response.json() == {"reply": "ok"}


def test_no_provider_key_is_503_and_not_500(caller, monkeypatch):
    monkeypatch.setattr(agent, "_model", None)
    monkeypatch.setattr(
        agent,
        "provider_settings",
        lambda: Provider(model="deepseek-chat", max_tokens=8, api_key=None),
    )

    response = client.post("/api/chat", json={"message": "hi"})

    assert response.status_code == 503
    assert response.json()["detail"] == "the model is not configured"


class Failing(Binds):
    def __init__(self) -> None:
        self.calls = 0

    async def ainvoke(self, *_args: object, **_kwargs: object) -> object:
        self.calls += 1
        raise RuntimeError("upstream said something about the prompt: SECRET-PROMPT")


def test_a_provider_failure_is_502_without_the_reason(caller, monkeypatch):
    failing = Failing()
    monkeypatch.setattr(agent, "_model", failing)

    response = client.post("/api/chat", json={"message": "SECRET-PROMPT"})

    assert failing.calls == 1
    assert response.status_code == 502
    assert response.json()["detail"] == "the model provider failed"
    # The reason is logged, not returned, and the prompt is in neither.
    assert "SECRET-PROMPT" not in response.text
    assert "upstream" not in response.text


def test_an_empty_message_is_rejected_before_the_model(caller, monkeypatch):
    seen: list[dict] = []
    monkeypatch.setattr(agent, "_model", recorded_model(seen, max_tokens=8))

    response = client.post("/api/chat", json={"message": ""})

    assert response.status_code == 422
    assert seen == []


def test_an_over_long_message_is_rejected_before_the_model(caller, monkeypatch):
    # The output cap does not bound the input: a provider charges for the prompt too. 4000 is the
    # sibling's cap, and 4001 is the first value that must not reach the provider.
    seen: list[dict] = []
    monkeypatch.setattr(agent, "_model", recorded_model(seen, max_tokens=8))

    response = client.post("/api/chat", json={"message": "x" * 4001})

    assert response.status_code == 422
    assert seen == []


def test_an_empty_reply_is_an_EmptyReply_that_names_the_finish_reason(monkeypatch):
    class Empty(Binds):
        async def ainvoke(self, *_args: object, **_kwargs: object) -> AIMessage:
            return AIMessage(content="", response_metadata={"finish_reason": "length"})

    monkeypatch.setattr(agent, "_model", Empty())

    with pytest.raises(agent.EmptyReply) as caught:
        asyncio.run(agent.call_model({"messages": [HumanMessage("hi")]}, {}))

    assert isinstance(caught.value, ProviderFailed)
    assert str(caught.value) == "provider returned no content (finish_reason=length)"


def test_an_empty_reply_is_502_like_any_provider_failure(caller, monkeypatch, caplog):
    class Empty(Binds):
        async def ainvoke(self, *_args: object, **_kwargs: object) -> AIMessage:
            return AIMessage(content="", response_metadata={"finish_reason": "content_filter"})

    monkeypatch.setattr(agent, "_model", Empty())

    with caplog.at_level(logging.ERROR, logger="hyperchat"):
        response = client.post("/api/chat", json={"message": "hi"})

    assert response.status_code == 502
    assert response.json()["detail"] == "the model provider failed"
    assert "provider returned no content (finish_reason=content_filter)" in caplog.text, (
        "the log must name the finish_reason; an EmptyReply without it is a rename"
    )


def test_a_reply_with_text_blocks_is_not_empty(monkeypatch):
    # `content` can be a list of blocks, so a truthiness check on the whole value would call this
    # reply empty and turn a real answer into a 502.
    blocks = [{"type": "text", "text": "ok"}]

    class Reply(Binds):
        async def ainvoke(self, *_args: object, **_kwargs: object) -> AIMessage:
            return AIMessage(content=blocks, response_metadata={"finish_reason": "stop"})

    monkeypatch.setattr(agent, "_model", Reply())

    result = asyncio.run(agent.call_model({"messages": [HumanMessage("hi")]}, {}))

    assert result["messages"][0].content == blocks


# --- the client lifetime --------------------------------------------------------------------------


def test_closing_the_model_closes_both_pools(monkeypatch):
    class Pool:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class AsyncPool(Pool):
        async def close(self) -> None:  # type: ignore[override]
            self.closed = True

    class Model:
        root_client = Pool()
        root_async_client = AsyncPool()

    model = Model()
    monkeypatch.setattr(agent, "_model", model)

    asyncio.run(agent.close_model())

    assert model.root_client.closed
    assert model.root_async_client.closed
    assert agent._model is None


# --- the CLI, which runs the same graph -----------------------------------------------------------


def test_the_cli_exits_3_when_the_model_is_off(monkeypatch, capsys):
    async def refuse(*_args: object, **_kwargs: object) -> object:
        raise NotConfigured("PROVIDER_API_KEY is unset")

    async def noop() -> None:
        return None

    monkeypatch.setattr(chat, "run_graph", refuse)
    monkeypatch.setattr(chat, "close_model", noop)

    assert chat.main(["chat", "hi"]) == 3
    assert "not broken" in capsys.readouterr().err


def test_the_cli_exits_4_when_the_provider_fails(monkeypatch, capsys):
    async def fail(*_args: object, **_kwargs: object) -> object:
        raise ProviderFailed("TimeoutError: slow")

    async def noop() -> None:
        return None

    monkeypatch.setattr(chat, "run_graph", fail)
    monkeypatch.setattr(chat, "close_model", noop)

    assert chat.main(["chat", "hi"]) == 4
    assert "TimeoutError" in capsys.readouterr().err


def test_the_cli_exits_4_and_names_the_bound_that_fired(monkeypatch, capsys):
    """A tool that says "failed" and no more is a tool nobody reaches for."""

    async def out_of_steps(*_args: object, **_kwargs: object) -> object:
        raise StepLimitExceeded("the run exceeded its 6 steps")

    async def noop() -> None:
        return None

    monkeypatch.setattr(chat, "run_graph", out_of_steps)
    monkeypatch.setattr(chat, "close_model", noop)

    assert chat.main(["chat", "hi"]) == 4
    assert "step limit" in capsys.readouterr().err


def test_the_cli_prints_the_reply(monkeypatch, capsys):
    async def reply(*_args: object, **_kwargs: object) -> dict:
        return {"messages": [type("M", (), {"content": "ok"})()]}

    async def noop() -> None:
        return None

    monkeypatch.setattr(chat, "run_graph", reply)
    monkeypatch.setattr(chat, "close_model", noop)

    assert chat.main(["chat", "hi"]) == 0
    assert capsys.readouterr().out.strip() == "ok"


def test_the_cli_without_a_message_is_usage():
    assert chat.main(["chat"]) == 2
