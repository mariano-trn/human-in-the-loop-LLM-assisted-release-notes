# src/rn/llm.py
"""
LLM client utilities.

Purpose:
- Centralize all interactions with the AI Gateway (OpenAI-compatible API).
- Provide a small, reusable primitive (chat_json) for JSON-only LLM calls.
- Add observability (latency + token usage) for cost/debugging.

Design choices:
- API key is read from environment to avoid secrets in code.
- JSON parsing + minimal fence stripping to make downstream logic deterministic.
- Logging includes operation + run_id to correlate calls across a pipeline run.
"""
from __future__ import annotations

import os
import json
import time
import logging
from typing import Optional, Dict, Any
from openai import OpenAI

# Dedicated logger namespace so reviewers can filter LLM telemetry independently from the rest of the app logs.
logger = logging.getLogger("rn.llm")

# This keeps the rest of the code provider-agnostic: we just pass model IDs.
def get_client() -> OpenAI:

    # Secrets come from env to support local dev, CI, and safe key rotation.
    return OpenAI(
        api_key=os.environ["API_KEY"],
        base_url=os.environ["BASE_URL"],
    )


def _safe_usage_dict(usage: Any) -> Optional[Dict[str, int]]:
    """
    Try to normalize usage object to dict with ints.
    Works with OpenAI python types and dict-like.
    Note: different gateway/providers may expose usage in slightly different shapes.
    This function normalizes that variability into a stable dict.
    """
    if usage is None:
        return None
    # OpenAI python usually provides usage.prompt_tokens, etc.
    for attr in ("prompt_tokens", "completion_tokens", "total_tokens"):
        if not hasattr(usage, attr):
            break
    else:
        return {
            "prompt_tokens": int(getattr(usage, "prompt_tokens")),
            "completion_tokens": int(getattr(usage, "completion_tokens")),
            "total_tokens": int(getattr(usage, "total_tokens")),
        }

    if isinstance(usage, dict):
        try:
            return {
                "prompt_tokens": int(usage.get("prompt_tokens", 0)),
                "completion_tokens": int(usage.get("completion_tokens", 0)),
                "total_tokens": int(usage.get("total_tokens", 0)),
            }
        except Exception:
            return None

    return None


def chat_json(
    model: str,
    system: str,
    user: str,
    temperature: float = 0.2,
    operation: str = "unspecified",
    run_id: Optional[str] = None,
) -> dict:
    """
    Call the LLM and parse a JSON response.

    Contract:
    - The caller MUST instruct the model to return JSON only.
    - This function enforces deterministic downstream behavior by json.loads(...) parsing.

    Observability:
    - Logs latency_ms and token usage (if provided) for cost tracking and debugging.
    - operation + run_id make it easy to correlate calls belonging to the same pipeline execution.
    """

    client = get_client()

    # High-resolution timer to measure gateway + model latency.
    t0 = time.perf_counter()

    # OpenAI-compatible Chat Completions call. Models are identified by gateway model IDs (e.g., gpt-4.1).
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
    )

    dt_ms = (time.perf_counter() - t0) * 1000.0
    usage = _safe_usage_dict(getattr(resp, "usage", None))

    logger.info(
        "llm_call op=%s model=%s latency_ms=%.1f run_id=%s usage=%s",
        operation,
        model,
        dt_ms,
        run_id,
        usage,
    )

    txt = resp.choices[0].message.content.strip()

    # Robustness: some models wrap JSON in ```json fences. We strip them before json.loads.
    # If the response is not valid JSON, json.loads will raise (fail fast).
    if txt.startswith("```"):
        txt = txt.strip("`")
        txt = txt.replace("json", "", 1).strip()

    return json.loads(txt)
