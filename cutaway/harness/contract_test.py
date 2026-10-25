"""Fast contract test: page loads cleanly and window.__cutaway matches the spec."""
from __future__ import annotations

import json
from pathlib import Path

from ..schemas import Finding, Spec
from .browser import browser, open_page
from .serve import serve

# Runs in the page: inspects every spec figure through the contract API only.
PROBE_JS = """
(spec) => {
  const out = {has_api: !!window.__cutaway, figures_listed: null, figures: {}};
  if (!window.__cutaway) return out;
  const api = window.__cutaway;
  out.figures_listed = Array.isArray(api.figures) ? api.figures : null;
  out.has_pause = typeof api.pauseAll === 'function' && typeof api.playAll === 'function';
  try { api.pauseAll && api.pauseAll(); } catch (e) { out.pause_error = String(e); }
  const finite = (v) => Array.isArray(v) ? v.every(x => typeof x === 'number' && isFinite(x))
                                         : (typeof v === 'number' && isFinite(v));
  for (const f of spec.figures) {
    const r = {errors: []};
    out.figures[f.id] = r;
    let fig;
    try { fig = api.figure(f.id); } catch (e) { r.errors.push('figure() threw: ' + e); continue; }
    if (!fig) { r.errors.push('figure() returned nothing'); continue; }
    r.methods = ['set','reset','advance','getState','highlight'].filter(m => typeof fig[m] !== 'function');
    r.controls = fig.controls || null;
    r.has_element = !!document.querySelector(`[data-figure="${f.id}"]`);
    try {
      const el = document.querySelector(`[data-figure="${f.id}"]`);
      if (el) el.scrollIntoView({block: 'center'});
      fig.reset();
      fig.advance(1/30);
      const s = fig.getState();
      r.state_keys = Object.keys(s || {});
      r.entities = Object.keys((s && s.entities) || {});
      r.entity_colors = Object.fromEntries(Object.entries((s && s.entities) || {}).map(([k, v]) => [k, v && v.color]));
      r.probes = Object.keys((s && s.probes) || {});
      r.nonfinite = Object.entries((s && s.probes) || {}).filter(([k, v]) => !finite(v)).map(([k]) => k);
      r.t = s && s.t;
    } catch (e) { r.errors.push('reset/advance/getState threw: ' + e); }
    try { fig.highlight(f.entities[0] || null); fig.highlight(null); }
    catch (e) { r.errors.push('highlight threw: ' + e); }
  }
  return out;
}
"""


def _f(n: int, sev: str, fig: str | None, what: str, evidence: str, hint: str) -> Finding:
    return Finding(id=f"K{n}", layer="contract", severity=sev, figure=fig, what=what, evidence=evidence, fix_hint=hint)


def analyze(report: dict, logs: list[dict], spec: Spec) -> list[Finding]:
    fs: list[Finding] = []
    add = lambda *a: fs.append(_f(len(fs) + 1, *a))  # noqa: E731
    errors = [l for l in logs if l["type"] in ("error", "pageerror", "requestfailed")]
    for l in errors[:20]:
        add("high", None, f"console {l['type']}", l["text"][:500], "fix the script error")
    if not report.get("has_api"):
        add("high", None, "window.__cutaway missing after load", json.dumps(report)[:300],
            "contract.js must define window.__cutaway before the load event")
        return fs
    want = [f.id for f in spec.figures]
    if report.get("figures_listed") != want:
        add("high", None, "__cutaway.figures does not match spec", f"got {report.get('figures_listed')}, want {want}",
            "list exactly the spec figure ids in order")
    if not report.get("has_pause"):
        add("medium", None, "pauseAll/playAll missing", "", "add pauseAll() and playAll()")
    for f in spec.figures:
        r = report["figures"].get(f.id, {})
        for e in r.get("errors", []):
            add("high", f.id, e, "", "make the contract methods work for this figure")
        if r.get("methods"):
            add("high", f.id, f"missing methods {r['methods']}", "", "implement every contract method")
        if r.get("has_element") is False:
            add("medium", f.id, "no element with data-figure", "", f'add <figure id="fig-{f.id}" data-figure="{f.id}">')
        got = r.get("controls") or {}
        for c in f.controls:
            g = got.get(c["name"])
            if g is None:
                add("high", f.id, f"control '{c['name']}' not listed", f"listed: {sorted(got)}", "expose every spec control")
                continue
            for k in ("min", "max", "default"):
                if k in c and g.get(k) != c[k]:
                    add("medium", f.id, f"control '{c['name']}' {k} = {g.get(k)}, spec says {c[k]}", "", "use spec values")
        extra = set(got) - {c["name"] for c in f.controls}
        if extra:
            add("low", f.id, f"controls not in spec: {sorted(extra)}", "", "remove or rename to spec names")
        if "errors" in r and not r.get("errors"):
            ents = set(r.get("entities", []))
            for eid in f.entities:
                if eid not in ents:
                    add("high", f.id, f"entity '{eid}' missing from getState().entities", f"got {sorted(ents)}",
                        "report every spec entity of this figure")
            colors = {e.id: e.color for e in spec.entities}
            for eid, col in (r.get("entity_colors") or {}).items():
                if eid in colors and col != colors[eid]:
                    add("medium", f.id, f"entity '{eid}' color '{col}', spec says '{colors[eid]}'", "", "use spec color name")
            probes = set(r.get("probes", []))
            for p in f.probes:
                if p not in probes:
                    add("high", f.id, f"probe '{p}' missing from getState().probes", f"got {sorted(probes)}",
                        "compute this probe in model.js")
            if r.get("nonfinite"):
                add("high", f.id, f"non-finite probes {r['nonfinite']}", "", "fix NaN/Infinity in model.js")
    return fs


async def contract_test(version_dir: Path, spec: Spec, shot: Path | None = None) -> tuple[list[Finding], dict]:
    with serve(version_dir) as base:
        async with browser() as b:
            page, logs = await open_page(b, base + "/index.html")
            try:
                await page.wait_for_function("!!window.__cutaway", timeout=10_000)
            except Exception:
                pass
            report = await page.evaluate(PROBE_JS, spec.model_dump(mode="json"))
            if shot:
                shot.parent.mkdir(parents=True, exist_ok=True)
                await page.evaluate("window.scrollTo(0, 0)")
                await page.screenshot(path=str(shot), full_page=True)
    return analyze(report, logs, spec), {"report": report, "logs": logs}
