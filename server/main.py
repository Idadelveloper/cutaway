"""FastAPI app.

  (POST /runs, GET /runs/{id}/events and POST /runs/{id}/change are aliases of the /api routes)
  POST /api/runs                 start a run (topic + optional uploaded files / URLs) in the background
  GET  /api/runs                 list runs
  GET  /api/runs/{id}            run summary (stage, versions, findings, links)
  GET  /api/runs/{id}/events     progress as server-sent events (history, then live)
  POST /api/runs/{id}/change     change request (quick: scoped to affected figures, ~2-3 min) -> checks
  GET  /api/runs/{id}/versions/N version detail: status, findings, before/after clips
  POST /api/runs/{id}/versions/N/approve|discard   a human publishes (or drops) a checked version
  /runs/...                      static: explainers, clips, traces
  /                              web UI (web/index.html)
"""
from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from cutaway import config, events, scoring
from cutaway.agents.base import save_state
from cutaway.agents.fixer import fix_version, render_and_review, version_findings
from cutaway.agents.pipeline import stages_for
from cutaway.agents.publish import Publish
from cutaway.agents.quick import quick_change
from cutaway.agents.root import run_pipeline
from cutaway.schemas import RunState

app = FastAPI(title="Cutaway")
config.RUNS_DIR.mkdir(exist_ok=True)
WEB = config.ROOT / "web"
_tasks: dict[str, asyncio.Task] = {}


def _state(run_id: str) -> RunState:
    p = config.RUNS_DIR / run_id / "state.json"
    if p.exists():
        return RunState.model_validate_json(p.read_text())
    if (config.RUNS_DIR / run_id / "events.jsonl").exists():   # just started: nothing saved yet
        try:
            return RunState(run_id=run_id, topic=config.load_topic(run_id.rsplit("-", 1)[-1]))
        except FileNotFoundError:
            pass
    raise HTTPException(404, f"no run {run_id}")


async def _guard(run_id: str, coro, what: str = "run"):
    """Run in the background; the lifecycle marks it running and ends with a complete/failed event.
    The task is dropped from _tasks before that event so the UI never sees a finished run as running."""
    try:
        async with events.lifecycle(run_id, what):
            try:
                await coro
            finally:
                _tasks.pop(run_id, None)
    except Exception:
        pass   # already reported as a failed "done" event


@app.post("/api/runs")
@app.post("/runs")
async def start_run(topic: str = Form("anc"), urls: str = Form(""), fresh_research: bool = Form(False),
                    files: list[UploadFile] = File(default=[])):
    if not (config.TOPICS_DIR / topic / "topic.yaml").exists():
        raise HTTPException(400, f"unknown topic {topic}")
    run_id = time.strftime(f"%Y%m%d-%H%M%S-{topic}")
    t = config.load_topic(topic)
    files = [f for f in files if f.filename]
    if files or urls.strip():  # per-run seeds: the topic's seeds + uploads + urls; forces fresh research
        seeds = config.RUNS_DIR / run_id / "seeds"
        if Path(t["sources_dir"]).exists():
            shutil.copytree(t["sources_dir"], seeds, ignore=shutil.ignore_patterns("_downloads"))
        seeds.mkdir(parents=True, exist_ok=True)
        for f in files:
            (seeds / Path(f.filename).name).write_bytes(await f.read())
        if urls.strip():
            with open(seeds / "urls.txt", "a") as fh:
                fh.write("\n" + urls.strip() + "\n")
        t["sources_dir"] = str(seeds)
        fresh_research = True
    state = RunState(run_id=run_id, topic=t)
    save_state(state)
    events.emit(run_id, "stage", stage="queued", msg=f"run {run_id} queued (topic {topic})")
    stages = stages_for(state, "publish", fresh_research)
    _tasks[run_id] = asyncio.create_task(_guard(run_id, run_pipeline(state, stages)))
    return {"run_id": run_id, "events": f"/api/runs/{run_id}/events"}


def _summary_numbers(run_id: str, s: RunState) -> dict:
    d = config.RUNS_DIR / run_id
    st = json.loads((d / "research_stats.json").read_text()) if (d / "research_stats.json").exists() else {}
    table = scoring.version_table(d) if (d / "findings").exists() else []
    best = s.best_version or s.version
    row = next((r for r in table if r["version"] == f"v{best}"), None)
    return {"sources": st.get("sources_total"), "citations_verified": st.get("citations_verified"),
            "citations_total": st.get("citations_total"), "video_hours": st.get("video_hours"),
            "versions": len(table), "first": table[0] if table else None, "best": row,
            "pending": sorted(n for n, v in s.versions.items() if v.get("status") == "ready")}


@app.get("/api/runs")
def list_runs():
    out = []
    for d in sorted(config.RUNS_DIR.iterdir(), reverse=True):
        p = d / "state.json"
        if not d.is_dir() or d.name.startswith("_") or "baseline" in d.name:
            continue
        if p.exists():
            s = json.loads(p.read_text())
        elif (d / "events.jsonl").exists():    # just started: no state saved yet
            name = d.name.rsplit("-", 1)[-1]
            try:
                s = {"topic": config.load_topic(name)}
            except FileNotFoundError:
                continue
        else:
            continue
        st = events.run_status(d.name)
        if d.name in _tasks:
            st["status"] = "running"
        pending = [n for n, v in (s.get("versions") or {}).items() if (v or {}).get("status") == "ready"]
        out.append({"run_id": d.name, "topic": s["topic"].get("name"), "title": s["topic"].get("title"),
                    "status": st["status"], "stage": st["current"], "started": st["started"],
                    "best": s.get("best_version") or s.get("version"),
                    "published": st["published"], "pending": len(pending)})
    return out


@app.get("/api/runs/{run_id}")
def run_summary(run_id: str):
    s = _state(run_id)
    d = config.RUNS_DIR / run_id
    st = events.run_status(run_id)
    if run_id in _tasks:
        st["status"] = "running"
    return {"run_id": run_id, "topic": s.topic.get("name"), "title": s.topic.get("title"),
            "field": s.topic.get("field"), **st, "version": s.version, "best": s.best_version,
            "running": st["status"] == "running", "versions": s.versions,
            "table": scoring.version_table(d) if (d / "findings").exists() else [],
            "numbers": _summary_numbers(run_id, s),
            "explainer_file": str((d / "final" / "index.html").resolve()) if (d / "final" / "index.html").exists() else None,
            "explainer": f"/runs/{run_id}/final/" if (d / "final" / "index.html").exists()
            else (f"/runs/{run_id}/v{s.version}/" if s.version else None),
            "figures": [{"id": f.id, "section": f.section} for f in s.spec.figures] if s.spec else []}


@app.get("/api/runs/{run_id}/events")
@app.get("/runs/{run_id}/events")
async def run_events(run_id: str):
    async def gen():
        """Tail runs/<id>/events.jsonl, so runs started from the CLI stream live too."""
        path = config.RUNS_DIR / run_id / "events.jsonl"
        sent, idle = 0, 0.0
        while True:
            lines = path.read_text().splitlines() if path.exists() else []
            for line in lines[sent:]:
                if line.strip():
                    yield f"data: {line}\n\n"
            if len(lines) > sent:
                sent, idle = len(lines), 0.0
            else:
                idle += 1.0
                if idle >= 15:
                    yield ": keepalive\n\n"
                    idle = 0.0
            await asyncio.sleep(1.0)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


class ChangeRequest(BaseModel):
    text: str
    mode: str = "quick"   # "quick": scoped to affected figures, needs approval; "full": all files, auto-publish if passing


async def apply_quick_change(run_id: str, text: str) -> None:
    await quick_change(_state(run_id), text)


async def apply_change(run_id: str, text: str) -> None:
    state = _state(run_id)
    base = state.best_version or state.version
    new = max(state.versions) + 1
    events.emit(run_id, "stage", stage="change", msg=f"change request on v{base} -> v{new}: {text}")
    await fix_version(state, base, new, [], requirement=text)
    state = await render_and_review(state, new)
    before, after = version_findings(state, base), version_findings(state, new)
    hb, ha = scoring.high_count(before), scoring.high_count(after)
    law_contract_high = [f for f in after if f.severity == "high" and f.layer in ("law", "contract")]
    accepted = ha <= hb and not law_contract_high
    state.versions[new]["change_request"] = text
    state.versions[new]["accepted"] = accepted
    if accepted:
        state.best_version = new
        state.version = new
        state = await Publish().run(state)
        msg = f"change accepted: v{new} published ({ha} high findings, was {hb})"
    else:
        state.version = base
        msg = (f"change rejected: v{new} has {ha} high findings (was {hb})"
               + (f", {len(law_contract_high)} law/contract" if law_contract_high else "") + f"; keeping v{base}")
    save_state(state)
    events.emit(run_id, "stage", stage="change", msg=msg, accepted=accepted, version=new)


@app.post("/api/runs/{run_id}/change")
@app.post("/runs/{run_id}/change")
async def change(run_id: str, req: ChangeRequest):
    s = _state(run_id)
    if run_id in _tasks:
        raise HTTPException(409, "run is busy")
    if not s.version:
        raise HTTPException(400, "nothing built yet")
    fn = apply_quick_change if req.mode == "quick" else apply_change
    _tasks[run_id] = asyncio.create_task(_guard(run_id, fn(run_id, req.text), what="change request"))
    return {"ok": True, "mode": req.mode, "events": f"/api/runs/{run_id}/events"}


@app.get("/api/runs/{run_id}/versions/{n}")
def version_detail(run_id: str, n: int):
    s = _state(run_id)
    v = s.versions.get(n)
    if v is None:
        raise HTTPException(404, f"no v{n}")
    base = v.get("from")
    status = v.get("status")
    if status is None and "accepted" in v:     # made by a full-mode change request (older format)
        status = ("published" if (s.best_version == n) else "approved") if v["accepted"] else "rejected"
    new_findings = v.get("new_findings")
    if new_findings is None:
        fp = config.RUNS_DIR / run_id / "findings" / f"v{n}.json"
        new_findings = [f.model_dump() for f in scoring.load_findings(fp)] if fp.exists() else []
    if not v.get("figures"):                   # full mode touched everything: show the figures it changed
        v = {**v, "figures": sorted({c.split("/")[-1][:-3] for c in v.get("changed", []) if c.startswith("figures/")})[:2]}
    clips = []
    for fig in v.get("figures", []):
        for p in sorted((config.RUNS_DIR / run_id / "clips" / f"v{n}").glob(f"{fig}_*.mp4")):
            clips.append({"figure": fig, "sweep": p.stem.split("_", 1)[1],
                          "before": f"/runs/{run_id}/clips/v{base}/{p.name}", "after": f"/runs/{run_id}/clips/v{n}/{p.name}"})
    return {"version": n, **{k: v.get(k) for k in ("from", "change_request", "figures", "changed", "seconds",
                                                    "high", "weighted")},
            "status": status, "new_findings": new_findings,
            "clips": clips, "page": f"/runs/{run_id}/v{n}/"}


@app.post("/api/runs/{run_id}/versions/{n}/approve")
async def approve(run_id: str, n: int):
    """A human approves a checked version; only then is it published."""
    s = _state(run_id)
    v = s.versions.get(n)
    if v is None or v.get("status") != "ready":
        raise HTTPException(400, f"v{n} is not ready for approval (status {v and v.get('status')})")
    if run_id in _tasks:
        raise HTTPException(409, "run is busy")
    v["status"] = "published"
    s.best_version = s.version = n
    s = await Publish().run(s)
    save_state(s)
    events.emit(run_id, "change", msg=f"v{n} approved and published", version=n, status="published")
    return {"ok": True, "explainer": f"/runs/{run_id}/final/"}


@app.post("/api/runs/{run_id}/versions/{n}/discard")
def discard(run_id: str, n: int):
    s = _state(run_id)
    v = s.versions.get(n)
    if v is None or v.get("status") not in ("ready", "rejected"):
        raise HTTPException(400, f"v{n} can't be discarded")
    v["status"] = "discarded"
    save_state(s)
    events.emit(run_id, "change", msg=f"v{n} discarded", version=n, status="discarded")
    return {"ok": True}


@app.get("/api/topics")
def topics():
    out = []
    for p in sorted(config.TOPICS_DIR.glob("*/topic.yaml")):
        t = config.load_topic(p.parent.name)
        cached = (config.ROOT / "cache" / t["name"] / "research" / "research_state.json").exists()
        out.append({"name": t["name"], "title": t.get("title"), "field": t.get("field", ""),
                    "for": t.get("for", []), "audience": " ".join(str(t.get("audience", "")).split()),
                    "figures": len(t.get("figure_hints", [])), "cached_research": cached})
    return out


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


app.mount("/runs", StaticFiles(directory=config.RUNS_DIR, html=True), name="runs")
app.mount("/web", StaticFiles(directory=WEB), name="web")
