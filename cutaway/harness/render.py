"""Render harness: drive each figure through the contract API frame by frame.

Per figure (several figures in parallel, one page each):
  default sweep: reset, advance(1/30) x FRAMES at default controls
  per control:   reset, sweep the control min -> max over FRAMES frames
Every frame: screenshot of the figure element + getState() + time spent in advance().
Outputs (per version N): traces/vN/<fig>_<sweep>.json, clips/vN/<fig>_<sweep>.mp4,
keyframes/vN/<fig>_<sweep>_{first,mid,last}.jpg, render/vN/{page.png, scroll.mp4, console.json, timing.json}.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path

import numpy as np

from .. import events
from ..schemas import Spec, Trace
from .browser import browser, open_page
from .serve import serve

FRAMES = 120
DT = 1 / 30
PARALLEL_PAGES = 4
SCROLL_FRAMES = 150

STEP_JS = """
([id, ctl, value, dt]) => {
  const f = window.__cutaway.figure(id);
  if (ctl !== null) f.set(ctl, value);
  const t0 = performance.now();
  f.advance(dt);
  const ms = performance.now() - t0;
  return {ms, state: f.getState()};
}
"""


async def ffmpeg(frames_dir: Path, out: Path, fps: int = 30) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps), "-i", str(frames_dir / "%04d.jpg"),
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-c:v", "libx264", "-preset", "veryfast",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed for {out}: {err.decode()[:500]}")


def sweeps_for(fig) -> list[tuple[str, dict | None]]:
    return [("default", None)] + [(c["name"], c) for c in fig.controls]


async def render_figure(page, fig, out: dict[str, Path], logs: list[dict], run_id: str | None) -> list[dict]:
    el = page.locator(f'[data-figure="{fig.id}"]').first
    await el.scroll_into_view_if_needed()
    await page.evaluate("window.__cutaway.pauseAll()")
    await page.wait_for_timeout(100)
    results = []
    for sweep, ctl in sweeps_for(fig):
        name = f"{fig.id}_{sweep}"
        fdir = out["frames"] / name
        if fdir.exists():
            shutil.rmtree(fdir)
        fdir.mkdir(parents=True)
        log_start = len(logs)
        await page.evaluate(f"window.__cutaway.figure({json.dumps(fig.id)}).reset()")
        await el.scroll_into_view_if_needed()
        box = await el.bounding_box()
        frames, ms = [], []
        for i in range(FRAMES):
            value = None
            if ctl is not None:
                value = ctl["min"] + (ctl["max"] - ctl["min"]) * i / (FRAMES - 1)
            r = await page.evaluate(STEP_JS, [fig.id, ctl["name"] if ctl else None, value, DT])
            frames.append(r["state"])
            ms.append(r["ms"])
            await page.screenshot(path=str(fdir / f"{i:04d}.jpg"), type="jpeg", quality=80,
                                  clip=box if box else None)
        trace = Trace(figure=fig.id, sweep=sweep, frames=frames, frame_ms=ms,
                      console=[f"{l['type']}: {l['text']}" for l in logs[log_start:]])
        (out["traces"] / f"{name}.json").write_text(trace.model_dump_json())
        for tag, idx in (("first", 0), ("mid", FRAMES // 2), ("last", FRAMES - 1)):
            shutil.copy(fdir / f"{idx:04d}.jpg", out["keyframes"] / f"{name}_{tag}.jpg")
        results.append({"figure": fig.id, "sweep": sweep, "frames_dir": fdir, "clip": out["clips"] / f"{name}.mp4",
                        "p95_ms": float(np.percentile(ms, 95)), "max_ms": float(max(ms))})
        events.emit(run_id, "info", stage="render", msg=f"render {name}: {FRAMES} frames, p95 {results[-1]['p95_ms']:.1f} ms")
    return results


ENT_WORDS_JS = """
() => [...document.querySelectorAll('[data-entity]')].filter(e => !e.closest('figure')).map(e => ({
  entity: e.dataset.entity, figure: e.dataset.figure || null, text: e.textContent.trim().slice(0, 60),
  color: getComputedStyle(e).color }))
"""


async def scroll_capture(page, out: dict[str, Path]) -> None:
    words = await page.evaluate(ENT_WORDS_JS)
    (out["render"] / "ent_words.json").write_text(json.dumps(words, indent=1))
    await page.evaluate("window.scrollTo(0, 0); window.__cutaway.playAll()")
    await page.screenshot(path=str(out["render"] / "page.png"), full_page=True)
    height = await page.evaluate("document.documentElement.scrollHeight - window.innerHeight")
    fdir = out["frames"] / "_scroll"
    if fdir.exists():
        shutil.rmtree(fdir)
    fdir.mkdir(parents=True)
    for i in range(SCROLL_FRAMES):
        await page.evaluate(f"window.scrollTo(0, {height * i / (SCROLL_FRAMES - 1)})")
        await page.wait_for_timeout(30)
        await page.screenshot(path=str(fdir / f"{i:04d}.jpg"), type="jpeg", quality=75)
    await ffmpeg(fdir, out["render"] / "scroll.mp4", fps=30)
    shutil.rmtree(fdir)


async def render_version(version_dir: Path, run_dir: Path, version: int, spec: Spec,
                         run_id: str | None = None, keep_frames: bool = False) -> dict:
    v = f"v{version}"
    out = {k: run_dir / k / v for k in ("traces", "clips", "keyframes", "render", "frames")}
    for p in out.values():
        p.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    all_logs: dict[str, list] = {}
    results: list[dict] = []
    queue: asyncio.Queue = asyncio.Queue()
    for f in spec.figures:
        queue.put_nowait(f)

    with serve(version_dir) as base:
        async with browser() as b:
            async def worker(k: int):
                page, logs = await open_page(b, base + "/index.html")
                await page.wait_for_function("!!window.__cutaway", timeout=15_000)
                all_logs[f"page{k}"] = logs
                while not queue.empty():
                    fig = queue.get_nowait()
                    try:
                        results.extend(await render_figure(page, fig, out, logs, run_id))
                    except Exception as e:
                        events.emit(run_id, "error", stage="render", msg=f"render {fig.id} failed: {e!r}"[:500])
                        results.append({"figure": fig.id, "sweep": "default", "error": repr(e)[:500]})
                await page.close()

            n = min(PARALLEL_PAGES, len(spec.figures))
            await asyncio.gather(*(worker(k) for k in range(n)))
            page, logs = await open_page(b, base + "/index.html")
            await page.wait_for_function("!!window.__cutaway", timeout=15_000)
            await scroll_capture(page, out)
            all_logs["scroll"] = logs
        t_capture = time.time() - t0

    ok = [r for r in results if "error" not in r]
    sem = asyncio.Semaphore(6)

    async def stitch(r):
        async with sem:
            await ffmpeg(r["frames_dir"], r["clip"])
            if not keep_frames:
                shutil.rmtree(r["frames_dir"], ignore_errors=True)

    await asyncio.gather(*(stitch(r) for r in ok))
    console = [{"page": k, **l} for k, ls in all_logs.items() for l in ls]
    (out["render"] / "console.json").write_text(json.dumps(console, indent=1))
    timing = {
        "figures": len(spec.figures), "sweeps": len(ok), "frames": len(ok) * FRAMES,
        "capture_s": round(t_capture, 1), "total_s": round(time.time() - t0, 1),
        "per_sweep": [{k: r[k] for k in ("figure", "sweep", "p95_ms", "max_ms")} for r in ok],
        "errors": [r for r in results if "error" in r],
    }
    (out["render"] / "timing.json").write_text(json.dumps(timing, indent=1))
    if not keep_frames:
        shutil.rmtree(out["frames"], ignore_errors=True)
    return timing
