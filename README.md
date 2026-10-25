# Cutaway

Cutaway researches a concept from real sources (web pages, PDFs of papers and patents, long videos, diagrams), writes a cited spec, and generates a complete interactive 3D explainer in one pass. It then **renders the explainer, measures it, watches it against the reference sources, and fixes it in a loop** until the checks pass.

The demo topic is **how noise-cancelling headphones work** (`topics/anc/`).

Built at the Gemini 4 Argon SF hackathon (problem statement Two: "More Dimensions").

## How it works

```
Intake → Parallel research → SpecWriter → Builder → Render → Parallel review → Fix loop → Publish
```

1. **Research** (in parallel)
   - VideoAnalyst watches the source videos and returns facts with timestamps.
   - DiagramReader extracts labels and numbers from images.
   - PaperReader extracts equations and constants from PDFs.
   - Two optional jobs: Deep Research for a cited web report, and a managed agent that finds open papers and checks the key equations numerically.
2. **Spec**: Gemini 4 Argon merges the research into `spec.json`. Every entity, number, law and figure carries a citation, and conflicts between sources are listed along with the value chosen.
3. **Build**: one long background call generates the whole explainer: HTML, CSS, a shared physics model, and one Three.js file per figure. Every page follows a test contract (`window.__cutaway`).
4. **Render**: Playwright steps each figure frame by frame at its default settings, then sweeps each control from min to max. It records the state on every frame and stitches the frames into clips.
5. **Review**
   - Acoustic law checks: wave speed, superposition, phase flip, delay residual.
   - Interaction checks.
   - A fidelity check against the cited values.
   - A watched review, where Argon compares each clip with the matching moments in the reference videos.
6. **Fix loop**: findings are triaged and passed to a fixer, which writes the next version. The loop stops when there are no high-severity findings, or after 3 rounds. If a version gets worse, the loop reverts to the best one.
7. **Publish**: a proof section is added to the end of the explainer. It shows findings per version, before/after clips of the biggest fixes, the sources, and the resolved conflicts.

## Setup

Requires Python 3.12, `ffmpeg`, and a Gemini API key.

```bash
uv venv .venv && source .venv/bin/activate
uv pip install -r requirements.txt
playwright install chromium
echo "GEMINI_API_KEY=your-key" > .env
```

Put the topic's sources in `sources/anc/`: PDFs, images, MP4s, and a `urls.txt` with video and paper links.

## Commands

```
make smoke        # API smoke test (Argon, Flash, background long-output call)
make research     # run_topic.py anc --until spec
make build        # through v1
make loop         # full run with fix loop
make serve        # FastAPI on :8000
make baselines    # prompt-only and no-loop baselines, scored with the same checks
make test         # check unit tests
```

Use `TOPIC=<name>` to run another topic, for example `make research TOPIC=anc`.

**Fake mode:** `CUTAWAY_FAKE=1 make loop` replays saved model responses from `fixtures/` instead of calling the API. It's fast and free, which makes it useful for testing the pipeline. Each real call that succeeds is saved as a fixture.

## Models

| Role | Model / agent |
|---|---|
| Spec, video analysis, build, watched review, fixes | `gemini-4-argon` |
| Intake, fidelity checks, summaries | `gemini-3.8-flash` |
| Web research (optional, `--with-deep-research`) | `deep-research-preview-04-2026` |
| Source scout (optional, `--with-scout`) | `antigravity-preview-09-2026` running `gemini-4-argon` |

All calls use the Gemini Interactions API and go through `cutaway/llm.py`. Each call is logged under `runs/<id>/calls/` with its model, thinking level, duration and token usage. The pipeline is orchestrated with Google ADK 2.13 (`SequentialAgent` / `ParallelAgent` / `LoopAgent`) using custom stages.

## Layout

```
cutaway/
  config.py  llm.py  schemas.py  events.py  scoring.py
  agents/    # root workflow, intake, research, spec_writer, builder, review, fixer, publish
  harness/   # Playwright render + page contract
  checks/    # common checks, acoustics laws, runner
  prompts/   # prompt templates
server/      # FastAPI: start runs, SSE progress, serve explainers
web/         # input page + live progress viewer
topics/      # per-topic config (prompt, audience, figure hints, law params)
style/       # STYLE.md, the visual rules for generated explainers
sources/     # input sources per topic (large media is git-ignored)
scripts/     # smoke_api.py, run_topic.py, baselines.py
tests/       # check unit tests with synthetic traces
runs/        # outputs: research/, spec.json, v1/, v2/, traces/, clips/, findings/, calls/
```

## Ground rules

- Explainers are written from scratch in the visual style of interactive long-form explainers. No code, text, images or assets are copied from any site or source.
- Sources are used only for facts, and every fact is cited.
- No secrets in the repo: the API key is read from `.env`, which is git-ignored.

## Status

- [x] M1: skeleton, API wrapper with logging and fake mode, smoke test. The code is written; the real API run is pending until the key is added.
- [ ] M2: research → cited spec
- [ ] M3: one-pass build + contract test
- [ ] M4: render harness
- [ ] M5: checks and review
- [ ] M6: fix loop
- [ ] M7: server, UI, proof section, change requests
- [ ] M8: baselines
