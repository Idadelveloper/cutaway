# Setup notes / quirks

- google-genai 2.33.0: the Interactions API lives in `google/genai/_gaos/`. `generation_config` (thinking_level, max_output_tokens)
  cannot be combined with `agent`/`agent_config`. Structured output is `response_format={"type":"text","mime_type":"application/json","schema":{...}}`
  (`response_mime_type` is deprecated). `create(..., timeout=<seconds>)`.
- google-adk 2.13.0 ships both classic SequentialAgent/ParallelAgent/LoopAgent and a graph `google.adk.workflow`. We use the classic ones
  with custom BaseAgent stages; long-decode calls bypass ADK and go through `cutaway/llm.py`.
- Fake mode: `CUTAWAY_FAKE=1` replays `fixtures/<stage>/<hash>.json`; falls back to the newest fixture of a stage if the prompt changed.
- The shell venv may not be active: the Makefile uses `.venv/bin/python` directly.
- `config.load_prompt(name, /, **values)`: `name` is positional-only because prompt templates use a `{{name}}` placeholder.
- Research discovers sources by itself (search tools + Deep Research + SourceScout); files in `sources/<topic>/` are optional seeds
  (`urls.txt` lines are `title | url` or a bare url). Ids are assigned after the merge by type: P paper, T patent, S standard,
  M manufacturer, A article, B blog, V video, D diagram, W other web. Rank 1 = P/T/S/M/D, 2 = A/V, 3 = B/W.
- Tool syntax (google-genai 2.33.0 types): `tools=[{"type": "google_search"}, {"type": "url_context"}]`. With tools or agents we
  don't set `response_format`; the prompt asks for JSON and pydantic validates it.
- Environment files: `client.environments.files.list(environment, path)` returns metadata only (no download URL in the model),
  so SourceScout returns extracted text inside its JSON (capped at 25k chars per source). Any source with less text than that is
  re-fetched locally (pypdf for PDFs, trafilatura for HTML). Blocked/paywalled/too-short pages are skipped and logged in sources.json.
- Video links go to VideoAnalyst as `{"type": "video", "uri": url}`. If the API refuses one, the video is skipped (no download).
  Its transcript is stored as the source text so video quotes can be verified.
- Citation quotes are matched after NFKC + lowercase + whitespace normalization; a second punctuation-insensitive pass catches PDF
  extraction artifacts and is counted separately (`citations_verified_loose`).
- Research is cached in `cache/<topic>/research/` (git-ignored: it holds third-party texts). `make research-fresh` redoes it.
  Fetched pages are also saved as fixtures in `fixtures/fetch/`, so fake mode never hits the network.
- A failing research job is logged and skipped; the run only stops if every job fails.
- Tests point `CUTAWAY_FIXTURES`/`config.FIXTURES_DIR` at a temp dir with synthetic fixtures; real fixtures go in `fixtures/` once the API runs.
