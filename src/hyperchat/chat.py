"""Run one message through the graph, from a shell.

The same `graph` the endpoint serves, `ainvoke`d directly, so the thing a person debugs with cannot
drift from the thing that runs in the service. No HTTP and no token: it is a local tool, and the
authentication that protects `/api/chat` is a property of the route rather than of the graph.

Exit codes are the answers the endpoint gives that a shell can act on, so the two cannot drift:

    0  a reply
    3  no provider key configured — the model is off, not broken
    4  the run failed: the provider, its timeout, the step ceiling or the time budget. The message on
       stderr says which, because a tool that says "failed" and no more is a tool nobody reaches for.
"""

from __future__ import annotations

import asyncio
import sys

from langchain_core.messages import HumanMessage

from hyperchat.agent import (
    BudgetExceeded,
    NotConfigured,
    ProviderFailed,
    StepLimitExceeded,
    close_model,
)
from hyperchat.agent import run as run_graph


async def run(message: str) -> str:
    try:
        result = await run_graph(
            [HumanMessage(message)],
            # A hand-run graph has no caller. "cli" is a subject for the record rather than a claim
            # about a person, and it keeps the provider's end-user id populated.
            {"metadata": {"sub": "cli"}},
        )
    finally:
        await close_model()

    reply = result["messages"][-1].content
    return reply if isinstance(reply, str) else str(reply)


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not argv[1].strip():
        print("usage: make chat MESSAGE='...'", file=sys.stderr)
        return 2

    try:
        reply = asyncio.run(run(argv[1]))
    except NotConfigured:
        print(
            "no provider key: set PROVIDER_API_KEY in .env. The model is off, not broken.",
            file=sys.stderr,
        )
        return 3
    except BudgetExceeded as exc:
        print(f"the run hit its time budget: {exc}", file=sys.stderr)
        return 4
    except StepLimitExceeded as exc:
        print(f"the run hit its step limit: {exc}", file=sys.stderr)
        return 4
    except ProviderFailed as exc:
        # The CLI is a debugging tool, so the reason is the point of it being here.
        print(f"the provider failed: {exc}", file=sys.stderr)
        return 4

    print(reply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
