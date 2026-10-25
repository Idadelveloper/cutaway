"""Progress events → runs/<id>/events.jsonl, plus in-process subscribers for SSE."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from . import config

_subscribers: dict[str, list[asyncio.Queue]] = {}


def _path(run_id: str) -> Path:
    p = config.RUNS_DIR / run_id / "events.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def emit(run_id: str | None, kind: str, **data) -> dict:
    """Record an event. kind: stage|agent|call|finding|info|error|done."""
    ev = {"ts": time.time(), "kind": kind, **data}
    if run_id:
        with open(_path(run_id), "a") as f:
            f.write(json.dumps(ev, default=str) + "\n")
        for q in _subscribers.get(run_id, []):
            q.put_nowait(ev)
    msg = data.get("msg") or data.get("stage") or data.get("agent") or ""
    print(f"[{kind}] {msg}".rstrip(), flush=True)
    return ev


def subscribe(run_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue()
    _subscribers.setdefault(run_id, []).append(q)
    return q


def unsubscribe(run_id: str, q: asyncio.Queue) -> None:
    if q in _subscribers.get(run_id, []):
        _subscribers[run_id].remove(q)


def history(run_id: str) -> list[dict]:
    p = config.RUNS_DIR / run_id / "events.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
