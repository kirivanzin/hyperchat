"""The model call: what goes on the wire, and the three answers.

CI has no provider key and no network to one, so the model is injected. Two kinds of test do the
work: a real `ChatDeepSeek` pointed at an `httpx.MockTransport`, which is the only way to assert the
*wire* field rather than our intent, and a fake that fails, which is the only way to reach 502.
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_deepseek import ChatDeepSeek

from hyperchat import agent, chat
from hyperchat.agent import NotConfigured, ProviderFailed, graph
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


@pytest.fixture
def caller():
    """The trust boundary is tested in `test_auth`; here it is stood in for so this file is about chat."""
    api.dependency_overrides[require_caller] = lambda: Caller(sub="sub-123", email=None)
    yield "sub-123"
    api.dependency_overrides.clear()


def recorded_model(seen: list[dict], **overrides) -> ChatDeepSeek:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=REPLY)

    transport = httpx.MockTransport(handler)
    return ChatDeepSeek(
        model="deepseek-chat",
        api_key="test-key",
        http_async_client=httpx.AsyncClient(transport=transport),
        **overrides,
    )


def test_chat_is_behind_the_boundary_before_it_is_behind_the_model():
    # No dependency override here, which is the point: the router carries the check, so /api/chat
    # answers 401 without a token no matter what the model is configured to do.
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 401


# --- the graph is a graph ------------------------------------------------------------------------


def test_the_model_arrives_as_a_graph():
    """The acceptance command, as a test: `langgraph dev` and `make chat` both need this to hold."""
    assert sorted(graph.get_graph().nodes) == ["__end__", "__start__", "model"]


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

    assert seen[0]["messages"] == [{"content": "say ok", "role": "user"}]


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


class Failing:
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


# --- the CLI, which runs the same graph -----------------------------------------------------------


def test_the_cli_exits_3_when_the_model_is_off(monkeypatch, capsys):
    async def refuse(*_args: object, **_kwargs: object) -> object:
        raise NotConfigured("PROVIDER_API_KEY is unset")

    async def noop() -> None:
        return None

    monkeypatch.setattr(chat, "graph", type("G", (), {"ainvoke": staticmethod(refuse)})())
    monkeypatch.setattr(chat, "close_model", noop)

    assert chat.main(["chat", "hi"]) == 3
    assert "not broken" in capsys.readouterr().err


def test_the_cli_exits_4_when_the_provider_fails(monkeypatch, capsys):
    async def fail(*_args: object, **_kwargs: object) -> object:
        raise ProviderFailed("TimeoutError: slow")

    async def noop() -> None:
        return None

    monkeypatch.setattr(chat, "graph", type("G", (), {"ainvoke": staticmethod(fail)})())
    monkeypatch.setattr(chat, "close_model", noop)

    assert chat.main(["chat", "hi"]) == 4
    assert "TimeoutError" in capsys.readouterr().err


def test_the_cli_prints_the_reply(monkeypatch, capsys):
    async def reply(*_args: object, **_kwargs: object) -> dict:
        return {"messages": [type("M", (), {"content": "ok"})()]}

    async def noop() -> None:
        return None

    monkeypatch.setattr(chat, "graph", type("G", (), {"ainvoke": staticmethod(reply)})())
    monkeypatch.setattr(chat, "close_model", noop)

    assert chat.main(["chat", "hi"]) == 0
    assert capsys.readouterr().out.strip() == "ok"


def test_the_cli_without_a_message_is_usage():
    assert chat.main(["chat"]) == 2
