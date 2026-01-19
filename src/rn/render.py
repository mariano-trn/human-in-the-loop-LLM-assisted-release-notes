# src/rn/render.py
"""
Release notes renderer (deterministic).

Purpose:
- Transform the reviewed manifest (review.json) into user-facing Markdown.
- Keep the rendering step deterministic and side-effect free to preserve trust:
  the LLM never publishes directly; it only suggests metadata upstream.

Output structure:
- Features
- Bug Fixes
- Optional internal section ("Needs clarification") for documentation owner workflow

Design choices:
- Titles/descriptions come from the manifest when available (LLM or human edited).
- Conservative fallbacks are used if missing (still readable).
- Includes commit URL as a "details" link when available.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime

# The manifest is the "single source of truth" for publication.
# This makes the human-in-the-loop step explicit and auditable.
def load_review_manifest(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

# Heuristic: turn conventional commit subjects into a cleaner, user-facing title.
# This is used only as a fallback when title is missing in the manifest.
def _clean_title_from_subject(subject: str) -> str:

    # Remove conventional prefix like "feat(scope): " / "fix: "
    s = subject.strip()

    # We strip only well-known prefixes to avoid accidentally deleting meaningful text.
    for prefix in ["feat", "fix", "chore", "refactor", "misc", "ci", "test", "build"]:
        # handle "type(scope): " and "type: "
        if s.lower().startswith(prefix + "(") or s.lower().startswith(prefix + ":"):
            # split after first ":"
            parts = s.split(":", 1)
            if len(parts) == 2:
                return parts[1].strip()
    # Remove "Merge pull request ..." etc (fallback)
    return s

# Conservative fallback description (keeps release notes readable even if LLM fields are missing).
def _fallback_description(entry: Dict[str, Any]) -> str:
    # Conservative fallback if LLM text missing (still readable)
    subject = entry.get("subject") or ""
    return f"Includes change: {subject}"

def render_release_notes_markdown(manifest: Dict[str, Any]) -> str:
    """
    Render release notes from the reviewed manifest.

    Determinism note:
    - This function performs no network calls and no LLM calls.
    - It simply formats reviewed data for publication.
    """
    meta = manifest.get("metadata", {})
    entries: List[Dict[str, Any]] = manifest.get("entries", [])

    repo = meta.get("repo", "")
    from_ref = meta.get("from_ref", "")
    to_ref = meta.get("to_ref", "")
    generated_at = meta.get("generated_at", "")

    # Only entries explicitly marked "included" are published.
    included = [e for e in entries if e.get("review_status") == "included"]

    # Categories are strict: feature / bugfix (enforced upstream by rules + schema + HITL).
    features = [e for e in included if e.get("category") == "feature"]
    bugfixes = [e for e in included if e.get("category") == "bugfix"]

    # Prefer human/LLM curated title; fallback to cleaned subject for readability.
    def fmt_entry(e: Dict[str, Any]) -> str:
        title = (e.get("title") or "").strip()
        if not title:
            title = _clean_title_from_subject(e.get("subject") or "Untitled change")
        desc = (e.get("description") or "").strip()

        # Prefer curated description; fallback to a conservative "Includes change: ..." line.
        if not desc:
            desc = _fallback_description(e)

        author = e.get("author") or "Unknown"
        url = e.get("url")
        link = f" ([details]({url}))" if url else ""

        return f"- **{title}**{link}\n  - {desc}\n  - Author: {author}\n"

    lines: List[str] = []
    lines.append(f"# Release Notes\n")
    if repo or from_ref or to_ref:
        lines.append(f"_Repository: {repo}_\n")
        lines.append(f"_Changes: {from_ref} → {to_ref}_\n")
    if generated_at:
        lines.append(f"_Generated at: {generated_at}_\n")

    lines.append("## Features\n")
    if features:
        for e in features:
            lines.append(fmt_entry(e))
    else:
        lines.append("_No user-facing features detected._\n")

    lines.append("## Bug Fixes\n")
    if bugfixes:
        for e in bugfixes:
            lines.append(fmt_entry(e))
    else:
        lines.append("_No user-facing bug fixes detected._\n")

    # Internal section is intentionally separated and clearly labeled.
    # It is useful for documentation owners but is NOT intended for end users.
    needs_clar = [e for e in entries if e.get("review_status") == "needs_clarification"]
    if needs_clar:
        lines.append("\n---\n")
        lines.append("## Needs clarification (internal)\n")
        for e in needs_clar:
            q = e.get("clarification_question") or "Clarification needed."
            lines.append(f"- {e.get('subject')}\n  - Question: {q}\n  - Author: {e.get('author')}\n")

    return "\n".join(lines)

# Small helper: ensures output directory exists and writes UTF-8 Markdown.
def write_markdown(text: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
