"""Intake (Flash): discover sources and write a research plan."""
from __future__ import annotations

from .. import config, llm, sources
from ..schemas import ResearchPlan, RunState
from .base import Stage, run_dir, save_json, stage_event


class Intake(Stage):
    name = "intake"

    async def run(self, state: RunState) -> RunState:
        topic = state.topic
        found = sources.discover(topic["sources_dir"])
        state.sources = [s.model_dump() for s in found]
        stage_event(state, "intake", f"{len(found)} sources: " + ", ".join(f"{s.id}={s.type}" for s in found))
        listing = "\n".join(f"- {s.id} ({s.type}): {s.title} [{s.url_or_file}]" for s in found) or "(none)"
        prompt = config.load_prompt(
            "intake", title=topic["title"], prompt=topic["prompt"], audience=topic["audience"],
            figure_hints="; ".join(topic.get("figure_hints", [])), sources=listing)
        plan, _ = await llm.call_model(ResearchPlan, stage="intake", model=config.FLASH, thinking="low",
                                       input=prompt, run_id=state.run_id)
        state.plan = plan.model_dump()
        save_json(run_dir(state) / "research" / "plan.json", plan)
        save_json(run_dir(state) / "research" / "sources.json", state.sources)
        return state
