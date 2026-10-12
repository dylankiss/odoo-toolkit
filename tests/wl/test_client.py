import io
import socket
import threading
import time
import zipfile
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, ClassVar

import pytest
from requests import PreparedRequest, ReadTimeout, Response
from typer.testing import CliRunner

from odoo_toolkit.common import TransientProgress
from odoo_toolkit.main import app
from odoo_toolkit.wl import common as wl_common
from odoo_toolkit.wl import copy as wl_copy
from odoo_toolkit.wl.common import (
    REQUEST_TIMEOUT,
    WeblateApi,
    WeblateApiError,
    WeblateConnectionError,
    WeblateRateLimiter,
    get_weblate_project_languages,
)


def make_response(status: int, *, headers: dict[str, str] | None = None, body: bytes = b"{}") -> Response:
    request = PreparedRequest()
    request.prepare(method="POST", url="https://translate.example.com/api/x/", headers={"Content-Type": "text/plain"})
    response = Response()
    response.status_code = status
    response.headers.update(headers or {})
    response.raw = io.BytesIO(body)
    response.request = request
    return response


class FakeWeblate(BaseHTTPRequestHandler):
    """Answer each request with the next of the queued responses, and remember the requests."""

    queued: ClassVar[list[tuple[int, dict[str, str], bytes]]] = []
    requests: ClassVar[list[str]] = []

    def _answer(self) -> None:
        self.requests.append(self.command)
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        status, headers, body = self.queued.pop(0)
        self.send_response(status)
        for key, value in {"Content-Type": "application/json", **headers}.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = _answer  # noqa: N815

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002, ANN401
        pass


def no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    """Don't space requests, nor wait between retries."""
    monkeypatch.setattr(wl_common, "RETRY", wl_common.RETRY.new(backoff_factor=0, backoff_jitter=0))
    monkeypatch.setattr(wl_common, "RATE_LIMITER", WeblateRateLimiter(1_000_000, 1_000_000, 20))
    monkeypatch.setattr(wl_common, "RATE_LIMITED_PAUSE", 0)


def timed(function: Any) -> float:  # noqa: ANN401
    start = time.monotonic()
    function()
    return time.monotonic() - start


@pytest.fixture
def weblate(monkeypatch: pytest.MonkeyPatch) -> Iterator[type[FakeWeblate]]:
    """Run a fake Weblate server, and point the API at it, without waiting between retries."""
    FakeWeblate.queued = []
    FakeWeblate.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeWeblate)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(wl_common, "WEBLATE_URL", f"http://127.0.0.1:{server.server_address[1]}")
    monkeypatch.setattr(wl_common, "WEBLATE_API_TOKEN", "secret")
    no_waiting(monkeypatch)
    yield FakeWeblate
    server.shutdown()


def test_rate_limited_uploads_are_retried(weblate: type[FakeWeblate]) -> None:
    weblate.queued = [(429, {"Retry-After": "0"}, b"{}"), (200, {}, b'{"accepted": 1}')]

    result = WeblateApi().post(dict[str, Any], "/api/translations/p/c/fr/file/", data={"method": "translate"})

    assert result == {"accepted": 1}
    assert weblate.requests == ["POST", "POST"]


def test_uploads_refused_by_nginx_are_retried(weblate: type[FakeWeblate]) -> None:
    # nginx's rate limit doesn't say how long to wait.
    weblate.queued = [(429, {}, b"<html>Too Many Requests</html>"), (200, {}, b'{"accepted": 1}')]

    result = WeblateApi().post(dict[str, Any], "/api/translations/p/c/fr/file/", data={"method": "translate"})

    assert result == {"accepted": 1}
    assert weblate.requests == ["POST", "POST"]


def test_requests_wait_when_weblate_rate_limit_runs_low(weblate: type[FakeWeblate]) -> None:
    low = {"X-RateLimit-Limit": "5000", "X-RateLimit-Remaining": "3", "X-RateLimit-Reset": "0.3"}
    weblate.queued = [(200, low, b"{}"), (200, {}, b"{}")]
    api = WeblateApi()

    api.get(dict[str, Any], "/api/projects/")

    assert timed(lambda: api.get(dict[str, Any], "/api/projects/")) >= 0.25


def test_generator_reads_paginated_and_plain_list_responses(weblate: type[FakeWeblate]) -> None:
    next_page = f'{{"next": "{wl_common.WEBLATE_URL}/api/projects/?page=2", "results": [{{"slug": "a"}}]}}'.encode()
    weblate.queued = [(200, {}, next_page), (200, {}, b'{"next": null, "results": [{"slug": "b"}]}')]
    assert list(WeblateApi().get_generator(dict[str, str], "/api/projects/")) == [{"slug": "a"}, {"slug": "b"}]

    # Some endpoints, like a project's languages, aren't paginated.
    weblate.queued = [(200, {}, b'[{"code": "fr"}, {"code": "nl"}]')]
    assert get_weblate_project_languages(WeblateApi(), "p") == ["fr", "nl"]


def test_rate_limiter_allows_a_burst_then_a_steady_rate() -> None:
    limiter = WeblateRateLimiter(20, 3, 20)

    assert timed(lambda: [limiter.acquire() for _ in range(3)]) < 0.03
    assert timed(lambda: [limiter.acquire() for _ in range(3)]) >= 0.13


def test_rate_limiter_drain_falls_back_to_the_steady_rate() -> None:
    limiter = WeblateRateLimiter(20, 100, 20)

    limiter.drain()

    assert timed(lambda: [limiter.acquire() for _ in range(3)]) >= 0.13


def test_nginx_rate_limit_drains_the_burst(weblate: type[FakeWeblate], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wl_common, "RATE_LIMITER", WeblateRateLimiter(20, 100, 20))
    weblate.queued = [(429, {}, b"Too Many Requests"), (200, {}, b"{}"), (200, {}, b"{}"), (200, {}, b"{}")]
    api = WeblateApi()

    api.get(dict[str, Any], "/api/projects/")

    assert timed(lambda: [api.get(dict[str, Any], "/api/projects/") for _ in range(2)]) >= 0.08


def test_rate_limiter_only_pauses_when_few_requests_remain() -> None:
    limiter = WeblateRateLimiter(1000, 1000, 20)

    limiter.update({})
    limiter.update({"X-RateLimit-Remaining": "4000", "X-RateLimit-Reset": "10"})
    assert timed(limiter.acquire) < 0.1

    limiter.update({"X-RateLimit-Remaining": "19", "X-RateLimit-Reset": "0.2"})
    assert timed(limiter.acquire) >= 0.15


def test_rate_limiter_pause_applies_to_waiting_requests() -> None:
    limiter = WeblateRateLimiter(5, 1, 20)
    limiter.acquire()

    # The next request waits for its turn, and gets paused in the meantime.
    threading.Timer(0.05, lambda: limiter.pause(0.4, "Paused")).start()

    assert timed(limiter.acquire) >= 0.4


def test_uploads_are_not_retried_after_a_server_error(weblate: type[FakeWeblate]) -> None:
    weblate.queued = [(500, {}, b'{"errors": [{"detail": "Server error"}]}')]

    with pytest.raises(WeblateApiError) as error:
        WeblateApi().post(dict[str, Any], "/api/translations/p/c/fr/file/", data={"method": "translate"})

    # Weblate may have processed it, so it must not run twice.
    assert weblate.requests == ["POST"]
    assert error.value.summary == "HTTP 500: Server error"


def test_downloads_are_retried_after_a_server_error(weblate: type[FakeWeblate]) -> None:
    weblate.queued = [(503, {}, b"{}"), (502, {}, b"{}"), (200, {}, b"po file")]

    assert WeblateApi().get_bytes("/api/translations/p/c/fr/file/") == b"po file"
    assert weblate.requests == ["GET", "GET", "GET"]


def test_unreachable_weblate_becomes_a_weblate_error(monkeypatch: pytest.MonkeyPatch) -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    # Nothing listens on the port anymore.
    monkeypatch.setattr(wl_common, "WEBLATE_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setattr(wl_common, "WEBLATE_API_TOKEN", "secret")
    no_waiting(monkeypatch)

    with pytest.raises(WeblateConnectionError, match="couldn't connect"):
        WeblateApi().get_bytes("/api/file/")


def test_error_summary_and_hidden_upload_body() -> None:
    response = make_response(400, body=b'{"type": "validation_error", "errors": [{"detail": "Bad file.\\nMore."}]}')
    response.request.body = b"x" * 10_000

    error = WeblateApiError(response)

    assert error.summary == "HTTP 400: Bad file."
    assert "<10000 bytes>" in str(error)
    assert "xxxx" not in str(error)


def test_every_request_has_a_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wl_common, "WEBLATE_API_TOKEN", "secret")
    timeouts: list[Any] = []

    def request(*_: object, **kwargs: Any) -> Response:  # noqa: ANN401
        timeouts.append(kwargs.get("timeout"))
        return make_response(200, body=b'{"results": []}')

    api = WeblateApi()
    monkeypatch.setattr(api.json_session, "request", request)
    monkeypatch.setattr(api.json_session, "get", request)
    monkeypatch.setattr(api.session, "get", request)

    api.get(dict[str, Any], "/api/projects/")
    api.get_bytes("/api/file/")
    list(api.get_generator(dict[str, Any], "/api/components/"))

    assert timeouts == [REQUEST_TIMEOUT] * 3


class FakeApi:
    """Answers a failing project ZIP download, and one file per component."""

    def get_bytes(self, endpoint: str, *, params: dict[str, str] | None = None) -> bytes:  # noqa: ARG002
        if "/languages/" in endpoint:
            raise WeblateConnectionError(ReadTimeout())
        if "/broken/" in endpoint:
            raise WeblateApiError(make_response(500, body=b'{"errors": [{"detail": "Server error"}]}'))
        return b'msgid ""\nmsgstr ""\n"Language: fr\\n"\n\nmsgid "Hello"\nmsgstr "Bonjour"\n'


def test_fetch_falls_back_to_one_file_per_component_when_the_zip_fails() -> None:
    components = {f"module_{i}" for i in range(wl_copy.BULK_ZIP_MIN_COMPONENTS)} | {"broken"}

    with TransientProgress() as progress:
        files, failures = wl_copy.fetch_language_sources(
            FakeApi(),  # pyright: ignore[reportArgumentType]
            "odoo-19",
            "fr",
            components,
            query=None,
            progress=progress,
            task_id=progress.add_task("test"),
        )

    assert len(files) == wl_copy.BULK_ZIP_MIN_COMPONENTS
    assert failures == {"broken": "HTTP 500: Server error"}


def test_zip_extraction_still_works() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("odoo/addons/sale/i18n/fr.po", b"content")
    assert wl_common.extract_po_files_from_zip(buffer.getvalue(), "fr", {"sale"}) == {"sale": b"content"}


def test_cli_copy_invalid_arguments_exit_with_an_error() -> None:
    result = CliRunner().invoke(app, ["wl", "copy", "-p", "odoo-19", "-l", "fr,nl", "-L", "de"])
    assert result.exit_code == 1
    assert "exactly one source language" in result.output
