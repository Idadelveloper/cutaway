"""Citation check layer: every quote must appear in the stored source text.

Match after normalizing unicode, case and whitespace. If that fails, a second pass also
ignores punctuation (PDF extraction mangles hyphens, ligatures and quotes); such matches
count as verified but are reported with method "loose".
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from ..schemas import Finding, Spec

MAX_QUOTE_WORDS = 20
_TRANS = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
                        "­": None, "−": "-"})


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).translate(_TRANS).lower()
    return re.sub(r"\s+", " ", s).strip()


def loose(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", normalize(s))


class SourceTexts:
    """Normalized source texts, loaded lazily from research/sources/<id>.txt."""

    def __init__(self, texts_dir: Path):
        self.dir = Path(texts_dir)
        self._norm: dict[str, tuple[str, str] | None] = {}

    def get(self, sid: str) -> tuple[str, str] | None:
        if sid not in self._norm:
            p = self.dir / f"{sid}.txt"
            self._norm[sid] = (normalize(p.read_text(errors="ignore")), loose(p.read_text(errors="ignore"))) \
                if p.exists() else None
        return self._norm[sid]


def iter_citations(spec: Spec):
    """Yield (kind, object id, figure id or None, Citation)."""
    for e in spec.entities:
        for c in e.cite:
            yield "entity", e.id, None, c
    for l in spec.laws:
        for c in l.cite:
            yield "law", l.id, None, c
    for f in spec.figures:
        for c in f.cite:
            yield "figure", f.id, f.id, c


def check_citations(spec: Spec, texts_dir: Path) -> tuple[list[Finding], dict]:
    texts = SourceTexts(texts_dir)
    findings: list[Finding] = []
    stats = {"total": 0, "verified": 0, "verified_loose": 0, "missing": 0, "too_long": 0, "no_text": 0}
    for n, (kind, oid, fig, c) in enumerate(iter_citations(spec), 1):
        stats["total"] += 1
        words = len(c.quote.split())
        if words > MAX_QUOTE_WORDS:
            stats["too_long"] += 1
            findings.append(Finding(
                id=f"CIT{n}", layer="citation", severity="medium", figure=fig,
                what=f"{kind} {oid}: quote from {c.source} is {words} words (max {MAX_QUOTE_WORDS})",
                evidence=c.quote[:300], fix_hint="shorten to the key verbatim phrase"))
        t = texts.get(c.source)
        q = normalize(c.quote)
        if t is None:
            stats["no_text"] += 1
            stats["missing"] += 1
            findings.append(Finding(
                id=f"CIT{n}", layer="citation", severity="high", figure=fig,
                what=f"{kind} {oid}: cites {c.source}, which has no stored text",
                evidence=f"{c.source} {c.where}: \"{c.quote}\"", fix_hint="cite a source with stored text"))
        elif q and q in t[0]:
            stats["verified"] += 1
        elif q and len(loose(c.quote)) >= 12 and loose(c.quote) in t[1]:
            stats["verified"] += 1
            stats["verified_loose"] += 1
        else:
            stats["missing"] += 1
            findings.append(Finding(
                id=f"CIT{n}", layer="citation", severity="high", figure=fig,
                what=f"{kind} {oid}: quote not found in {c.source}",
                evidence=f"{c.source} {c.where}: \"{c.quote}\"",
                fix_hint="replace with a verbatim snippet copied from the source text, or drop the fact"))
    return findings, stats
