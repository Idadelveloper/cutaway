"""Render stage: run the harness on the current version."""
from __future__ import annotations

from pathlib import Path

from ..harness.render import render_version
from ..schemas import RunState
from .base import Stage, run_dir, stage_event


class Render(Stage):
    name = "render"

    async def run(self, state: RunState) -> RunState:
        n = state.version
        vdir = Path(state.versions[n]["dir"])
        stage_event(state, "render", f"v{n}: {len(state.spec.figures)} figures")
        timing = await render_version(vdir, run_dir(state), n, state.spec, run_id=state.run_id)
        state.versions[n]["render"] = {k: timing[k] for k in ("sweeps", "frames", "total_s")}
        stage_event(state, "render", f"v{n}: {timing['sweeps']} clips, {timing['frames']} frames "
                                     f"in {timing['total_s']}s, {len(timing['errors'])} errors")
        return state
