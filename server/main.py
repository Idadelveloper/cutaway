"""FastAPI app.

  (POST /runs, GET /runs/{id}/events and POST /runs/{id}/change are aliases of the /api routes)
  POST /api/runs                 start a run (topic + optional uploaded files / URLs) in the background
  GET  /api/runs                 list runs
  GET  /api/runs/{id}            run summary (stage, versions, findings, links)
  GET  /api/runs/{id}/events     progress as server-sent events (history, then live)
  POST /api/runs/{id}/change     change request -> Fixer -> render -> checks -> new version if checks pass
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
from cutaway.agents.root import run_pipeline
from cutaway.schemas import RunState

app = FastAPI(title="Cutaway")
config.RUNS_DIR.mkdir(exist_ok=True)
WEB = config.ROOT / "web"
_tasks: dict[str, asyncio.Task] = {}


def _state(run_id: str) -> RunState:
    p = config.RUNS_DIR / run_id / "state.json"
    if not p.exists():
        raise HTTPException(404, f"no run {run_id}")
    return RunState.model_validate_json(p.read_text())


async def _guard(run_id: str, coro):
    try:
        await coro
        events.emit(run_id, "done", msg="run finished")
    except Exception as e:  # surface failures to the UI
        events.emit(run_id, "error", msg=f"run failed: {e!r}"[:800])
        events.emit(run_id, "done", msg="run failed")
    finally:
        _tasks.pop(run_id, None)


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


@app.get("/api/runs")
def list_runs():
    out = []
    for d in sorted(config.RUNS_DIR.iterdir(), reverse=True):
        p = d / "state.json"
        if p.exists():
            s = json.loads(p.read_text())
            out.append({"run_id": d.name, "topic": s["topic"].get("name"), "stage": s.get("stage"),
                        "version": s.get("version"), "best": s.get("best_version"),
                        "published": (d / "final" / "index.html").exists(), "running": d.name in _tasks})
    return out


@app.get("/api/runs/{run_id}")
def run_summary(run_id: str):
    s = _state(run_id)
    d = config.RUNS_DIR / run_id
    return {"run_id": run_id, "stage": s.stage, "version": s.version, "best": s.best_version,
            "running": run_id in _tasks, "versions": s.versions,
            "table": scoring.version_table(d) if (d / "findings").exists() else [],
            "explainer": f"/runs/{run_id}/final/" if (d / "final" / "index.html").exists()
            else (f"/runs/{run_id}/v{s.version}/" if s.version else None),
            "figures": [f.id for f in s.spec.figures] if s.spec else []}


@app.get("/api/runs/{run_id}/events")
@app.get("/runs/{run_id}/events")
async def run_events(run_id: str):
    async def gen():
        q = events.subscribe(run_id)
        try:
            for ev in events.history(run_id):
                yield f"data: {json.dumps(ev, default=str)}\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(ev, default=str)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            events.unsubscribe(run_id, q)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


class ChangeRequest(BaseModel):
    text: str


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
    _tasks[run_id] = asyncio.create_task(_guard(run_id, apply_change(run_id, req.text)))
    return {"ok": True, "events": f"/api/runs/{run_id}/events"}


@app.get("/api/topics")
def topics():
    return sorted(p.parent.name for p in config.TOPICS_DIR.glob("*/topic.yaml"))


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


app.mount("/runs", StaticFiles(directory=config.RUNS_DIR, html=True), name="runs")
app.mount("/web", StaticFiles(directory=WEB), name="web")
