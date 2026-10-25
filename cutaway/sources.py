"""Discover the inputs in sources/<topic>/ and assign stable source ids.

  *.mp4|*.mov|*.webm      -> video   V1, V2 ...
  *.pdf                   -> paper   P1 ...
  *.png|*.jpg|*.jpeg|*.webp -> diagram D1 ...
  urls.txt (one per line, '#' comments, optional 'title | url'):
     youtube/youtu.be/vimeo or .mp4 -> video
     .pdf or arxiv/patents          -> paper (downloaded to sources/<topic>/_downloads/)
     anything else                  -> web page U1 ... (read with the URL context tool)
"""
from __future__ import annotations

import re
from pathlib import Path

import httpx

from .schemas import Source

VIDEO_EXT = {".mp4", ".mov", ".webm"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_URL = re.compile(r"(youtube\.com|youtu\.be|vimeo\.com)|\.(mp4|webm)(\?|$)", re.I)
PAPER_URL = re.compile(r"\.pdf(\?|$)|arxiv\.org/pdf|patents\.google\.com/.+\.pdf", re.I)


def discover(sources_dir: str | Path) -> list[Source]:
    d = Path(sources_dir)
    out: list[Source] = []
    counters = {"V": 0, "P": 0, "D": 0, "U": 0}

    def nid(prefix: str) -> str:
        counters[prefix] += 1
        return f"{prefix}{counters[prefix]}"

    if not d.exists():
        return out
    files = sorted(p for p in d.iterdir() if p.is_file() and not p.name.startswith("."))
    for p in files:
        ext = p.suffix.lower()
        if ext in VIDEO_EXT:
            out.append(Source(id=nid("V"), type="video", title=p.stem, url_or_file=str(p), local=True))
        elif ext == ".pdf":
            out.append(Source(id=nid("P"), type="paper", title=p.stem, url_or_file=str(p), local=True))
        elif ext in IMAGE_EXT:
            out.append(Source(id=nid("D"), type="diagram", title=p.stem, url_or_file=str(p), local=True))
    urls = d / "urls.txt"
    if urls.exists():
        for line in urls.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            title, url = (s.strip() for s in line.split("|", 1)) if "|" in line else (line, line)
            if VIDEO_URL.search(url):
                out.append(Source(id=nid("V"), type="video", title=title, url_or_file=url, local=False))
            elif PAPER_URL.search(url):
                out.append(Source(id=nid("P"), type="paper", title=title, url_or_file=url, local=False))
            else:
                out.append(Source(id=nid("U"), type="web", title=title, url_or_file=url, local=False))
    return out


def download_pdf(src: Source, sources_dir: str | Path) -> Path:
    """Fetch a remote PDF once into sources/<topic>/_downloads/<id>.pdf."""
    dl = Path(sources_dir) / "_downloads"
    dl.mkdir(parents=True, exist_ok=True)
    path = dl / f"{src.id}.pdf"
    if not path.exists():
        r = httpx.get(src.url_or_file, follow_redirects=True, timeout=120,
                      headers={"User-Agent": "Mozilla/5.0 (cutaway research)"})
        r.raise_for_status()
        path.write_bytes(r.content)
    return path
