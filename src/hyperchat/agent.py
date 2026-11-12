"""One model call, as a graph.

The endpoint and the tool that debugs it are the same object: `graph` here is what `/api/chat` awaits
and what `make chat` runs, so a debugging tool cannot drift from production. It is a graph with one
node today because everything after this ticket — streaming, the run bounds, tools — is work *on* the
graph rather than work to introduce one.

Four decisions that are cheap now and a refactor later:

- **The node is `async` and awaits the model.** `langgraph dev` refuses synchronous blocking I/O in a
  node; in the service a synchronous node occupies a threadpool slot that `/livez` also needs, for
  every step of a run rather than the length of one call. `--allow-blocking` silences the first and is
  the wrong fix.
- **The client's lifetime is the process's, not the request's.** Built once, closed on shutdown.
- **The caller's subject arrives on the run config**, in `metadata["sub"]`, which is also the field
  Studio shows when the graph runs by hand with no subject. It rides to the provider as the end-user
  id, which is what makes a cost attributable to a person.
- **The output cap is explicit and named `max_tokens`.** Not left to the model, and not the
  `max_completion_tokens` spelling an OpenAI client would substitute: DeepSeek ignores that name and
  answers with a full reply under a cap that was never applied, which is indistinguishable from a
  working one.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig
from langchain_deepseek import ChatDeepSeek
from langgraph.graph import END, START, MessagesState, StateGraph

from hyperchat.config import provider_settings


class NotConfigured(Exception):
    """No provider key. The feature is off, which is not the same as broken."""


class ProviderFailed(Exception):
    """The provider failed. The reason is for the log, never for the caller."""


_model: ChatDeepSeek | None = None


def get_model() -> ChatDeepSeek:
    """The one client, built on first use.

    Lazy so that importing the graph — which `langgraph dev`, `make chat` and the acceptance command
    all do — does not require a key. The app's lifespan calls this at startup so the connection is
    established then rather than on the first request.
    """
    global _model
    if _model is None:
        provider = provider_settings()
        if provider.api_key is None:
            raise NotConfigured("PROVIDER_API_KEY is unset")
        _model = ChatDeepSeek(
            model=provider.model,
            api_key=provider.api_key,
            # On the wire, by name. See the module docstring.
            max_tokens=provider.max_tokens,
        )
    return _model


async def close_model() -> None:
    """Close both pools the model holds. Called on shutdown, and by the CLI."""
    global _model
    if _model is not None:
        await _model.root_async_client.close()
        # The sync pool exists whether or not a sync path used it, so closing only the async one
        # leaves it open for the life of the process.
        _model.root_client.close()
        _model = None


async def call_model(state: MessagesState, config: RunnableConfig) -> dict[str, list[BaseMessage]]:
    """The node. One provider call, with the caller's subject attached and the cap in place."""
    model = get_model()
    sub = (config.get("metadata") or {}).get("sub")

    # `user` is the provider's end-user id. Absent for a hand-run graph, which is why it is conditional
    # rather than `None`: an empty string would be a subject that does not exist.
    kwargs: dict[str, Any] = {"user": sub} if isinstance(sub, str) and sub else {}

    try:
        reply = await model.ainvoke(state["messages"], **kwargs)
    except NotConfigured:
        raise
    except Exception as exc:
        # The provider boundary. Converted to a distinct error so the endpoint can answer 502 without
        # returning, or logging, anything about the prompt.
        raise ProviderFailed(f"{type(exc).__name__}: {exc}") from exc

    return {"messages": [reply]}


_builder = StateGraph(MessagesState)
_builder.add_node("model", call_model)
_builder.add_edge(START, "model")
_builder.add_edge("model", END)
graph = _builder.compile()
