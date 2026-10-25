"""Generic checks on rendered traces: NaN/Infinity, console errors, entity visibility,
color-linked words, frame time."""
from __future__ import annotations

import math
import re

import numpy as np

from .. import config
from ..schemas import Finding, Spec, Trace

FRAME_P95_MS = 50.0


class Ids:
    """Sequential finding ids per layer."""

    def __init__(self, prefix: str):
        self.prefix, self.n = prefix, 0

    def __call__(self) -> str:
        self.n += 1
        return f"{self.prefix}{self.n}"


def _nonfinite(obj, path="") -> list[str]:
    bad = []
    if isinstance(obj, bool) or obj is None:
        return bad
    if isinstance(obj, (int, float)):
        return [path] if not math.isfinite(obj) else []
    if isinstance(obj, str):
        return [path] if obj in ("NaN", "Infinity", "-Infinity") else []
    if isinstance(obj, dict):
        for k, v in obj.items():
            bad += _nonfinite(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            bad += _nonfinite(v, f"{path}[{i}]")
    return bad


def check_finite(traces: dict[tuple[str, str], Trace], ids: Ids) -> list[Finding]:
    out = []
    for (fig, sweep), tr in traces.items():
        for i, fr in enumerate(tr.frames):
            if fr is None:
                out.append(Finding(id=ids(), layer="interaction", severity="high", figure=fig, time=f"frame {i}",
                                   what="getState() returned nothing (NaN serializes as null)", evidence=f"{sweep}",
                                   fix_hint="return a full state object every frame"))
                break
            bad = _nonfinite({"probes": fr.get("probes", {}), "entities": fr.get("entities", {}), "t": fr.get("t")})
            # Playwright serializes NaN/Infinity as None inside objects; treat None probes as non-finite too
            bad += [f"probes.{k}" for k, v in (fr.get("probes") or {}).items() if v is None]
            if bad:
                out.append(Finding(id=ids(), layer="interaction", severity="high", figure=fig,
                                   time=f"{sweep} frame {i} (t={fr.get('t')})",
                                   what=f"non-finite values: {sorted(set(bad))[:6]}",
                                   evidence=f"traces {fig}_{sweep}.json frame {i}",
                                   fix_hint="guard divisions/logs in model.js; clamp inputs"))
                break
    return out


def check_console(console: list[dict], ids: Ids) -> list[Finding]:
    seen, out = set(), []
    for l in console:
        if l.get("type") not in ("error", "pageerror"):
            continue
        key = re.sub(r"\d+", "#", l["text"])[:200]
        if key in seen:
            continue
        seen.add(key)
        out.append(Finding(id=ids(), layer="interaction", severity="high", figure=None, time=None,
                           what=f"console {l['type']}", evidence=l["text"][:500], fix_hint="fix the script error"))
    return out


def check_visibility(spec: Spec, traces: dict[tuple[str, str], Trace], ids: Ids) -> list[Finding]:
    out = []
    for f in spec.figures:
        tr = traces.get((f.id, "default"))
        if tr is None:
            out.append(Finding(id=ids(), layer="interaction", severity="high", figure=f.id, time=None,
                               what="figure was not rendered (no default trace)", evidence="",
                               fix_hint="make the figure render through the contract API"))
            continue
        for eid in f.entities:
            states = [(fr.get("entities") or {}).get(eid) for fr in tr.frames if fr]
            if not any(states):
                out.append(Finding(id=ids(), layer="interaction", severity="high", figure=f.id, time=None,
                                   what=f"entity '{eid}' missing from getState()", evidence=f"{f.id}_default",
                                   fix_hint="draw and report every entity the figure discusses"))
            elif not any(s and s.get("visible") for s in states):
                out.append(Finding(id=ids(), layer="interaction", severity="high", figure=f.id, time="0-4s",
                                   what=f"entity '{eid}' is never visible at default controls",
                                   evidence=f"{f.id}_default: visible=false in all {len(states)} frames",
                                   fix_hint="make the discussed entity visible (not hidden by the shell)"))
    return out


def _rgb(s: str) -> tuple[int, int, int] | None:
    m = re.match(r"rgba?\((\d+),\s*(\d+),\s*(\d+)", s or "")
    if m:
        return tuple(int(x) for x in m.groups())
    m = re.match(r"#([0-9a-f]{6})", s or "", re.I)
    return tuple(int(m.group(1)[i:i + 2], 16) for i in (0, 2, 4)) if m else None


def check_color_words(spec: Spec, words: list[dict], ids: Ids) -> list[Finding]:
    out = []
    ents = {e.id: e for e in spec.entities}
    figs = {f.id for f in spec.figures}
    for w in words:
        e = ents.get(w["entity"])
        if e is None:
            out.append(Finding(id=ids(), layer="interaction", severity="medium", figure=w.get("figure"), time=None,
                               what=f"colored word '{w['text']}' links to unknown entity '{w['entity']}'",
                               evidence=str(w), fix_hint="use a spec entity id"))
            continue
        if w.get("figure") and w["figure"] not in figs:
            out.append(Finding(id=ids(), layer="interaction", severity="low", figure=w["figure"], time=None,
                               what=f"colored word '{w['text']}' links to unknown figure", evidence=str(w),
                               fix_hint="use a spec figure id"))
        want, got = _rgb(config.PALETTE.get(e.color, "")), _rgb(w.get("color", ""))
        if want and got and max(abs(a - b) for a, b in zip(want, got)) > 24:
            out.append(Finding(id=ids(), layer="interaction", severity="medium", figure=w.get("figure"), time=None,
                               what=f"colored word '{w['text']}' is {w['color']}, entity '{e.id}' is {e.color} "
                                    f"({config.PALETTE[e.color]})", evidence=str(w),
                               fix_hint="color entity words with the entity's palette color"))
    return out


def check_frame_time(traces: dict[tuple[str, str], Trace], ids: Ids) -> list[Finding]:
    out = []
    for (fig, sweep), tr in traces.items():
        if not tr.frame_ms:
            continue
        p95 = float(np.percentile(tr.frame_ms, 95))
        if p95 > FRAME_P95_MS:
            out.append(Finding(id=ids(), layer="interaction", severity="medium", figure=fig, time=None,
                               what=f"slow frames: p95 {p95:.0f} ms > {FRAME_P95_MS:.0f} ms",
                               evidence=f"{fig}_{sweep}: max {max(tr.frame_ms):.0f} ms",
                               fix_hint="reuse geometry/materials; avoid per-frame allocation"))
    return out
