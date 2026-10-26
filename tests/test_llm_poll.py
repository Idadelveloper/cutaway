"""A failed status poll of a background interaction is retried, not treated as the job failing."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from google.genai._gaos.lib.compat_errors import APITimeoutError, BadRequestError

from cutaway import config, llm


class FakeInteractions:
    def __init__(self, errors: list[Exception]):
        self.errors = errors
        self.calls = 0

    async def get(self, id, timeout=None):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return SimpleNamespace(id=id, status="completed")


def _timeout():
    return APITimeoutError(request=httpx.Request("GET", "https://example.invalid"))


def test_poll_retries_timeouts(monkeypatch):
    monkeypatch.setattr(config, "POLL_SECONDS", 0)
    aio = FakeInteractions([_timeout(), _timeout()])
    got = asyncio.run(llm._poll(aio, SimpleNamespace(id="i1"), None, "build"))
    assert got.status == "completed" and aio.calls == 3


def test_poll_gives_up_after_retries(monkeypatch):
    monkeypatch.setattr(config, "POLL_SECONDS", 0)
    aio = FakeInteractions([_timeout() for _ in range(llm.POLL_RETRIES)])
    with pytest.raises(APITimeoutError):
        asyncio.run(llm._poll(aio, SimpleNamespace(id="i1"), None, "build"))
    assert aio.calls == llm.POLL_RETRIES


def test_poll_does_not_retry_client_errors(monkeypatch):
    monkeypatch.setattr(config, "POLL_SECONDS", 0)
    req = httpx.Request("GET", "https://example.invalid")
    err = BadRequestError("bad", response=httpx.Response(400, request=req), body=None)
    aio = FakeInteractions([err])
    with pytest.raises(BadRequestError):
        asyncio.run(llm._poll(aio, SimpleNamespace(id="i1"), None, "build"))
    assert aio.calls == 1
