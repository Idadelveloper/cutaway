"""Shared Playwright browser setup. Headless by default: headed windows that are hidden or in the background
get requestAnimationFrame throttled on macOS, which stalls Playwright's waits. CUTAWAY_HEADED=1 shows the windows."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from playwright.async_api import async_playwright

VIEWPORT = {"width": 1280, "height": 900}


@asynccontextmanager
async def browser():
    headless = os.environ.get("CUTAWAY_HEADED") != "1"
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=headless, args=["--ignore-gpu-blocklist", "--enable-webgl"])
        try:
            yield b
        finally:
            await b.close()


async def open_page(b, url: str):
    """New page with console/pageerror capture. Returns (page, logs) where logs = list of dicts."""
    ctx = await b.new_context(viewport=VIEWPORT, device_scale_factor=1)
    page = await ctx.new_page()
    logs: list[dict] = []
    page.on("console", lambda m: logs.append({"type": m.type, "text": m.text}))
    page.on("pageerror", lambda e: logs.append({"type": "pageerror", "text": str(e)}))
    page.on("requestfailed", lambda r: logs.append({"type": "requestfailed", "text": f"{r.url} {r.failure}"}))
    await page.goto(url, wait_until="load", timeout=60_000)
    return page, logs
