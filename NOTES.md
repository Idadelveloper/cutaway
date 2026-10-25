# Setup notes / quirks

- google-genai 2.33.0: the Interactions API lives in `google/genai/_gaos/`. `generation_config` (thinking_level, max_output_tokens)
  cannot be combined with `agent`/`agent_config`. Structured output is `response_format={"type":"text","mime_type":"application/json","schema":{...}}`
  (`response_mime_type` is deprecated). `create(..., timeout=<seconds>)`.
- google-adk 2.13.0 ships both classic SequentialAgent/ParallelAgent/LoopAgent and a graph `google.adk.workflow`. We use the classic ones
  with custom BaseAgent stages; long-decode calls bypass ADK and go through `cutaway/llm.py`.
- Fake mode: `CUTAWAY_FAKE=1` replays `fixtures/<stage>/<hash>.json`; falls back to the newest fixture of a stage if the prompt changed.
- The shell venv may not be active: the Makefile uses `.venv/bin/python` directly.
- `config.load_prompt(name, /, **values)`: `name` is positional-only because prompt templates use a `{{name}}` placeholder.
- Sources: files in `sources/<topic>/` get ids by type (V video, P paper, D diagram); `urls.txt` lines are `title | url` or a bare url.
  YouTube links go straight to Gemini as a video `uri`; remote PDFs are downloaded to `_downloads/`; other links are read with the `url_context` tool.
  Local files over 15 MB are uploaded with the Files API instead of being sent inline.
- A failing research job is logged and skipped; the run only stops if every job fails.
- Tests point `CUTAWAY_FIXTURES`/`config.FIXTURES_DIR` at a temp dir with synthetic fixtures; real fixtures go in `fixtures/` once the API runs.
