"""Progress events → runs/<id>/events.jsonl, plus in-process subscribers for SSE."""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
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


# ---------- run lifecycle: running.pid while a process works on a run; a final "done" event ----------

def _pidfile(run_id: str) -> Path:
    return config.RUNS_DIR / run_id / "running.pid"


@contextlib.asynccontextmanager
async def lifecycle(run_id: str, what: str = "run"):
    """Mark a run as running (pid file) for the duration; emit done with status complete/failed at the end."""
    p = _pidfile(run_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(os.getpid()))
    try:
        yield
    except BaseException as e:
        p.unlink(missing_ok=True)
        emit(run_id, "error", msg=f"{what} failed: {e!r}"[:800])
        emit(run_id, "done", status="failed", msg=f"{what} failed")
        raise
    p.unlink(missing_ok=True)
    emit(run_id, "done", status="complete", msg=f"{what} complete")


def is_running(run_id: str) -> bool:
    p = _pidfile(run_id)
    if not p.exists():
        return False
    try:
        os.kill(int(p.read_text().strip()), 0)
        return True
    except (ValueError, ProcessLookupError, PermissionError):
        return False


STALE_S = 30 * 60


def run_status(run_id: str) -> dict:
    """running | complete | failed | stopped, plus progress through the pipeline steps."""
    evs = history(run_id)
    published = (config.RUNS_DIR / run_id / "final" / "index.html").exists()
    last_done = next((e for e in reversed(evs) if e.get("kind") == "done"), None)
    last_ts = evs[-1]["ts"] if evs else 0
    if is_running(run_id):
        status = "running"
    elif last_done is not None and last_done is evs[-1]:
        status = last_done.get("status") or ("failed" if "fail" in last_done.get("msg", "") else "complete")
    elif published:
        status = "complete"
    elif evs and time.time() - last_ts < STALE_S and not (config.RUNS_DIR / run_id / "running.pid").exists():
        status = "running"      # started by an older process without a pid file
    else:
        status = "stopped" if evs else "new"
    seen = [e.get("stage") for e in evs if e.get("kind") == "stage"]
    first_ts = evs[0]["ts"] if evs else None
    ts = [e["ts"] for e in evs] + ([time.time()] if status == "running" else [])
    active = sum(b - a for a, b in zip(ts, ts[1:]) if b - a < STALE_S)   # ignore idle gaps between sessions
    return {"status": status, "stages_seen": list(dict.fromkeys(s for s in seen if s)),
            "current": seen[-1] if seen else None, "started": first_ts, "elapsed_s": round(active, 1),
            "last_msg": evs[-1].get("msg", "") if evs else "", "published": published}
