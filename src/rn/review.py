# src/rn/review.py
"""
Review manifest builder (Human-in-the-Loop boundary).

Purpose:
- Create a stable, editable artifact (`review.json`) that acts as the contract
  between automated analysis and human validation.
- This file is the ONLY input consumed by the publishing/rendering stage.

Design principles:
- Explicit review_status for every entry (included / excluded / needs_clarification)
- Clear separation between:
    * automated signals (filter_stage, filter_reason)
    * human-editable fields (title, description, category)
- JSON format chosen for:
    * diff-friendliness
    * easy editing
    * CI / tooling compatibility
"""
from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List


def build_review_manifest(
    *,
    repo_url: str,
    from_ref: str,
    to_ref: str,
    items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Build the review manifest consumed by the documentation owner.

    This manifest represents the explicit Human-in-the-Loop checkpoint:
    - It aggregates all automated decisions and LLM suggestions
    - It is meant to be reviewed, edited, and validated by a human
    - Downstream publishing reads ONLY from this file
    """

    # Metadata provides traceability and auditability for the review process
    manifest = {
        "metadata": {
            "repo": repo_url,
            "from_ref": from_ref,
            "to_ref": to_ref,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "entries": [],
    }

    for it in items:
        # Each entry is intentionally flattened and explicit
        # to make manual review and editing as simple as possible.
        entry = {
            "sha": it.get("sha"),
            "subject": it.get("subject"),
            "author": it.get("author_name"),
            "url": it.get("url"),

            # review_status is the single authoritative signal
            # controlling publication behavior downstream
            "review_status": it.get("review_status"),
            "category": it.get("category"),

            # These fields are intentionally optional and human-editable.
            # Documentation owners can override LLM suggestions here.
            "title": it.get("title"),
            "description": it.get("description"),

            "filter_stage": it.get("filter_stage"),
            "filter_reason": it.get("filter_reason"),

            # Clarification fields make uncertainty explicit instead of hiding it.
            "needs_clarification": it.get("needs_clarification", False),
            "clarification_question": it.get("clarification_question"),
        }

        manifest["entries"].append(entry)

    return manifest

# Persist the review manifest as a readable, diff-friendly JSON file
# UTF-8 + pretty formatting for comfortable human editing and code review
def write_review_manifest(
    manifest: Dict[str, Any],
    output_path: Path,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
