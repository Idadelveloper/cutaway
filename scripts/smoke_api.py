"""M1 smoke test: Argon (low), Flash, and one background long-output call with polling."""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cutaway import config, llm  # noqa: E402

RUN_ID = "smoke"


async def main() -> None:
    if not config.FAKE and not config.api_key():
        sys.exit("GEMINI_API_KEY missing: add it to .env (or run with CUTAWAY_FAKE=1)")
    t0 = time.time()

    async def show(label, coro):
        r = await coro
        print(f"\n=== {label} ===\n{r.text.strip()[:400]}\n-- {r.duration:.1f}s usage={r.usage} id={r.id}")
        return r

    await asyncio.gather(
        show("gemini-4-argon (thinking low)", llm.call(
            stage="smoke_argon", model=config.ARGON, thinking="low", run_id=RUN_ID,
            input="In one sentence: why does a 180-degree phase-flipped copy of a sound wave cancel it?")),
        show("gemini-3.8-flash", llm.call(
            stage="smoke_flash", model=config.FLASH, thinking="low", run_id=RUN_ID,
            input="Speed of sound in air at 20 C in m/s? Reply with just the number.")),
        show("gemini-4-argon background, max_output_tokens=262144", llm.call(
            stage="smoke_background", model=config.ARGON, thinking="low", run_id=RUN_ID,
            background=True, max_output_tokens=config.LONG_OUTPUT_TOKENS,
            input="Reply with exactly: background ok")),
    )
    print(f"\nall three done in {time.time() - t0:.1f}s (fake={config.FAKE})")


if __name__ == "__main__":
    asyncio.run(main())
