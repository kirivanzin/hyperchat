"""The app.

Two values are read at import — the expected audience and the allowlist — and the process refuses to
start without them. That is deliberate: a service that boots with an empty allowlist answers 403 to
everybody while its readiness probe stays green, so the failure has to be a crash rather than a
healthy-looking process nobody can use.

`/` stays open. It returns a constant and spends nothing. Everything under `/api` is behind the trust
boundary, and the dependency that enforces it sits on the router rather than on the routes, so adding
a route there is the only step required to protect it.

`/api/chat` is the only route that spends money, so it is the only one with two ways to be unavailable:
503 when no provider key is configured, 502 when the provider itself fails.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from hyperchat.agent import NotConfigured, ProviderFailed, close_model, get_model, graph
from hyperchat.auth import AuthError, Caller, NotAllowedError, UpstreamError, verify_token
from hyperchat.config import load_settings

# Read here, not in `config`, so that importing the graph does not require an allowlist: `make chat`
# and `langgraph dev` are tools, and a tool that needs the production trust boundary configured is a
# tool nobody runs. The service still refuses to start without it, because this line runs on import.
settings = load_settings()

logger = logging.getLogger("hyperchat")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """The provider client lives as long as the process: built here, closed on shutdown.

    Building it at startup rather than on the first request means a bad key is a log line at boot
    instead of a slow first call. It does not fail the boot: an unset key is "the model is off", and
    `/api/chat` says 503 when it is.
    """
    try:
        get_model()
    except NotConfigured:
        # A warning rather than info, because uvicorn's default logging does not show info from this
        # logger: an operator would see nothing at boot and discover it from a 503. It is not an
        # error -- a deployment without a key is a deployment with the model turned off.
        logger.warning("provider key is unset: /api/chat will answer 503")
    yield
    await close_model()


api = FastAPI(title="hyperchat", lifespan=lifespan)


@api.get("/")
def hello() -> dict[str, str]:
    return {"hello": "world"}


def require_caller(request: Request) -> Caller:
    """The trust boundary. Everything protected depends on this and nothing else checks a token."""
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=401,
            detail="expected an Authorization: Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        return verify_token(token, settings=settings)
    except AuthError as exc:
        # One answer for every verification failure. Which check failed is for the tests and the
        # operator, not for the caller: the caller learns only that the token is not acceptable.
        raise HTTPException(
            status_code=401,
            detail="token is not acceptable",
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        ) from exc
    except NotAllowedError as exc:
        # Distinct from 401 on purpose: the token is good, the subject is not on the list.
        raise HTTPException(status_code=403, detail="caller is not on the allowlist") from exc
    except UpstreamError as exc:
        # The keys could not be fetched, so nothing was decided. 503 says "try again", which is the
        # only honest answer; 401 would tell the caller to fix a token that may be perfect.
        raise HTTPException(status_code=503, detail="could not check the token right now") from exc


# The dependency lives on the router, not on each route. A route added here that forgets to name it
# is still behind the boundary; a rule enforced at every call site is a rule that will eventually be
# missing from one, and the failure is a 200 to anyone. `/api/me` names it as well because it needs
# the value, not because the router does not already require it -- FastAPI resolves it once.
protected = APIRouter(prefix="/api", dependencies=[Depends(require_caller)])
api.include_router(protected)


@protected.get("/me")
def me(caller: Annotated[Caller, Depends(require_caller)]) -> dict[str, str | None]:
    return {"sub": caller.sub, "email": caller.email}


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)


@protected.post("/chat")
async def chat(
    body: ChatRequest, caller: Annotated[Caller, Depends(require_caller)]
) -> dict[str, str]:
    """One message through the graph. The subject is what makes the spend attributable to a person."""
    try:
        result = await graph.ainvoke(
            {"messages": [HumanMessage(body.message)]},
            {"metadata": {"sub": caller.sub}},
        )
    except NotConfigured as exc:
        # The feature is off, not broken. 503 says "not now", which is true and not the caller's fault.
        raise HTTPException(status_code=503, detail="the model is not configured") from exc
    except ProviderFailed as exc:
        # The reason is logged and the caller gets a sentence with nothing in it. The prompt is not
        # in either: it is the caller's content and it is not ours to keep.
        logger.error("provider call failed: %s", exc)
        raise HTTPException(status_code=502, detail="the model provider failed") from exc

    reply = result["messages"][-1].content
    return {"reply": reply if isinstance(reply, str) else str(reply)}
