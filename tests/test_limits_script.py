"""`scripts/limits.sh` in both directions, against a fake service.

The check exists because a limiter that never fires passes a test written only one way round. So
there are two servers here: one that allows exactly LIMIT and one that allows a hundred, and the
second must make the script exit 1 rather than shrug. No provider, no token, no container — the
script is the thing under test, and it only needs `/api/chat` to answer 200 or 429.
"""

from __future__ import annotations

import http.server
import os
import subprocess
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "limits.sh"


class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        self.server.seen += 1
        if self.server.seen <= self.server.allowed:
            body = b'{"reply": "OK"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(429)
        if self.server.retry_after is not None:
            self.send_header("Retry-After", self.server.retry_after)
        self.end_headers()

    def log_message(self, *_args: object) -> None:
        """Silence: the script's output is what the test reads."""


class Service:
    """A `/api/chat` that allows `allowed` requests, then refuses with (or without) Retry-After."""

    def __init__(self, allowed: int, retry_after: str | None = "37") -> None:
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.allowed = allowed
        self.httpd.retry_after = retry_after
        self.httpd.seen = 0
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self) -> str:
        self.thread.start()
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}"

    def __exit__(self, *_exc: object) -> None:
        self.httpd.shutdown()
        self.thread.join()
        # `shutdown` stops the loop; the listening socket is what a resource warning is about, and
        # with `filterwarnings = ["error"]` an unclosed one fails the test rather than leaking.
        self.httpd.server_close()


def limits(url: str, limit: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), url, limit],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "TOKEN": "test-token"},
    )


def test_limit_allowed_and_two_refused():
    with Service(allowed=3) as url:
        done = limits(url, "3")

    assert done.returncode == 0, done.stderr
    assert "3 allowed, 2 refused" in done.stdout
    assert "Retry-After: 37s" in done.stdout


def test_a_service_that_allows_more_reports_the_failure_rather_than_passing():
    """The direction that catches a limiter which is configured too high to ever fire."""
    with Service(allowed=100) as url:
        done = limits(url, "3")

    assert done.returncode == 1
    assert "expected 429, got 200" in done.stderr
    assert "allowed more than LIMIT=3" in done.stderr


def test_a_limit_that_fires_early_is_a_failure_too():
    with Service(allowed=2) as url:
        done = limits(url, "3")

    assert done.returncode == 1
    assert "the limit fired before LIMIT=3" in done.stderr


def test_a_429_without_retry_after_is_a_failure():
    """`Retry-After` is half of the answer: without it the caller cannot tell whether to wait."""
    with Service(allowed=1, retry_after=None) as url:
        done = limits(url, "1")

    assert done.returncode == 1
    assert "without a Retry-After" in done.stderr


@pytest.mark.parametrize("limit", ["three", "-1", ""])
def test_limit_must_be_a_whole_number(limit):
    done = limits("http://127.0.0.1:1", limit)

    assert done.returncode == 2
    assert "LIMIT must be a whole number" in done.stderr or "usage:" in done.stderr
