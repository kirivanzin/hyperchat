"""The app.

Two values are read at import — the expected audience and the allowlist — and the process refuses to
start without them. That is deliberate: a service that boots with an empty allowlist answers 403 to
everybody while its readiness probe stays green, so the failure has to be a crash rather than a
healthy-looking process nobody can use.

`/` stays open. It returns a constant and spends nothing. Everything under `/api` is behind the trust
boundary, and the dependency that enforces it sits on the router rather than on the routes, so adding
a route there is the only step required to protect it.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request

from hyperchat.auth import AuthError, Caller, NotAllowedError, UpstreamError, verify_token
from hyperchat.config import settings

api = FastAPI(title="hyperchat")


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
