"""ADK workflow (google-adk 2.13, classic agents).

  SequentialAgent root
    intake -> research -> spec -> build -> render -> review -> fix_loop -> publish

Each stage is a custom BaseAgent wrapping a plain async Stage (`run(state) -> state`), which calls the
Interactions API directly (long decode, agents, video/PDF input never go through ADK's model calls).
Parallelism lives inside the stages (asyncio.gather): the three discoverers + analysts in research,
and code checks + fidelity + per-figure watched reviews in review. The fix loop (with its revert rule)
is one stage. RunState is kept in a module registry keyed by run id; ADK session state holds the id.
"""
from __future__ import annotations

from typing import AsyncGenerator

from google.adk.agents import BaseAgent, SequentialAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events.event import Event
from google.adk.runners import InMemoryRunner
from google.genai import types

from ..schemas import RunState
from .base import Stage, save_state

_STATES: dict[str, RunState] = {}


class StageAgent(BaseAgent):
    """ADK agent around one pipeline Stage."""

    stage: Stage

    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        run_id = ctx.session.state["run_id"]
        state = await self.stage.run(_STATES[run_id])
        _STATES[run_id] = state
        save_state(state)
        yield Event(author=self.name, invocation_id=ctx.invocation_id,
                    content=types.Content(role="model", parts=[types.Part(text=f"{self.name}: done ({state.stage})")]))


def build_root(stages: list[Stage]) -> SequentialAgent:
    return SequentialAgent(name="cutaway", description="research -> spec -> build -> verify -> fix -> publish",
                           sub_agents=[StageAgent(name=s.name, stage=s) for s in stages])


async def run_pipeline(state: RunState, stages: list[Stage]) -> RunState:
    """Run the stages under ADK; returns the final RunState."""
    _STATES[state.run_id] = state
    runner = InMemoryRunner(agent=build_root(stages), app_name="cutaway")
    await runner.session_service.create_session(app_name="cutaway", user_id="cutaway", session_id=state.run_id,
                                                state={"run_id": state.run_id})
    async for _ in runner.run_async(user_id="cutaway", session_id=state.run_id,
                                    new_message=types.Content(role="user", parts=[types.Part(text=state.run_id)])):
        pass
    return _STATES.pop(state.run_id)
