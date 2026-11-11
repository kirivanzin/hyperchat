"""A hello-world, and nothing else yet.

Nothing is read at import. A service that refuses to start without a value it does not need is a
service only its author can run, and this one has no value to be missing.
"""

from fastapi import FastAPI

api = FastAPI(title="hyperchat")


@api.get("/")
def hello() -> dict[str, str]:
    return {"hello": "world"}
