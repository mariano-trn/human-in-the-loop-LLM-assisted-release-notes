# src/rn/filtering_llm.py
"""
LLM filtering stage for ambiguous changes.

Purpose:
- Handle only commits that the deterministic rules stage could not confidently classify.
- Decide include/exclude for public release notes.
- If included, classify strictly into {feature, bugfix} and write user-facing title/description.
- If uncertain, emit a clarification question (human-in-the-loop friendly).

Key guardrails:
- Strict JSON-only output + Pydantic validation (LLMDecision) to avoid brittle parsing.
- Low temperature for consistency.
- run_id correlates all LLM calls in a pipeline run for observability (token usage / latency logged in rn.llm).
"""
from __future__ import annotations
import uuid
from typing import Any, Dict, List
from rn.llm import chat_json
from rn.schema import LLMDecision

# System prompt acts as a policy layer:
# - reduces hallucinations / verbosity
# - enforces user-facing tone
# - forces a strict schema for robust downstream handling
# NOTE: "Return ONLY valid JSON" is critical: it allows reliable automation and schema validation.
SYSTEM_PROMPT = """You are assisting with publishing user-facing release notes.
Rules:
- Exclude internal-only changes (refactors, chores, CI, tests, dependency bumps) unless clearly user-impacting.
- If included, classify strictly as: feature OR bugfix.
- Write for end users: clear, concise, minimal jargon.
- If information is insufficient, set needs_clarification=true and provide a single concrete question to ask the author.
Return ONLY valid JSON matching the requested schema. No extra text.
"""

# We pass the minimal high-signal context to the model:
# subject + body + files + author. This is typically enough for a decision,
# while keeping token usage low.
def build_user_prompt(item: Dict[str, Any]) -> str:
    subject = (item.get("subject") or "").strip()
    body = (item.get("body") or "").strip()
    files = item.get("files") or []     # File list is included as a weak but useful signal (e.g., docs/tests/CI changes)
    author = item.get("author_name") or "Unknown"

    return f"""
Decide if the following change should appear in public release notes.

SCHEMA:
{{
  "include": true/false,
  "category": "feature"|"bugfix"|null,
  "title": string|null,
  "description": string|null,
  "needs_clarification": true/false,
  "clarification_question": string|null,
  "reason": string
}}

CHANGE:
- author: {author}
- subject: {subject}
- body: {body}
- files: {files}

Constraints for included entries:
- title <= 70 chars
- description <= 240 chars
"""

def llm_decide_ambiguous(
    ambiguous_items: List[Dict[str, Any]],
    model: str = "azure-oai-gpt-4.1",
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    run_id = str(uuid.uuid4())      # One run_id per pipeline run: makes logs traceable and simplifies cost auditing
    for it in ambiguous_items:
        raw = chat_json(
            model=model,
            system=SYSTEM_PROMPT,
            user=build_user_prompt(it),
            temperature=0.2,
            operation="filter_ambiguous",
            run_id=run_id,
        )
        # Pydantic validation is the main guardrail:
        # if the model returns malformed JSON/fields, we fail fast instead of silently publishing garbage.
        decision = LLMDecision.model_validate(raw)

        # review_status is the normalized field used by the review manifest and renderer:
        # - included / excluded / needs_clarification
        review_status = "included" if decision.include else "excluded"
        if decision.needs_clarification:
            review_status = "needs_clarification"

        it2 = dict(it)
        it2.update(
            {
                "include": decision.include,
                "filter_stage": "llm",
                "filter_reason": decision.reason,
                "category": decision.category,
                "title": decision.title,
                "description": decision.description,
                "needs_clarification": decision.needs_clarification,
                "clarification_question": decision.clarification_question,
                "review_status": review_status,
            }
        )
        out.append(it2)
    return out
