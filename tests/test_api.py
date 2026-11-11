"""One route, and the negative that proves the test can fail.

The 404 is not decoration. A test that only asserts 200 cannot tell a working route from an app
that answers everything with 200 — which is exactly what a mis-mounted router looks like, and it
looks like success.
"""

from fastapi.testclient import TestClient

from hyperchat.main import api

client = TestClient(api)


def test_the_root_greets():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"hello": "world"}


def test_an_unknown_path_is_not_the_root():
    assert client.get("/nope").status_code == 404
