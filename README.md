# Cutaway

Cutaway researches a concept from real sources (papers, patents, manufacturer docs, articles and long videos), writes a cited spec, and generates a complete interactive 3D explainer in one pass. It then **renders the explainer, measures it, watches it against the reference sources, and fixes it in a loop** until the checks pass.

**Who it's for:**
- **Students**, who learn a hard concept by playing with it: dragging a slider and watching the physiology, physics or algorithm respond.
- **Teachers** preparing a lesson, who need an interactive explainer that is actually correct.
- **Hardware documentation teams**, who need an accurate, cited "how it works" page for a product.

All of them need more than a plausible-looking animation: they need evidence that the behavior, the numbers and the pictures match the sources. Cutaway publishes that evidence with every explainer.

## Topics

| Topic | Field | What the law checks measure on every rendered frame |
|---|---|---|
| `anc`: how noise-cancelling headphones work | physics / engineering | wave speed (f·λ = 343 m/s), superposition, cancellation at zero delay, residual = 2·\|sin(πfτ)\| with delay, phase flip, particles oscillating without drift |
| `heart`: how the heart pumps blood | anatomy and physiology | valves open only when upstream pressure is higher; mitral and aortic valves never open together; constant volume when both are closed; filling and ejection flow the right way; SV = EDV − ESV and CO = HR × SV; cycle period = 60 / HR |
| `bst`: how binary search trees work | computer science | the BST property and sorted in-order traversal; height and node count match the structure; a search follows the comparison path; AVL balance when balancing is on |

Every topic also checks that each control changes something measurable, plus the generic checks (NaN, console errors, visibility, colored words, frame time) and the fidelity and watched reviews.

**Adding a topic:** create `topics/<name>/topic.yaml` with:
- the brief, audience and outline;
- the hero line and palette;
- the probes, model notes and spec notes;
- the research focus and the SourceScout checks;
- the `law_checks` it uses.

If it needs new kinds of laws, add a module in `cutaway/checks/` that registers `CHECKS = {name: fn}` and include it in `checks/registry.py`. Seed sources in `sources/<name>/` are optional. Then run `make all TOPIC=<name>`.

Built at the Gemini 4 Argon SF hackathon (problem statement Two: "More Dimensions").

## How it works

```
Research (3 discoverers ∥) → SpecWriter → Builder → Render → Review (checks ∥ fidelity ∥ watched) → Fix loop → Publish
```

1. **Research** (cached per topic in `cache/<topic>/research/`)
   - **Three discoverers run in parallel:**
     - Gemini 4 Argon with the Google Search and URL context tools.
     - The Deep Research agent writes a cited report, and Flash extracts its sources.
     - SourceScout, an Antigravity managed agent on Argon, downloads open PDFs and pages in its sandbox, extracts their text, and checks the key equations numerically.
   - Files in `sources/<topic>/` are optional seeds.
   - Sources are deduped by URL and ranked: paper / patent / standard / manufacturer, then article / video, then blog. Up to 50 are kept, including at most 5 videos.
   - The full text of every source is saved to `research/sources/<id>.txt`, with an index in `sources.json`. Paywalled or blocked pages are skipped and logged.
   - VideoAnalyst (Argon, high thinking) watches each video link and returns a transcript plus timestamped facts and motion moments.
2. **Spec**: one background Argon call (high thinking) reads every source text and the video facts, then writes `spec.json`.
   - Every entity, law and figure carries a citation with a **verbatim quote of 20 words or fewer**.
   - Code checks that each quote appears in the stored source text. A missing quote is a high-severity finding, and SpecWriter gets one repair call.
   - Conflicts between sources are listed along with the value chosen.
3. **Build**: one long background call (`max_output_tokens` 262144) generates the whole explainer: HTML, CSS, a shared physics model (`model.js`), one Three.js file per figure, and `contract.js`.
   - A Playwright contract test (`window.__cutaway`) runs straight away. Failures go back to Argon for up to 2 contract-fix rounds.
4. **Render**: Playwright steps each figure for 120 frames at its default settings, then sweeps each control from min to max.
   - Every frame is screenshotted, and its `getState()` is saved to `traces/`. ffmpeg stitches the frames into `clips/`.
   - Also captured: a full-page screenshot, a scroll-through clip, console logs and frame timing.
5. **Review**, all in parallel:
   - **Law checks** on the traces: wave speed, superposition (sum and cancellation), phase flip, delay residual 2|sin(πfτ)|, particles oscillating without drift, and controls changing the physics.
   - **Interaction checks**: NaN or Infinity in state, console errors, entity visibility, color-linked words, frame time.
   - **FidelityChecker** (Flash): the values the page actually uses vs the cited values.
   - **VisualReviewer** (Argon, high): watches each figure's clips next to the matching reference video segments and answers a fixed 5-question rubric.
6. **Fix loop**: Triage (Argon, medium) dedupes and ranks the findings, then the Fixer (Argon, high, long output) writes `v<N+1>`, which is re-rendered and re-checked.
   - The loop stops at zero high-severity findings or after 3 rounds.
   - If a version scores worse, the loop reverts to the best version and retries once with the regression as feedback.
7. **Publish**: the best version is copied to `final/` with a **proof section** appended. It contains:
   - research stats;
   - findings per version by layer;
   - the biggest fixes as before/after clips beside the matching reference video moment;
   - the resolved conflicts and every cited source.

The stages run as an ADK workflow (`cutaway/agents/root.py`).

## Setup

Requires Python 3.12, `ffmpeg`, and a Gemini API key.

```bash
uv venv .venv && source .venv/bin/activate
uv pip install -r requirements.txt
playwright install chromium
echo "GEMINI_API_KEY=your-key" > .env
```

Seed sources are optional: put PDFs, images, MP4s or a `urls.txt` (`title | url` per line) in `sources/<topic>/`.

## Commands

```
make smoke           # API smoke test (Argon, Flash, background long-output call)
make research        # research + cited spec (reuses cache/<topic>/research)
make research-fresh  # redo research, ignoring the cache
make build           # through v1 (+ contract test)
make render          # through render + review of the current version
make loop            # full run with the fix loop
make all             # full run + publish (proof section)
make serve           # FastAPI on :8000 (web UI at /)
make baselines       # prompt-only (A) and no-loop (B) baselines vs the full run
make score RUN=<id>  # findings per version by layer
make test            # unit tests (checks, citations, contract, render, full fake pipeline)
```

Add `ARGS="--run-id <id>"` to resume an existing run; finished stages are skipped.

When a run finishes, the published explainer opens in your browser automatically. From the CLI it opens by its absolute path (`file://…/runs/<id>/final/index.html`; pass `--no-open` to skip). In the web UI, the explainer opens in a new tab when the run you're watching completes, and when you approve a change. If the browser blocks the tab, a banner shows an Open button and the file path.

**Fake mode:** `CUTAWAY_FAKE=1 make all` replays saved model responses from `fixtures/` instead of calling the API. It's fast and free, which makes it useful for testing the pipeline. Each real call that succeeds is saved as a fixture, and so is each fetched page. `fixtures/` and `cache/` are git-ignored, because they contain full third-party texts. The unit tests don't need them: they write their own made-up fixtures to a temp directory.

## Server

`make serve`, then open http://localhost:8000.

| Endpoint | What it does |
|---|---|
| `POST /runs` (or `/api/runs`) | Start a run: topic, plus optional uploaded files and URLs |
| `GET /runs/<id>/events` | Live progress as server-sent events |
| `POST /runs/<id>/change` | Change request, e.g. `{"text": "make the chip 1 ms slower"}`. Quick mode (the default) runs in about 2–3 min: Flash scopes it to the affected figures and files, Argon (medium thinking) edits only those files, only those figures are re-rendered, and the code checks, fidelity and a watched review run on them. A version that passes is marked **ready**; failing versions are **rejected**. `{"mode": "full"}` uses the full Fixer on every file. |
| `GET /api/runs/<id>/versions/<n>` | A version's status, new findings, and before/after clips |
| `POST /api/runs/<id>/versions/<n>/approve` / `discard` | **A human decides.** Only an approved version is published; nothing a change request makes goes live on its own. |
| `GET /api/runs`, `GET /api/runs/<id>` | Run list and summary (versions, findings table, explainer link) |
| `/runs/<id>/final/` | The published explainer with its proof section |

## Models

| Role | Model / agent |
|---|---|
| Spec, video analysis, search discovery, build, watched review, triage, fixes | `gemini-4-argon` |
| Intake, Deep Research source extraction, fidelity checks | `gemini-3.8-flash` |
| Web research | `deep-research-preview-04-2026` |
| Source scout (sandbox downloads, text extraction, numeric checks) | `antigravity-preview-09-2026` running `gemini-4-argon` |

All calls use the Gemini Interactions API and go through `cutaway/llm.py`. Each call is logged under `runs/<id>/calls/` with its model, thinking level, duration and token usage.

## Layout

```
cutaway/
  config.py  llm.py  schemas.py  events.py  fetch.py  sources.py  stats.py  scoring.py
  agents/    # root (ADK), pipeline, intake, research, spec_writer, builder, render, review, fixer, publish
  harness/   # contract.md, contract_test.py, render.py, browser.py, serve.py
  checks/    # citations.py, common.py, acoustics.py, runner.py
  prompts/   # intake, discover_search, web, scout, video, diagrams, spec, build, review, fidelity, triage, fix ...
server/      # FastAPI
web/         # input form + live progress + change requests
topics/      # per-topic config (prompt, audience, outline, law checks, model notes)
style/       # STYLE.md, the visual rules for generated explainers
sources/     # optional seed sources per topic (large media is git-ignored)
scripts/     # smoke_api.py, run_topic.py, baselines.py
tests/       # unit tests + fake-mode end-to-end test
runs/        # research/, spec.json, build/, v1/ v2/ ..., traces/, clips/, findings/, calls/, final/
```

## Ground rules

- Explainers are original: no code, text, images or assets are copied from any site or source.
- Sources are used only for facts, and every fact is cited with a quote that is checked against the source text.
- No secrets or third-party content in the repo: `.env`, `runs/`, `cache/`, `fixtures/` and seed media in `sources/` are git-ignored.

## Status

- [x] **M1: skeleton and API smoke test.** Verified with real calls: Argon 3.9 s, Flash 1.7 s, and a background call with `max_output_tokens` 262144 in 6.5 s.
- [x] **M2: research → cited spec.** First real run took 21 min. Deep Research found 69 sources and SourceScout 10, merged to 50. 37 had usable text (12 papers, 12 articles, 5 patents, 5 videos, 3 manufacturer docs); 47 were skipped, including paywalled, blocked and over-the-cap sources. 2.57M tokens read; 0.58 h of video watched. The spec has 11 figures, 11 entities, 7 laws, 12 video facts and 3 resolved conflicts. **All 73 citation quotes were found verbatim in the source texts.** (The search discoverer timed out on this run; it has since been fixed.)
- [x] **M3: one-pass build + contract test.** One background Argon call took 10.7 min and wrote 16 files (136k chars). The first contract test found 26 problems, mostly script errors; after one contract-fix round there were 0.
- [x] **M4: render harness.** 11 figures, 41 clips, 4,920 frames in 68 s (4 workers, each with its own browser). Frame time p95 is under 1 ms in `advance()`.
- [x] **M5: checks and review.** v1 had 46 findings: 27 watched (11 high), 18 interaction (wrong word colors, unrendered LaTeX), 1 law, 0 fidelity. The page reports exactly the cited params; note this doesn't prove the drawing uses them.
- [x] **M6: fix loop.** v1 → v2 → v3 automatically. Weighted findings went 124 → 14 → 5 and high-severity 11 → 2 → 0. The loop stopped at v3.
- [x] **M7: server, UI, proof section, change requests.** The real run is published at `/runs/20261025-120027-anc/final/`. Its proof section has research stats, the table above, 3 before/after fixes (F1, F6, F8) beside the cited YouTube moments, 3 conflicts and 25 cited sources. Change requests were tried on the real run through the server. "Make the chip 1 ms slower" was **rejected**: v4 had 18 high findings, because the law check measured a 0.905 residual, matching the predicted 2·|sin(π·150 Hz·1 ms)| = 0.91, and fidelity flagged the change against the cited 5 µs. So v3 stayed published. "Show on/off controls as On/Off toggles" was **accepted**: v5 had 0 high findings and weighted 4, and it is now the published version.
- [x] **M8: baselines.** All three scored with the same checks (table below).

### First real run (`20261025-120027-anc`): about 41 min from build to publish

```
version         contract           law   interaction      fidelity       watched   total  high  weighted
v1                0 (0H)        1 (0H)       18 (0H)        0 (0H)      27 (11H)      46    11       124
v2                0 (0H)        0 (0H)        0 (0H)        0 (0H)        4 (2H)       4     2        14
v3                0 (0H)        0 (0H)        0 (0H)        0 (0H)        4 (0H)       4     0         5
v4                0 (0H)        3 (3H)        0 (0H)        7 (7H)       17 (8H)      27    18       107   <- change request: chip 1 ms slower (rejected)
v5                0 (0H)        0 (0H)        0 (0H)        0 (0H)        3 (0H)       3     0         4   <- change request: On/Off toggles (accepted, published)
```

### More topics: binary search trees and the heart

Both ran end to end without help, with the same pipeline and checks:

```
run                     time    sources  video   quotes    weighted per version     high per version   published
20261025-154115-bst     44 min  22       1.5 h   64/64     117 -> 5 -> 37 -> 4      9 -> 1 -> 5 -> 0   v4
20261025-171009-heart   49 min  26       0.5 h   100/100   245 -> 50 -> 9           11 -> 6 -> 0       v3
```

- **heart:** v1 had 106 findings (94 interaction, 1 high-severity law failure). Two fix rounds reached 0 high, and the loop stopped on its own.
- **bst:** the fix from v2 to v3 made things worse (1 → 5 high), so the loop discarded v3, fixed v2 again, and published v4.
- Across all three topics, every first draft had 9–11 high-severity problems, and every published version has 0.

### Baselines (same spec ids, same checks)

```
baseline        contract           law   interaction      fidelity       watched   total  high  weighted
A prompt          1 (1H)        5 (4H)       18 (1H)        0 (0H)       25 (5H)      49    11       131
B no loop         0 (0H)        1 (0H)       18 (0H)        0 (0H)      27 (11H)      46    11       124
Full v3           0 (0H)        0 (0H)        0 (0H)        0 (0H)        4 (0H)       4     0         5
```

- A = topic prompt plus the bare figure, control, entity and probe ids (so the harness can drive it), one pass, no research, no contract-fix rounds.
- B = research + cited spec + one-pass build: the full run's v1.
- **The fix loop makes the biggest difference** (weighted 124 → 5).
- **Research mainly improves the physics:** A's page fails 4 high-severity law checks (the anti-noise leaves 11% and 18% residual where the limit is 5%, and a control changes nothing). B has none.
- Caveat: both pages darken the amber and grey word colors to keep text readable on white. The color-word check (tolerance 24 per RGB channel) counts that as a medium finding.

### Live change request in under 3 minutes

Measured on the real run, through the server, with the request "In the frequency and wavelength figure, show the wavelength readout in both meters and centimeters":

```
[  0.0s] change request on v5 -> v6
[  3.1s] scope: figures F3; files figures/F3.js            (Flash)
[ 54.1s] fixer changed figures/F3.js                         (Argon, medium thinking, 4.8k output tokens)
[ 69.7s] rendered 2 clips of F3 in 15.5s
[ 85.2s] checked: code checks + fidelity + watched review of F3
[ 85.2s] v6 passed the checks (0 high, was 0); waiting for approval
```

The published page doesn't change until someone clicks **Approve & publish** in the UI.

Example fixes found by watching: in v1, F6's waves were frozen while the reference video (V2 01:46) shows the noise wave racing the chip; F9's foam padding hid the inner mic; F10's waves didn't move. All three were fixed in v2.

