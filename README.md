# Cutaway

Cutaway researches a concept from real sources (papers, patents, manufacturer docs, articles and long videos), writes a cited spec, and generates a complete interactive 3D explainer in one pass. It then **renders the explainer, measures it, watches it against the reference sources, and fixes it in a loop** until the checks pass.

The demo topic is **how noise-cancelling headphones work** (`topics/anc/`).

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

**Fake mode:** `CUTAWAY_FAKE=1 make all` replays saved model responses from `fixtures/` instead of calling the API. It's fast and free, which makes it useful for testing the pipeline. Each real call that succeeds is saved as a fixture, and so is each fetched page. `fixtures/` and `cache/` are git-ignored, because they contain full third-party texts. The unit tests don't need them: they write their own made-up fixtures to a temp directory.

## Server

`make serve`, then open http://localhost:8000.

| Endpoint | What it does |
|---|---|
| `POST /runs` (or `/api/runs`) | Start a run: topic, plus optional uploaded files and URLs |
| `GET /runs/<id>/events` | Live progress as server-sent events |
| `POST /runs/<id>/change` | Change request, e.g. `{"text": "make the chip 1 ms slower"}`. The Fixer applies it, then the page is re-rendered and re-checked. It is only accepted if the checks pass. |
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
- [x] **M2: research → cited spec.** Discovery, ranking, full texts, quote verification and caching. Unit and fake-mode tests pass. First real run in progress.
- [x] **M3: one-pass build + contract test.** Contract test verified on a passing and a failing page. Real build pending the spec.
- [x] **M4: render harness.** Verified on a test page (traces, clips, keyframes, scroll clip). Real 10-figure timing pending.
- [x] **M5: checks and review.** Each law and interaction check has a passing and a failing synthetic test. Fidelity and watched review are tested in fake mode.
- [x] **M6: fix loop.** Triage, Fixer, stop/revert gate and scoring tested end to end in fake mode (v1 → v2).
- [x] **M7: server, UI, proof section, change requests.** Endpoints tested in-process. The proof section is generated in the fake end-to-end test.
- [x] **M8: baselines.** `scripts/baselines.py` written (A = prompt only, B = the full run's v1, Full = best version). Needs a finished real run.

The code for every milestone is written and tested in fake mode. The real end-to-end run (research → publish) is still going; this section will be updated with its results.
