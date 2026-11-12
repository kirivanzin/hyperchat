"""One route, and the negative that proves the test can fail.

The 404 is not decoration. A test that only asserts 200 cannot tell a working route from an app
that answers everything with 200 — which is exactly what a mis-mounted router looks like, and it
looks like success.
"""

from __future__ import annotations

import os

from fastapi.testclient import TestClient

from hyperchat import main
from hyperchat.config import Settings
from hyperchat.main import api

client = TestClient(api)


def test_the_root_greets():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"hello": "world"}


def test_an_unknown_path_is_not_the_root():
    assert client.get("/nope").status_code == 404


def test_livez_reports_the_process():
    response = client.get("/livez")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "pid": os.getpid()}


def test_readyz_is_ready_with_the_settings_loaded_at_import():
    response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_fails_when_the_cached_trust_boundary_is_empty(monkeypatch):
    # The failed side of the probe: readiness has to be able to say no, or it is a route that always
    # returns 200 and a test that cannot fail.
    monkeypatch.setattr(main, "settings", Settings(expected_audience="", allowed_subs=frozenset()))

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["detail"] == "not ready: the trust boundary is not configured"


def test_healthz_is_not_a_route():
    # Google Frontend reserves this exact path and never forwards it, so a route here would look
    # configured and be unreachable. The absence is deliberate, not an oversight.
    assert client.get("/healthz").status_code == 404
