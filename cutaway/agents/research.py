"""Research jobs, run in parallel:

  VideoAnalyst   (Argon high)    one job per video: timestamped facts + comparison moments
  DiagramReader  (Argon medium)  all images in one job: entities, layout, labels, numbers
  PaperReader    (Argon medium)  one job per PDF: equations, constants, claims with pages
  WebReader      (Argon medium)  one job per web page in urls.txt, via the URL context tool
  WebResearcher  (Deep Research agent, --with-deep-research)
  SourceScout    (Antigravity managed agent, --with-scout)
"""
from __future__ import annotations

import asyncio
import json

from .. import config, events, llm, sources
from ..schemas import ResearchOutput, RunState, Source
from .base import Stage, run_dir, save_json, stage_event

RULES = (config.PROMPTS_DIR / "_rules.md").read_text()


def _focus(state: RunState, sid: str) -> str:
    return (state.plan or {}).get("focus", {}).get(sid, "everything relevant to the topic")


def _questions(state: RunState) -> str:
    return "\n".join(f"- {q}" for q in (state.plan or {}).get("questions", [])) or "- (see brief)"


async def video_analyst(state: RunState, src: Source) -> ResearchOutput:
    part = await llm.media_part(src.url_or_file) if src.local else {"type": "video", "uri": src.url_or_file}
    prompt = config.load_prompt("video", source_id=src.id, name=src.title, title=state.topic["title"],
                                focus=_focus(state, src.id), rules=RULES)
    out, _ = await llm.call_model(ResearchOutput, stage=f"video_{src.id}", model=config.ARGON, thinking="high",
                                  input=[part, llm.text_part(prompt)], run_id=state.run_id)
    return out


async def diagram_reader(state: RunState, srcs: list[Source]) -> ResearchOutput:
    parts = []
    for s in srcs:
        parts += [llm.text_part(f"Image {s.id}: {s.title}"), llm.file_part(s.url_or_file)]
    focus = "; ".join(f"{s.id}: {_focus(state, s.id)}" for s in srcs)
    prompt = config.load_prompt("diagrams", source_ids=", ".join(s.id for s in srcs),
                                title=state.topic["title"], focus=focus, rules=RULES)
    out, _ = await llm.call_model(ResearchOutput, stage="diagrams", model=config.ARGON, thinking="medium",
                                  input=parts + [llm.text_part(prompt)], run_id=state.run_id)
    return out


async def paper_reader(state: RunState, src: Source) -> ResearchOutput:
    path = src.url_or_file if src.local else await asyncio.to_thread(
        sources.download_pdf, src, state.topic["sources_dir"])
    part = await llm.media_part(path)
    prompt = config.load_prompt("papers", source_id=src.id, name=src.title, title=state.topic["title"],
                                focus=_focus(state, src.id), rules=RULES)
    out, _ = await llm.call_model(ResearchOutput, stage=f"paper_{src.id}", model=config.ARGON, thinking="medium",
                                  input=[part, llm.text_part(prompt)], run_id=state.run_id)
    return out


async def web_reader(state: RunState, src: Source) -> ResearchOutput:
    prompt = config.load_prompt("webpage", url=src.url_or_file, source_id=src.id, title=state.topic["title"],
                                focus=_focus(state, src.id), rules=RULES)
    out, _ = await llm.call_model(ResearchOutput, stage=f"web_{src.id}", model=config.ARGON, thinking="medium",
                                  input=prompt, tools=[{"type": "url_context"}], response_json=False,
                                  run_id=state.run_id)
    return out


async def web_researcher(state: RunState) -> ResearchOutput:
    t = state.topic
    prompt = config.load_prompt("web", title=t["title"], prompt=t["prompt"], questions=_questions(state))
    report = await llm.call(stage="deep_research", agent=config.DEEP_RESEARCH, input=prompt,
                            background=True, run_id=state.run_id)
    (run_dir(state) / "research" / "deep_research_report.md").write_text(report.text)
    out, _ = await llm.call_model(ResearchOutput, stage="deep_research_extract", model=config.FLASH,
                                  thinking="low", input=config.load_prompt("web_extract", report=report.text),
                                  run_id=state.run_id)
    return out


async def source_scout(state: RunState) -> ResearchOutput:
    prompt = config.load_prompt("scout", title=state.topic["title"], questions=_questions(state))
    out, _ = await llm.call_model(
        ResearchOutput, stage="scout", agent=config.ANTIGRAVITY,
        agent_config={"type": "antigravity", "model": config.ARGON}, environment="remote",
        input=prompt, response_json=False, run_id=state.run_id)
    return out


class Research(Stage):
    name = "research"

    async def run(self, state: RunState) -> RunState:
        srcs = [Source(**s) for s in state.sources]
        jobs: dict[str, asyncio.Future] = {}
        for s in srcs:
            if s.type == "video":
                jobs[f"video_{s.id}"] = video_analyst(state, s)
            elif s.type in ("paper", "patent"):
                jobs[f"paper_{s.id}"] = paper_reader(state, s)
            elif s.type == "web":
                jobs[f"web_{s.id}"] = web_reader(state, s)
        diagrams = [s for s in srcs if s.type == "diagram"]
        if diagrams:
            jobs["diagrams"] = diagram_reader(state, diagrams)
        if state.flags.get("with_deep_research"):
            jobs["deep_research"] = web_researcher(state)
        if state.flags.get("with_scout"):
            jobs["scout"] = source_scout(state)
        if not jobs:
            raise RuntimeError(f"no sources in {state.topic['sources_dir']} and no web research enabled "
                               "(add files/urls.txt, or pass --with-deep-research / --with-scout)")

        stage_event(state, "research", f"{len(jobs)} jobs in parallel: {', '.join(jobs)}")

        async def guarded(name, coro):
            events.emit(state.run_id, "agent", agent=name, status="start", msg=f"{name} started")
            try:
                out = await coro
                save_json(run_dir(state) / "research" / f"{name}.json", out)
                events.emit(state.run_id, "agent", agent=name, status="done",
                            msg=f"{name} done: {len(out.items)} items")
                return name, out
            except Exception as e:  # one failing source must not kill the run
                events.emit(state.run_id, "error", agent=name, msg=f"{name} failed: {e!r}"[:500])
                return name, None

        results = await asyncio.gather(*(guarded(n, c) for n, c in jobs.items()))
        ok = {n: o for n, o in results if o is not None}
        if not ok:
            raise RuntimeError("all research jobs failed")
        state.research = {n: o.model_dump() for n, o in ok.items()}
        # register sources discovered by web research / scout
        known = {s["id"] for s in state.sources}
        for o in ok.values():
            for s in o.sources:
                if s.get("id") and s["id"] not in known:
                    state.sources.append({"id": s["id"], "type": s.get("type", "web"), "title": s.get("title", ""),
                                          "url_or_file": s.get("url", s.get("url_or_file", "")), "local": False})
                    known.add(s["id"])
        save_json(run_dir(state) / "research" / "sources.json", state.sources)
        n_items = sum(len(o.items) for o in ok.values())
        stage_event(state, "research", f"{len(ok)}/{len(jobs)} jobs ok, {n_items} items")
        return state


def research_digest(state: RunState) -> str:
    """Compact JSON of all research for the spec writer."""
    return json.dumps(state.research, indent=1)
