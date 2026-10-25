"""Which stages to run for a target, skipping work a resumed run already has."""
from __future__ import annotations

from ..schemas import RunState
from .base import Stage
from .builder import Builder
from .fixer import FixLoop
from .intake import Intake
from .render import Render
from .research import Research, load_cache, save_cache
from .review import Review
from .spec_writer import SpecWriter

TARGETS = ["research", "spec", "build", "render", "review", "loop", "publish"]


class ResearchStage(Stage):
    """Intake + research, or the per-topic cache unless fresh."""
    name = "research"

    def __init__(self, fresh: bool = False):
        self.fresh = fresh

    async def run(self, state: RunState) -> RunState:
        if state.sources and state.research and not self.fresh:
            return state
        if not self.fresh and load_cache(state):
            return state
        state = await Intake().run(state)
        state = await Research().run(state)
        save_cache(state)
        return state


def stages_for(state: RunState, until: str, fresh_research: bool = False) -> list[Stage]:
    want = TARGETS[:TARGETS.index(until) + 1]
    out: list[Stage] = [ResearchStage(fresh_research)]
    if "spec" in want and not state.spec:
        out.append(SpecWriter())
    if "build" in want and state.version < 1:
        out.append(Builder())
    has_review = bool(state.versions.get(state.version, {}).get("findings"))
    if "render" in want and not has_review:
        out.append(Render())
    if "review" in want and not has_review:
        out.append(Review())
    if "loop" in want:
        out.append(FixLoop())
    if "publish" in want:
        from .publish import Publish
        out.append(Publish())
    return out
