"""Thin async client for the LiteLLM proxy (OpenAI-compatible), returning parsed JSON."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv(Path(__file__).resolve().parent / ".env")

_missing = [k for k in ("LLM_MODEL", "LLM_BASE_URL", "LLM_API_KEY") if not os.environ.get(k)]
if _missing:
    raise RuntimeError(f"Missing {', '.join(_missing)} in backend/.env")

MODEL = os.environ["LLM_MODEL"]
# The SDK already retries connection errors, 429s, and 5xx; the timeout stops
# one hung call from freezing a whole negotiation.
_client = AsyncOpenAI(
    base_url=os.environ["LLM_BASE_URL"].rstrip("/") + "/v1",
    api_key=os.environ["LLM_API_KEY"],
    timeout=120,
    max_retries=2,
)

# Running totals, used later for the baseline comparison (latency + cost).
stats = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "seconds": 0.0}


def _parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in response")
    return json.loads(text[start : end + 1])


async def complete_json(system: str, user: str, retries: int = 2) -> dict:
    """Call the model and return its reply as a dict, retrying on malformed JSON."""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    for attempt in range(retries + 1):
        started = time.monotonic()
        resp = await _client.chat.completions.create(model=MODEL, messages=messages)
        stats["calls"] += 1
        stats["seconds"] += time.monotonic() - started
        if resp.usage:
            stats["prompt_tokens"] += resp.usage.prompt_tokens
            stats["completion_tokens"] += resp.usage.completion_tokens
        content = resp.choices[0].message.content or ""
        try:
            return _parse_json(content)
        except (ValueError, json.JSONDecodeError) as err:
            if attempt == retries:
                raise
            messages += [
                {"role": "assistant", "content": content},
                {"role": "user", "content": f"That was not valid JSON ({err}). Reply with only the JSON object."},
            ]
    raise AssertionError("unreachable")


if __name__ == "__main__":
    import asyncio

    reply = asyncio.run(complete_json("Reply in JSON.", 'Return {"status": "connected"}'))
    print(reply, stats)
