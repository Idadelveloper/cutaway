"""Plain async stages with a uniform interface: `await stage.run(state) -> state`.

agents/root.py wraps these in ADK agents; scripts can also call them directly.
"""
from __future__ import annotations

import json
from pathlib import Path

from .. import config, events
from ..schemas import RunState


class Stage:
    name = "stage"

    async def run(self, state: RunState) -> RunState:  # pragma: no cover - interface
        raise NotImplementedError


def run_dir(state: RunState) -> Path:
    d = config.RUNS_DIR / state.run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_json(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)

    def plain(o):
        return o.model_dump(mode="json") if hasattr(o, "model_dump") else str(o)
    path.write_text(json.dumps(data, indent=2, default=plain))
    return path


def save_state(state: RunState) -> None:
    save_json(run_dir(state) / "state.json", state)


def stage_event(state: RunState, stage: str, msg: str = "", **kw) -> None:
    state.stage = stage
    events.emit(state.run_id, "stage", stage=stage, msg=f"{stage}: {msg}" if msg else stage, **kw)
