"""Fetch a source's full text (PDF via pypdf, HTML via trafilatura); detect paywalls/blocks.

Real fetches are saved to fixtures/fetch/<sha>.json; with CUTAWAY_FAKE=1 only those are used
(exact URL match, never the network).
"""
from __future__ import annotations

import hashlib
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path

import httpx

from . import config

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
MIN_CHARS = 800
PAYWALL = re.compile(
    r"(subscribe to (continue|read)|purchase (this|the) article|access denied|are you a robot|captcha|"
    r"log ?in to (continue|view)|institutional access|buy this article|enable javascript and cookies)", re.I)


@dataclass
class Fetched:
    ok: bool
    text: str = ""
    reason: str | None = None
    title: str | None = None


def _fixture(url: str) -> Path:
    return config.FIXTURES_DIR / "fetch" / f"{hashlib.sha256(url.encode()).hexdigest()[:16]}.json"


def pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    pages = []
    for i, page in enumerate(reader.pages):
        try:
            pages.append(f"[p.{i + 1}]\n" + (page.extract_text() or ""))
        except Exception:  # broken page: keep going
            pages.append(f"[p.{i + 1}]\n")
    return "\n\n".join(pages)


def html_text(html: str, url: str) -> tuple[str, str | None]:
    import trafilatura
    text = trafilatura.extract(html, url=url, include_tables=True, include_comments=False, favor_recall=True) or ""
    meta = trafilatura.extract_metadata(html)
    return text, (meta.title if meta else None)


def local_text(path: str | Path) -> str:
    path = Path(path)
    return pdf_text(path.read_bytes()) if path.suffix.lower() == ".pdf" else path.read_text(errors="ignore")


def _extract(r: httpx.Response, url: str) -> Fetched:
    title = None
    try:
        if "pdf" in r.headers.get("content-type", "") or r.content[:5] == b"%PDF-":
            text = pdf_text(r.content)
        else:
            text, title = html_text(r.text, url)
    except Exception as e:
        return Fetched(ok=False, reason=f"extract failed: {type(e).__name__}")
    if not text:
        return Fetched(ok=False, reason="no extractable text")
    if len(text) < MIN_CHARS:
        return Fetched(ok=False, reason=f"too short ({len(text)} chars): likely paywalled or JS-only")
    if len(text) < 5000 and PAYWALL.search(text):
        return Fetched(ok=False, reason="paywall/login page")
    return Fetched(ok=True, text=text, title=title)


async def fetch(url: str) -> Fetched:
    fx = _fixture(url)
    if config.FAKE:
        if fx.exists():
            return Fetched(**json.loads(fx.read_text()))
        return Fetched(ok=False, reason="fake mode: no fetch fixture")
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=60, headers={"User-Agent": UA}) as c:
            r = await c.get(url)
    except Exception as e:
        res = Fetched(ok=False, reason=f"network: {type(e).__name__}")
    else:
        if r.status_code in (401, 402, 403, 407, 429, 451):
            res = Fetched(ok=False, reason=f"blocked: HTTP {r.status_code}")
        elif r.status_code >= 400:
            res = Fetched(ok=False, reason=f"HTTP {r.status_code}")
        else:
            res = _extract(r, url)
    fx.parent.mkdir(parents=True, exist_ok=True)
    fx.write_text(json.dumps(res.__dict__))
    return res
