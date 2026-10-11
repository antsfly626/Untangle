"""Lazy structured cloud inference; intentionally independent of legacy llm.py."""

import json
import logging
import os
import time
from pathlib import Path
from typing import TypeVar
from urllib.parse import urlsplit

from dotenv import load_dotenv
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, OpenAIError
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)
logger = logging.getLogger(__name__)
stats = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "seconds": 0.0}


class EvidenceServiceError(RuntimeError):
    """Safe to expose to callers; never contains a raw provider exception."""

    def __init__(self, message: str, *, code: str = "review_unavailable"):
        super().__init__(message)
        self.code = code


class EvidenceConfigurationError(EvidenceServiceError):
    pass


ERROR_DETAILS = {
    "review_unavailable": "Evidence review unavailable; no valid vote was recorded.",
    "authentication": "Model authentication failed. Check that the API key belongs to the configured provider.",
    "access_denied": "Model access denied. Check the API key's permissions and model access.",
    "not_found": "Model or API endpoint not found. Check LLM_MODEL and LLM_BASE_URL.",
    "quota": "Model provider rate limit or quota reached. Check your provider's quota and billing, then retry.",
    "provider_request": "Model provider rejected the request. Check the model name and structured-output support.",
    "timeout": "Model request timed out. Retry the review.",
    "connection": "Cannot connect to the model provider. Check the endpoint and network connection.",
    "provider_unavailable": "Model provider is temporarily unavailable. Retry later or select another model available to your account.",
    "invalid_output": "Model returned an invalid or inconsistent review. Retry; no vote was recorded.",
    "refusal": "Model declined the review. No vote was recorded.",
}


def normalize_base_url(value: str | None, *, append_v1: bool = True) -> str | None:
    """Accept provider roots or complete compatible endpoints without double suffixes."""
    if not value:
        return None
    url = value.rstrip("/")
    parsed = urlsplit(url)
    if parsed.hostname == "generativelanguage.googleapis.com":
        return f"{parsed.scheme}://{parsed.netloc}/v1beta/openai/"
    return url if not append_v1 or url.endswith("/v1") else url + "/v1"


def model_options(model: str, base_url: str | None) -> dict:
    """Keep Groq free-tier completions bounded, including reasoning tokens."""
    if urlsplit(base_url or "").hostname == "api.groq.com" and model in {
        "openai/gpt-oss-20b", "openai/gpt-oss-120b",
    }:
        return {"reasoning_effort": "low", "max_completion_tokens": 4096}
    return {"reasoning_effort": "low"} if model.startswith("gemini-3") else {}


async def structured_call(system: str, payload: dict, schema: type[T]) -> T:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    key = os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY")
    model = os.getenv("EVIDENCE_MODEL") or os.getenv("LLM_MODEL")
    if not key or not model:
        raise EvidenceConfigurationError("Evidence model credentials or model name are not configured.")
    configured_url = os.getenv("EVIDENCE_BASE_URL")
    if not configured_url and not os.getenv("OPENAI_API_KEY"):
        configured_url = os.getenv("LLM_BASE_URL")
    base_url = normalize_base_url(configured_url, append_v1=not bool(os.getenv("EVIDENCE_BASE_URL")))
    # Gemini defaults can spend most of a demo request on thinking. Use its documented
    # lower-latency setting for Gemini 3; keep other providers' configuration unchanged.
    options = model_options(model, base_url)
    try:
        async with AsyncOpenAI(api_key=key, base_url=base_url, timeout=30, max_retries=1) as client:
            started = time.monotonic()
            completion = await client.beta.chat.completions.parse(
                model=model,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": json.dumps(payload, separators=(",", ":"))}],
                response_format=schema,
                **options,
            )
            stats["calls"] += 1
            stats["seconds"] += time.monotonic() - started
            usage = getattr(completion, "usage", None)
            if usage:
                stats["prompt_tokens"] += usage.prompt_tokens or 0
                stats["completion_tokens"] += usage.completion_tokens or 0
            message = completion.choices[0].message
            if message.refusal or message.parsed is None:
                raise EvidenceServiceError(ERROR_DETAILS["refusal"], code="refusal")
            return schema.model_validate(message.parsed)
    except APITimeoutError:
        raise EvidenceServiceError(ERROR_DETAILS["timeout"], code="timeout") from None
    except APIConnectionError:
        raise EvidenceServiceError(ERROR_DETAILS["connection"], code="connection") from None
    except APIStatusError as exc:
        code = {400: "provider_request", 401: "authentication", 403: "access_denied",
                404: "not_found", 429: "quota"}.get(
                    exc.status_code, "provider_unavailable" if exc.status_code >= 500 else "review_unavailable")
        logger.warning("Evidence %s provider HTTP %s", schema.__name__, exc.status_code)
        raise EvidenceServiceError(ERROR_DETAILS[code], code=code) from None
    except (OpenAIError, ValidationError, ValueError, IndexError) as exc:
        # Log only schema/error identifiers, never response text or validation inputs.
        issues = [error["type"] for error in exc.errors()] if isinstance(exc, ValidationError) else []
        logger.warning("Evidence %s failed (%s): %s", schema.__name__, type(exc).__name__, issues)
        raise EvidenceServiceError(ERROR_DETAILS["invalid_output"], code="invalid_output") from None
