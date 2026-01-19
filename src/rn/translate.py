# src/rn/translate.py
"""
Translation utilities (LLM-assisted).

Purpose:
- Translate user-facing release notes into one or more target languages.
- Preserve Markdown structure exactly to keep rendering stable and links intact.
- Keep internal workflow sections (e.g., clarification requests) in English to avoid
  translating internal communication artifacts intended for developers/doc owners.

Design principles:
- Translate only the "public" portion of the document.
- JSON-only responses + deterministic parsing to avoid fragile post-processing.
- Low temperature for consistent, repeatable translations.
"""
from __future__ import annotations

from typing import Optional, Tuple
import re
from rn.llm import chat_json

# Sentinel header used to split user-facing content from internal workflow content.
# We keep the internal section un-translated to avoid altering developer-facing questions.
_INTERNAL_HEADER_RE = re.compile(r"^##\s+Needs clarification\s*\(internal\)\s*$", re.IGNORECASE | re.MULTILINE)

def split_public_and_internal(md: str) -> Tuple[str, Optional[str]]:
    """
    Split markdown into:
      - public_part: everything before '## Needs clarification (internal)'
      - internal_part: that section and everything after (kept as-is)
    If the header is not found, internal_part is None.
    Why:
    - Public release notes are user-facing and benefit from localization.
    - The internal clarification section is a workflow artifact for doc owners/developers,
      so we keep it as-is (English) for precision and to avoid accidental meaning drift.
    """
    m = _INTERNAL_HEADER_RE.search(md)
    if not m:
        return md, None

    # Split at the start of the internal header so the internal section is preserved verbatim.
    idx = m.start()
    return md[:idx].rstrip() + "\n", md[idx:].lstrip()

# Translation guardrails:
# - Markdown structure must remain identical (headings/bullets/indentation)
# - URLs must remain unchanged
# - Output must be JSON-only for robust downstream parsing
SYSTEM_PROMPT = """You are a professional technical writer translating release notes.
Constraints:
- Preserve Markdown structure EXACTLY (headings, bullets, indentation).
- Preserve URLs and link targets exactly.
- Do NOT add or remove sections.
- Do NOT change inline code or code spans.
Return ONLY a JSON object: {"text": "<translated markdown>"}.
"""

def translate_public_markdown(
    md_public: str,
    target_language: str,
    model: str = "azure-oai-gpt-4.1",
    run_id: Optional[str] = None,
) -> str:
    """
    Translate the public part of release notes into the target language.

    Contract:
    - Returns translated Markdown as plain text.
    - Raises if model output is not valid JSON with key 'text' (fail fast).
    """

    # Keep the prompt minimal to reduce token usage while preserving strict constraints via system prompt.
    user_prompt = f"""Translate the following Markdown release notes into {target_language}.
Return JSON: {{ "text": "<translated markdown>" }}.

MARKDOWN:
{md_public}
"""
    out = chat_json(
        model=model,
        system=SYSTEM_PROMPT,
        user=user_prompt,
        temperature=0.2,
        operation="translate_release_notes",
        run_id=run_id,
    )

    # Fail fast: translation is an automation step and must return a predictable payload.
    if not isinstance(out, dict) or "text" not in out:
        raise ValueError("Expected JSON with key 'text'.")
    return str(out["text"])
