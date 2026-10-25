"""Research: autonomous source discovery + analysis.

Discovery (three discoverers in parallel; files in sources/<topic>/ are optional seeds):
  SearchDiscoverer  Argon + google_search + url_context tools -> source list
  WebResearcher     Deep Research agent -> cited report -> Flash extracts the source list
  SourceScout       Antigravity managed agent (Argon) -> sources with full extracted text
Then: merge + dedupe by URL, rank by type, fetch full text for anything without it
(skipping paywalled/blocked pages), save research/sources/<id>.txt + sources.json.

Analysis (parallel):
  VideoAnalyst   Argon high, one per video link (passed as a uri; skipped if the API refuses it)
  DiagramReader  Argon medium, seed images only
"""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from .. import config, events, fetch, llm, stats
from ..schemas import SOURCE_PREFIX, SOURCE_RANK, DiscoveryOutput, ResearchOutput, RunState, Source
from ..sources import VIDEO_URL, discover as discover_seeds
from .base import Stage, run_dir, save_json, stage_event

RULES = (config.PROMPTS_DIR / "_rules.md").read_text()
MAX_SOURCES = 50
MAX_VIDEOS = 5
FETCH_CONCURRENCY = 8


class ScoutOutput(DiscoveryOutput):
    checks: list[dict] = []


def _questions(state: RunState) -> str:
    return "\n".join(f"- {q}" for q in (state.plan or {}).get("questions", [])) or "- (see brief)"


def _focus(state: RunState, sid: str) -> str:
    return (state.plan or {}).get("focus", {}).get(sid, "everything relevant to the topic")


def research_dir(state: RunState) -> Path:
    return run_dir(state) / "research"


def texts_dir(state: RunState) -> Path:
    return research_dir(state) / "sources"


# ---------- discoverers ----------

async def search_discoverer(state: RunState) -> DiscoveryOutput:
    t = state.topic
    prompt = config.load_prompt("discover_search", title=t["title"], prompt=t["prompt"], questions=_questions(state))
    # Tool syntax from google-genai 2.33.0 types: GoogleSearch type "google_search", UrlContext type "url_context".
    out, _ = await llm.call_model(DiscoveryOutput, stage="discover_search", model=config.ARGON, thinking="medium",
                                  input=prompt, tools=[{"type": "google_search"}, {"type": "url_context"}],
                                  response_json=False, background=True, run_id=state.run_id)
    return out


async def web_researcher(state: RunState) -> DiscoveryOutput:
    t = state.topic
    prompt = config.load_prompt("web", title=t["title"], prompt=t["prompt"], questions=_questions(state))
    report = await llm.call(stage="deep_research", agent=config.DEEP_RESEARCH, input=prompt, run_id=state.run_id)
    (research_dir(state) / "deep_research_report.md").write_text(report.text)
    out, _ = await llm.call_model(DiscoveryOutput, stage="deep_research_extract", model=config.FLASH,
                                  thinking="low", input=config.load_prompt("web_extract", report=report.text),
                                  run_id=state.run_id)
    return out


async def source_scout(state: RunState) -> DiscoveryOutput:
    prompt = config.load_prompt("scout", title=state.topic["title"], questions=_questions(state))
    out, _ = await llm.call_model(
        ScoutOutput, stage="scout", agent=config.ANTIGRAVITY,
        agent_config={"type": "antigravity", "model": config.ARGON}, environment="remote",
        input=prompt, response_json=False, run_id=state.run_id)
    save_json(research_dir(state) / "scout_checks.json", out.checks)
    return out


# ---------- merge / rank / fetch ----------

def norm_url(url: str) -> str:
    u = urlsplit(url.strip())
    host = u.netloc.lower().removeprefix("www.").removeprefix("m.")
    q = urlencode([(k, v) for k, v in parse_qsl(u.query) if not k.lower().startswith(("utm_", "fbclid", "gclid"))])
    if "youtube.com" in host and "v=" in u.query:   # keep only the video id
        q = urlencode([(k, v) for k, v in parse_qsl(u.query) if k == "v"])
    return urlunsplit(("https", host, u.path.rstrip("/"), q, ""))


def merge(found: dict[str, DiscoveryOutput], seeds: list[Source]) -> tuple[list[Source], dict[str, str], list[dict]]:
    """Dedupe across discoverers; returns (sources with ids, {source_id: scout text}, skipped)."""
    by_url: dict[str, Source] = {}
    texts: dict[str, str] = {}
    skipped: list[dict] = []
    for s in seeds:
        s.found_by = ["seed"]
        by_url[s.url_or_file if s.local else norm_url(s.url_or_file)] = s
    for who, out in found.items():
        skipped += [{**k, "found_by": who} for k in out.skipped]
        for d in out.sources:
            if not d.url.startswith("http"):
                continue
            key = norm_url(d.url)
            typ = "video" if VIDEO_URL.search(d.url) else d.type
            if key in by_url:
                s = by_url[key]
                if who not in s.found_by:
                    s.found_by.append(who)
                s.date = s.date or d.date
                s.publisher = s.publisher or d.publisher
            else:
                s = by_url[key] = Source(type=typ, title=d.title, url_or_file=d.url, date=d.date,
                                         publisher=d.publisher, found_by=[who])
            if d.text and len(d.text) > len(texts.get(key, "")):
                texts[key] = d.text
    srcs = list(by_url.values())
    for s in srcs:
        s.rank = SOURCE_RANK.get(s.type, 3)
    # seeds always kept; then by rank, then by how many discoverers agree
    order = lambda s: ("seed" not in s.found_by, s.rank, -len(s.found_by))  # noqa: E731
    srcs.sort(key=order)
    videos = [s for s in srcs if s.type == "video"][:MAX_VIDEOS]
    others = [s for s in srcs if s.type != "video"][:MAX_SOURCES - len(videos)]
    dropped = [s for s in srcs if s not in videos and s not in others]
    skipped += [{"url": s.url_or_file, "reason": "over source cap", "found_by": ",".join(s.found_by)} for s in dropped]
    kept = sorted(others + videos, key=order)
    counters: dict[str, int] = {}
    id_texts: dict[str, str] = {}
    for s in kept:
        p = SOURCE_PREFIX[s.type]
        counters[p] = counters.get(p, 0) + 1
        s.id = f"{p}{counters[p]}"
        key = s.url_or_file if s.local else norm_url(s.url_or_file)
        if key in texts:
            id_texts[s.id] = texts[key]
    return kept, id_texts, skipped


async def collect_texts(state: RunState, srcs: list[Source], scout_texts: dict[str, str]) -> None:
    d = texts_dir(state)
    d.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(FETCH_CONCURRENCY)

    async def one(s: Source) -> None:
        if s.type in ("video", "diagram"):
            return  # text comes from VideoAnalyst transcript / DiagramReader
        text, reason = scout_texts.get(s.id, ""), None
        if s.local:
            text = await asyncio.to_thread(fetch.local_text, s.url_or_file)
        elif len(text) < 25000:  # scout text missing or truncated: try the full document
            async with sem:
                r = await fetch.fetch(s.url_or_file)
            if r.ok and len(r.text) > len(text):
                text = r.text
                s.title = s.title or r.title or s.title
            elif not text:
                reason = r.reason
        if text:
            store_text(state, s, text)
        else:
            s.status, s.skip_reason = "skipped", reason or "no text"
            events.emit(state.run_id, "info", stage="research", msg=f"skip {s.id} {s.url_or_file}: {s.skip_reason}")

    await asyncio.gather(*(one(s) for s in srcs))


def store_text(state: RunState, s: Source, text: str) -> None:
    p = texts_dir(state) / f"{s.id}.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    s.status, s.chars, s.text_path, s.skip_reason = "ok", len(text), str(p.relative_to(run_dir(state))), None


# ---------- analysts ----------

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


async def guarded(state: RunState, name: str, coro):
    """Run one job; log and return None on failure (one bad source must not kill the run)."""
    events.emit(state.run_id, "agent", agent=name, status="start", msg=f"{name} started")
    try:
        out = await coro
    except Exception as e:
        events.emit(state.run_id, "error", agent=name, msg=f"{name} failed: {e!r}"[:600])
        return None
    n = len(getattr(out, "items", None) or getattr(out, "sources", None) or [])
    events.emit(state.run_id, "agent", agent=name, status="done", msg=f"{name} done: {n}")
    return out


# ---------- stage ----------

class Research(Stage):
    name = "research"

    async def run(self, state: RunState) -> RunState:
        rdir = research_dir(state)
        rdir.mkdir(parents=True, exist_ok=True)
        seeds = discover_seeds(state.topic["sources_dir"])

        jobs = {"search": search_discoverer(state), "deep_research": web_researcher(state),
                "scout": source_scout(state)}
        stage_event(state, "research", f"discovery: {', '.join(jobs)} in parallel + {len(seeds)} seeds")
        results = await asyncio.gather(*(guarded(state, n, c) for n, c in jobs.items()))
        found = {n: o for n, o in zip(jobs, results) if o is not None}
        for n, o in found.items():
            save_json(rdir / f"discover_{n}.json", o)
        if not found and not seeds:
            raise RuntimeError("all discoverers failed and there are no seed sources")

        srcs, scout_texts, skipped = merge(found, seeds)
        stage_event(state, "research", f"merged {len(srcs)} sources; fetching texts")
        await collect_texts(state, srcs, scout_texts)

        # analysts: videos + seed diagrams, in parallel
        videos = [s for s in srcs if s.type == "video"]
        diagrams = [s for s in srcs if s.type == "diagram"]
        names = [f"video_{s.id}" for s in videos] + (["diagrams"] if diagrams else [])
        coros = [video_analyst(state, s) for s in videos] + ([diagram_reader(state, diagrams)] if diagrams else [])
        stage_event(state, "research", f"analysis: {len(videos)} videos, {len(diagrams)} diagrams")
        outs = await asyncio.gather(*(guarded(state, n, c) for n, c in zip(names, coros)))
        research: dict[str, dict] = {}
        for n, o in zip(names, outs):
            if o is None:
                continue
            research[n] = o.model_dump()
            save_json(rdir / f"{n}.json", o)
        for s in videos:
            o = research.get(f"video_{s.id}")
            if o and o.get("transcript"):
                store_text(state, s, o["transcript"])
            else:
                s.status, s.skip_reason = "skipped", ("video link not accepted by API (see events)" if not o
                                                      else "no transcript returned")
                events.emit(state.run_id, "info", stage="research", msg=f"skip {s.id} {s.url_or_file}: {s.skip_reason}")
        if "diagrams" in research:  # diagram readings become the diagrams' source text
            by_src: dict[str, list[str]] = {}
            for it in research["diagrams"]["items"]:
                by_src.setdefault(it.get("source") or it["id"].split("-")[0], []).append(f"[{it['where']}] {it['text']}")
            for s in diagrams:
                if s.id in by_src:
                    store_text(state, s, "\n".join(by_src[s.id]))

        state.sources = [s.model_dump() for s in srcs]
        state.research = research
        index = {"sources": state.sources, "skipped": skipped,
                 "ranking": "1 = paper/patent/standard/manufacturer/diagram, 2 = article/video, 3 = blog/web"}
        save_json(rdir / "sources.json", index)
        save_json(rdir / "research_state.json", {"sources": state.sources, "research": research, "plan": state.plan})
        stats.save_research_tokens(state)
        ok = [s for s in srcs if s.status == "ok"]
        stage_event(state, "research", f"{len(ok)} sources with text, "
                                       f"{len(srcs) - len(ok) + len(skipped)} skipped")
        return state


# ---------- per-topic cache ----------

def cache_dir(topic: str) -> Path:
    return config.ROOT / "cache" / topic / "research"


def save_cache(state: RunState) -> None:
    dst = cache_dir(state.topic["name"])
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(research_dir(state), dst, ignore=shutil.ignore_patterns(".from_cache"))


def load_cache(state: RunState) -> bool:
    src = cache_dir(state.topic["name"])
    if not (src / "research_state.json").exists():
        return False
    dst = research_dir(state)
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    (dst / ".from_cache").write_text(str(src))
    data = json.loads((dst / "research_state.json").read_text())
    state.sources, state.research, state.plan = data["sources"], data["research"], data.get("plan")
    stage_event(state, "research", f"using cached research from {src} ({len(state.sources)} sources); "
                                   "pass --fresh-research to redo")
    return True

