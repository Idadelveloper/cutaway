"""Run status shown in the UI: running while the lifecycle is open, then complete or failed."""
from __future__ import annotations

import asyncio

import pytest

from cutaway import config, events


def test_status_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path)

    async def ok():
        async with events.lifecycle("r1"):
            events.emit("r1", "stage", stage="build", msg="build: working")
            assert events.run_status("r1")["status"] == "running"
    asyncio.run(ok())
    st = events.run_status("r1")
    assert st["status"] == "complete" and st["current"] == "build"
    assert not (tmp_path / "r1" / "running.pid").exists()

    async def bad():
        async with events.lifecycle("r2"):
            raise RuntimeError("boom")
    with pytest.raises(RuntimeError):
        asyncio.run(bad())
    assert events.run_status("r2")["status"] == "failed"

    # a pid file left by a process that died -> not running
    (tmp_path / "r3").mkdir()
    (tmp_path / "r3" / "running.pid").write_text("999999")
    events.emit("r3", "stage", stage="render", msg="render")
    assert events.run_status("r3")["status"] == "stopped"
