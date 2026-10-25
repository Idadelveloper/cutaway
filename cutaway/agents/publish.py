"""Publish: copy the best version to final/ and append the proof section to its index.html.

Proof section: research stats, findings per version by layer, the 2-3 biggest fixes (before/after
clips side by side with the matching reference video moment), cited sources, resolved conflicts.
"""
from __future__ import annotations

import html
import re
import shutil
from pathlib import Path

from .. import scoring, stats
from ..schemas import Finding, RunState
from .base import Stage, run_dir, save_json, stage_event
from .review import _seconds

E = html.escape


def youtube_embed(url: str, sec: float | None) -> str | None:
    m = re.search(r"(?:v=|youtu\.be/|embed/)([\w-]{6,})", url)
    if not m:
        return None
    s = int(max(0, (sec or 0) - 5))
    return f"https://www.youtube.com/embed/{m.group(1)}?start={s}&end={s + 25}&rel=0"


def biggest_fixes(state: RunState, best: int, k: int = 3) -> list[dict]:
    """High-severity v1 findings (law/watched/interaction first) that are gone in the best version."""
    d = run_dir(state)
    v1 = scoring.load_findings(d / "findings" / "v1.json") if (d / "findings" / "v1.json").exists() else []
    vb = scoring.load_findings(d / "findings" / f"v{best}.json") if (d / "findings" / f"v{best}.json").exists() else []
    still = {(f.figure, f.what) for f in vb}
    order = {"law": 0, "watched": 1, "interaction": 2, "fidelity": 3, "contract": 4}
    gone = [f for f in v1 if f.severity == "high" and f.figure and (f.figure, f.what) not in still]
    gone.sort(key=lambda f: order.get(f.layer, 9))
    out, seen = [], set()
    for f in gone:
        if f.figure in seen:
            continue
        seen.add(f.figure)
        fig = next((x for x in state.spec.figures if x.id == f.figure), None)
        names = [c["name"] for c in (fig.controls if fig else [])]
        sweep = next((n for n in names if n in f"{f.time} {f.evidence}"), "default")
        out.append({"finding": f, "sweep": sweep})
        if len(out) == k:
            break
    return out


def proof_html(state: RunState, best: int, final: Path) -> str:
    d = run_dir(state)
    st = stats.research_stats(state)
    rows = scoring.version_table(d)
    spec = state.spec
    parts = ['<section id="proof" class="cutaway-proof">', "<h2>How this page was checked</h2>",
             "<p>This explainer was generated from the sources below, then rendered frame by frame, measured "
             "against the physical laws it teaches, watched next to the reference videos, and fixed in a loop. "
             "Here is the evidence.</p>"]

    # research
    by_type = ", ".join(f"{v} {E(k)}" for k, v in sorted(st["sources_by_type"].items(), key=lambda x: -x[1]))
    parts += ["<h3>Research</h3>", '<dl class="proof-stats">',
              f"<dt>Sources read</dt><dd>{st['sources_total']} ({by_type})</dd>",
              f"<dt>Tokens read</dt><dd>{st['tokens_read']:,}</dd>",
              f"<dt>Video watched</dt><dd>{st['video_hours']} h across {st['videos_watched']} videos</dd>",
              f"<dt>Citations verified</dt><dd>{st['citations_verified']} of {st['citations_total']} quotes found "
              f"verbatim in the stored source text</dd>",
              f"<dt>Conflicts resolved</dt><dd>{st['conflicts_resolved']}</dd>", "</dl>"]

    # findings per version
    layers = scoring.LAYERS
    parts += ["<h3>Findings per version</h3>", '<div class="proof-scroll"><table class="proof-table"><thead><tr><th>version</th>'
              + "".join(f"<th>{l}</th>" for l in layers) + "<th>high</th><th>weighted</th></tr></thead><tbody>"]
    for r in rows:
        mark = " (published)" if r["version"] == f"v{best}" else ""
        parts.append(f"<tr><td>{r['version']}{mark}</td>" +
                     "".join(f"<td>{sum(r['layers'][l].values())} <small>({r['layers'][l]['high']} high)</small></td>"
                             for l in layers) + f"<td>{r['high']}</td><td>{r['weighted']}</td></tr>")
    parts.append("</tbody></table></div><p class=\"proof-note\">law = measured physics (superposition, delay residual, "
                 "wave speed...); interaction = page behavior; fidelity = built values vs cited values; watched = "
                 "a model watching the rendered clips next to the reference videos. weighted = 5×high + 2×medium + 1×low.</p>")

    # biggest fixes
    fixes = biggest_fixes(state, best)
    if fixes and best != 1:
        pdir = final / "proof"
        pdir.mkdir(exist_ok=True)
        parts.append("<h3>Biggest fixes</h3>")
        vfs = {v.id: v for v in spec.video_facts}
        srcs = {s.get("id"): s for s in spec.sources} | {s["id"]: s for s in state.sources}
        for i, fx in enumerate(fixes):
            f: Finding = fx["finding"]
            name = f"{f.figure}_{fx['sweep']}.mp4"
            before, after = d / "clips" / "v1" / name, d / "clips" / f"v{best}" / name
            if not (before.exists() and after.exists()):
                continue
            shutil.copy(before, pdir / f"fix{i}_before.mp4")
            shutil.copy(after, pdir / f"fix{i}_after.mp4")
            fig = next((x for x in spec.figures if x.id == f.figure), None)
            ref = ""
            for vid in (fig.compare_with if fig else []):
                vf = vfs.get(vid)
                if vf and (emb := youtube_embed(srcs.get(vf.source, {}).get("url_or_file", ""), _seconds(vf.time))):
                    ref = (f'<div><iframe src="{E(emb)}" title="reference {E(vf.source)} {E(vf.time)}" loading="lazy" '
                           f'allowfullscreen></iframe><p>Reference: [{E(vf.source)} {E(vf.time)}] {E(vf.text)}</p></div>')
                    break
            parts += [f'<div class="proof-fix"><h4>{E(f.figure)}: {E(f.what)}</h4>',
                      f"<p><b>Evidence (v1):</b> {E(f.evidence)}</p>",
                      '<div class="proof-clips">',
                      f'<div><video src="proof/fix{i}_before.mp4" muted loop autoplay playsinline></video><p>Before (v1)</p></div>',
                      f'<div><video src="proof/fix{i}_after.mp4" muted loop autoplay playsinline></video><p>After (v{best})</p></div>',
                      ref, "</div></div>"]

    # conflicts
    if spec.conflicts:
        parts += ["<h3>Conflicts resolved</h3>", "<ul>"]
        for c in spec.conflicts:
            parts.append(f"<li><b>{E(str(c.get('fact', '')))}</b>: {E('; '.join(map(str, c.get('values', []))))} "
                         f"→ chose <b>{E(str(c.get('chosen', '')))}</b> ({E(str(c.get('why', '')))})</li>")
        parts.append("</ul>")

    # sources
    ranks = {s["id"]: s.get("rank") for s in state.sources}
    parts += ["<h3>Cited sources</h3>", '<ol class="proof-sources">']
    for s in spec.sources:
        u = str(s.get("url_or_file", ""))
        link = f'<a href="{E(u)}">{E(u)}</a>' if u.startswith("http") else E(Path(u).name)
        parts.append(f"<li id=\"proof-src-{E(str(s.get('id')))}\"><b>[{E(str(s.get('id')))}]</b> {E(str(s.get('title', '')))} "
                     f"<small>({E(str(s.get('type', '')))}, rank {ranks.get(s.get('id'), '?')})</small><br>{link}</li>")
    parts += ["</ol>", "</section>"]
    return "\n".join(parts)


PROOF_CSS = """
<style>
.cutaway-proof{max-width:880px;margin:96px auto 64px;padding:0 20px;font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,sans-serif;color:#22252a;border-top:2px solid #22252a}
.cutaway-proof h2{margin-top:32px}.cutaway-proof h3{margin-top:40px}
.proof-stats{display:grid;grid-template-columns:max-content 1fr;gap:6px 20px}.proof-stats dt{font-weight:600}.proof-stats dd{margin:0}
.proof-scroll{overflow-x:auto}.proof-table{border-collapse:collapse;width:100%;font-size:14px}
.proof-table th,.proof-table td{border-bottom:1px solid #ddd;padding:6px 8px;text-align:left;white-space:nowrap}
.proof-note{font-size:13px;color:#555}
.proof-clips{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}
.proof-clips video,.proof-clips iframe{width:100%;aspect-ratio:16/10;border:1px solid #ddd;border-radius:6px;background:#fff}
.proof-clips p{font-size:13px;margin:4px 0 0}.proof-fix{margin:24px 0}.proof-sources li{margin:6px 0;word-break:break-word}
</style>
"""


class Publish(Stage):
    name = "publish"

    async def run(self, state: RunState) -> RunState:
        best = state.best_version or state.version
        d = run_dir(state)
        final = d / "final"
        if final.exists():
            shutil.rmtree(final)
        shutil.copytree(Path(state.versions[best]["dir"]), final)
        stage_event(state, "publish", f"v{best} -> final/ with proof section")
        index = final / "index.html"
        page = index.read_text()
        proof = PROOF_CSS + proof_html(state, best, final)
        page = page.replace("</body>", proof + "\n</body>", 1) if "</body>" in page else page + proof
        index.write_text(page)
        save_json(d / "published.json", {"version": best, "url": f"/runs/{state.run_id}/final/"})
        stage_event(state, "publish", f"published: /runs/{state.run_id}/final/")
        return state

