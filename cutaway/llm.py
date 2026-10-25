"""The single wrapper for every model/agent call (Gemini Interactions API).

Every call is logged to runs/<run_id>/calls/ (prompt, response, model, thinking,
duration, usage). With CUTAWAY_FAKE=1, calls replay fixtures from fixtures/<stage>/
instead of hitting the API. Real successful calls are saved as fixtures.

SDK facts (checked in google-genai 2.33.0, google/genai/_gaos/interactions.py and
_gaos/types/interactions/*.py):
  client.aio.interactions.create(model=|agent=, input=, system_instruction=,
      generation_config={"thinking_level", "max_output_tokens"}, background=,
      previous_interaction_id=, response_format={"type": "text",
      "mime_type": "application/json", "schema": {...}}, tools=, agent_config=,
      environment=, timeout=<seconds>)
  generation_config and agent_config/agent are mutually exclusive.
  client.aio.interactions.get(id) -> Interaction(status, output_text, usage)
  usage fields: total_input_tokens, total_output_tokens, total_thought_tokens, total_tokens
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import itertools
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Type, TypeVar

from pydantic import BaseModel, ValidationError

from . import config, events

T = TypeVar("T", bound=BaseModel)

_client = None
_counter = itertools.count(1)


def client():
    global _client
    if _client is None:
        from google import genai
        _client = genai.Client(api_key=config.api_key())
    return _client


@dataclass
class Result:
    text: str
    id: str | None = None
    usage: dict = field(default_factory=dict)
    duration: float = 0.0
    status: str = "completed"
    fake: bool = False


# ---------- helpers ----------

def _strip_binary(obj: Any) -> Any:
    """Copy of input with inline base64 data replaced by a size marker (for logs/hashing)."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "data" and isinstance(v, (str, bytes)) and len(v) > 256:
                out[k] = f"<{len(v)} bytes sha={hashlib.sha256(v if isinstance(v, bytes) else v.encode()).hexdigest()[:12]}>"
            else:
                out[k] = _strip_binary(v)
        return out
    if isinstance(obj, list):
        return [_strip_binary(x) for x in obj]
    return obj


def _key(stage: str, target: str, input: Any, system: str | None) -> str:
    blob = json.dumps([target, _strip_binary(input), system], sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _usage_dict(usage) -> dict:
    if not usage:
        return {}
    keys = ["total_input_tokens", "total_output_tokens", "total_thought_tokens", "total_tokens"]
    return {k: getattr(usage, k, None) for k in keys if getattr(usage, k, None) is not None}


def _calls_dir(run_id: str | None) -> Path:
    d = config.RUNS_DIR / (run_id or "_adhoc") / "calls"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _log(run_id, stage, target, thinking, input, system, result: Result, error: str | None = None) -> Path:
    n = next(_counter)
    path = _calls_dir(run_id) / f"{int(time.time())}_{n:03d}_{stage}.json"
    record = {
        "stage": stage, "target": target, "thinking": thinking,
        "duration_s": round(result.duration, 2), "usage": result.usage,
        "interaction_id": result.id, "status": result.status, "fake": result.fake,
        "error": error, "system": system, "input": _strip_binary(input), "response": result.text,
    }
    path.write_text(json.dumps(record, indent=2, default=str))
    events.emit(run_id, "call", stage=stage, target=target, thinking=thinking,
                duration_s=record["duration_s"], usage=result.usage, path=str(path),
                fake=result.fake, error=error,
                msg=f"{stage}: {target} [{thinking}] {record['duration_s']}s {result.usage.get('total_tokens', '')} tok"
                    + (" (fake)" if result.fake else "") + (f" ERROR {error}" if error else ""))
    return path


def _fixture_dir(stage: str) -> Path:
    return config.FIXTURES_DIR / stage


def _save_fixture(stage: str, key: str, result: Result) -> None:
    d = _fixture_dir(stage)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{key}.json").write_text(json.dumps({"text": result.text, "usage": result.usage, "saved": time.time()}, indent=2))


def _load_fixture(stage: str, key: str) -> Result:
    d = _fixture_dir(stage)
    exact = d / f"{key}.json"
    if exact.exists():
        path = exact
    else:  # prompt changed since recording: fall back to the newest fixture for this stage
        candidates = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime) if d.exists() else []
        if not candidates:
            raise RuntimeError(f"CUTAWAY_FAKE=1 but no fixture for stage '{stage}' in {d}")
        path = candidates[-1]
    data = json.loads(path.read_text())
    return Result(text=data["text"], usage=data.get("usage", {}), fake=True)


def file_part(path: str | Path, kind: str | None = None) -> dict:
    """Inline content part for a local file (image/document/video/audio)."""
    path = Path(path)
    ext = path.suffix.lower().lstrip(".")
    mime = {
        "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp", "gif": "image/gif",
        "pdf": "application/pdf", "mp4": "video/mp4", "mov": "video/quicktime", "webm": "video/webm",
        "mp3": "audio/mp3", "wav": "audio/wav",
    }[ext]
    kind = kind or {"image": "image", "application": "document", "video": "video", "audio": "audio"}[mime.split("/")[0]]
    return {"type": kind, "mime_type": mime, "data": base64.b64encode(path.read_bytes()).decode()}


def text_part(text: str) -> dict:
    return {"type": "text", "text": text}


INLINE_LIMIT = 15 * 1024 * 1024
_uploaded: dict[str, str] = {}


async def media_part(path: str | Path) -> dict:
    """Content part for a local file: inline if small, else uploaded via the Files API (uri)."""
    path = Path(path)
    if path.stat().st_size <= INLINE_LIMIT:
        return file_part(path)
    mime = {"mp4": "video/mp4", "mov": "video/quicktime", "webm": "video/webm", "pdf": "application/pdf"}[path.suffix.lower().lstrip(".")]
    kind = "document" if mime == "application/pdf" else "video"
    if config.FAKE:  # never upload in fake mode; hash only the path
        return {"type": kind, "mime_type": mime, "uri": f"fake://{path.name}"}
    key = f"{path}:{path.stat().st_mtime}"
    if key not in _uploaded:
        f = await client().aio.files.upload(file=str(path), config={"mime_type": mime, "display_name": path.name})
        while str(getattr(f.state, "value", f.state)) == "PROCESSING":
            await asyncio.sleep(3)
            f = await client().aio.files.get(name=f.name)
        if str(getattr(f.state, "value", f.state)) == "FAILED":
            raise RuntimeError(f"upload of {path} failed")
        _uploaded[key] = f.uri
    return {"type": kind, "mime_type": mime, "uri": _uploaded[key]}


# ---------- core call ----------

async def call(
    *,
    stage: str,
    input: Any,
    model: str | None = None,
    agent: str | None = None,
    thinking: str | None = "low",
    system: str | None = None,
    json_schema: dict | None = None,
    json_mode: bool = False,
    background: bool = False,
    max_output_tokens: int | None = None,
    previous_interaction_id: str | None = None,
    agent_config: dict | None = None,
    environment: Any = None,
    tools: list | None = None,
    timeout: float | None = None,
    run_id: str | None = None,
) -> Result:
    target = agent or model or config.FLASH
    if agent is None and model is None:
        model = config.FLASH
    key = _key(stage, target, input, system)
    th_label = thinking if agent is None else (agent_config or {}).get("model", "agent")

    if config.FAKE:
        t0 = time.time()
        result = _load_fixture(stage, key)
        result.duration = time.time() - t0
        _log(run_id, stage, target, th_label, input, system, result)
        return result

    kwargs: dict[str, Any] = {"input": input}
    if agent:
        kwargs["agent"] = agent
        if agent_config:
            kwargs["agent_config"] = agent_config
        if environment is not None:
            kwargs["environment"] = environment
    else:
        kwargs["model"] = model
        gen: dict[str, Any] = {}
        if thinking:
            gen["thinking_level"] = thinking
        if max_output_tokens:
            gen["max_output_tokens"] = max_output_tokens
        if gen:
            kwargs["generation_config"] = gen
    if system:
        kwargs["system_instruction"] = system
    if json_schema is not None:
        kwargs["response_format"] = {"type": "text", "mime_type": "application/json", "schema": json_schema}
    elif json_mode:
        kwargs["response_format"] = {"type": "text", "mime_type": "application/json"}
    if previous_interaction_id:
        kwargs["previous_interaction_id"] = previous_interaction_id
    if tools:
        kwargs["tools"] = tools
    if agent:
        background = True  # agents require background mode
    if background:
        kwargs["background"] = True
    if timeout is None:
        timeout = config.TIMEOUT_HIGH if (thinking == "high" or background) else config.TIMEOUT_DEFAULT

    aio = client().aio.interactions
    t0 = time.time()
    try:
        inter = await aio.create(**kwargs, timeout=timeout)
        if background:
            last_status = None
            while inter.status in ("queued", "in_progress"):
                if inter.status != last_status:
                    events.emit(run_id, "info", stage=stage, msg=f"{stage}: {target} {inter.status} ({inter.id})")
                    last_status = inter.status
                await asyncio.sleep(config.POLL_SECONDS)
                inter = await aio.get(inter.id, timeout=60)
        result = Result(text=inter.output_text or "", id=inter.id, usage=_usage_dict(inter.usage),
                        duration=time.time() - t0, status=str(inter.status))
        if inter.status not in ("completed",):
            err = f"status={inter.status} errors={getattr(inter, 'errors', None)}"
            _log(run_id, stage, target, th_label, input, system, result, error=err)
            raise RuntimeError(f"{stage}: interaction {inter.id} ended with {err}")
    except Exception as e:
        if not isinstance(e, RuntimeError):
            _log(run_id, stage, target, th_label, input, system,
                 Result(text="", duration=time.time() - t0, status="error"), error=repr(e)[:2000])
        raise
    _log(run_id, stage, target, th_label, input, system, result)
    _save_fixture(stage, key, result)
    return result


# ---------- JSON helpers ----------

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_json(text: str) -> Any:
    t = text.strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    t = _FENCE.sub("", t).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        # last resort: outermost {...}
        i, j = t.find("{"), t.rfind("}")
        if i >= 0 and j > i:
            return json.loads(t[i:j + 1])
        raise


async def call_model(model_cls: Type[T], *, stage: str, use_schema: bool = False,
                     response_json: bool = True, **kw) -> tuple[T, Result]:
    """Call and validate the output against a pydantic model; retry once with the error.

    response_json=False skips response_format (e.g. with tools or agents); the prompt must ask for JSON.
    """
    schema = model_cls.model_json_schema() if (use_schema and response_json) else None
    jm = response_json and not use_schema
    res = await call(stage=stage, json_schema=schema, json_mode=jm, **kw)
    try:
        return model_cls.model_validate(parse_json(res.text)), res
    except (ValidationError, json.JSONDecodeError, ValueError) as e:
        if config.FAKE:
            raise
        events.emit(kw.get("run_id"), "info", stage=stage, msg=f"{stage}: invalid JSON, retrying once: {str(e)[:300]}")
        fix = (f"Your previous output failed validation:\n{str(e)[:4000]}\n\n"
               "Return the complete corrected JSON object only, no prose, no code fences.")
        kw2 = dict(kw)
        if res.id:
            kw2["previous_interaction_id"] = res.id
            kw2["input"] = fix
        else:
            kw2["input"] = [text_part(fix)] + (kw["input"] if isinstance(kw["input"], list) else [text_part(str(kw["input"]))])
        if kw.get("agent"):  # agents can't continue via previous_interaction_id reliably; re-ask with Flash
            kw2 = {"model": config.FLASH, "thinking": "low", "run_id": kw.get("run_id"),
                   "input": fix + "\n\nPrevious output:\n" + res.text[:200000]}
            jm = True
        res2 = await call(stage=stage + "_retry", json_schema=schema, json_mode=jm, **kw2)
        return model_cls.model_validate(parse_json(res2.text)), res2


async def call_json(*, stage: str, **kw) -> tuple[Any, Result]:
    """Call in JSON mode and parse (no schema validation)."""
    res = await call(stage=stage, json_mode=True, **kw)
    return parse_json(res.text), res
