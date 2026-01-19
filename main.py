# main.py
"""
Entry point / Orchestrator.

This file wires together the end-to-end workflow:
1) Harvest git changes between two refs (deterministic)
2) Rule-based filtering (deterministic, conservative)
3) LLM-assisted decision only for ambiguous items (guardrailed)
4) Materialize a review manifest (review.json) for human-in-the-loop edits
5) Render user-facing release notes (Markdown)
6) Optional translation into multiple languages
7) Publish as a MkDocs static site (docs/ + mkdocs.yml)

Design principle:
- "Determinism-first": call the LLM only where semantic interpretation is required.
- "HITL by design": review.json is the source of truth for what gets published.
- "Observability": token usage and latency are logged in rn.llm.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Tuple

from dotenv import load_dotenv

from rn.harvest import harvest_changes
from rn.filtering import filter_candidates
from rn.filtering_llm import llm_decide_ambiguous
from rn.review import build_review_manifest, write_review_manifest
from rn.render import load_review_manifest, render_release_notes_markdown, write_markdown
from rn.logging_utils import setup_logging
from rn.translate import split_public_and_internal, translate_public_markdown
from rn.mkdocs_publish import publish_release_notes_pages, write_mkdocs_yml, ensure_index_page

# Load configuration from .env (API keys, target languages, HITL toggle).
# This keeps secrets out of the codebase and makes the workflow portable across environments.
load_dotenv()


def parse_target_langs() -> Dict[str, str | None]:
    """
    Returns a dict like:
      {"en": None, "it": "Italian", "fr": "French"}
    Source: env TARGET_LANGS (comma separated codes), default: "en,it"
    Notes:
      - Values are language names passed to the translator (LLM). Unknown codes fallback to the code itself.
      - English ("en") is always enforced as the base (non-translated) page.
    """
    raw = os.environ.get("TARGET_LANGS", "en,it").strip()
    codes = [c.strip().lower() for c in raw.split(",") if c.strip()]
    if not codes:
        codes = ["en", "it"]

    # Map language codes -> language names for the translator
    # (For 'en' we don't translate, so value is None)
    # NOTE: Extend this map to add more supported languages.
    name_map = {
        "en": None,
        "it": "Italian",
        "fr": "French",
        "de": "German",
        "es": "Spanish",
    }

    out: Dict[str, str | None] = {}
    for c in codes:
        out[c] = name_map.get(c, c) if c != "en" else None
    # Ensure 'en' is always present (base page)
    if "en" not in out:
        out = {"en": None, **out}
    return out


def join_translated_with_internal(translated_public: str, internal_md: str | None) -> str:
    if not internal_md:
        return translated_public.rstrip() + "\n"
    # do not add extra '---' (renderer already adds it)
    return translated_public.rstrip() + "\n\n" + internal_md.lstrip()


def normalize_review_status(items: List[Dict[str, Any]]) -> None:
    """
    Ensure each item has a review_status:
      - included
      - excluded
      - needs_clarification
    Mutates items in place (ok for pipeline assembly).
    Why:
      Some stages produce include/exclude/clarify decisions in different shapes.
      This function normalizes everything to a single review_status field to simplify downstream review/publish.
    """
    for it in items:
        if "review_status" in it and it["review_status"]:
            continue

        include = it.get("include")
        needs_clarification = bool(it.get("needs_clarification"))

        if needs_clarification:
            it["review_status"] = "needs_clarification"
        elif include is True:
            it["review_status"] = "included"
        elif include is False:
            it["review_status"] = "excluded"
        else:
            it["review_status"] = "needs_clarification"


def run_pipeline(
    repo_url: str,
    from_ref: str,
    to_ref: str,
    cache_dir: Path,
    include_files: bool = True,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Returns: (decided, ambiguous, all_items)
    Pipeline strategy:
      - Apply deterministic rules first (fast, cheap, reproducible).
      - Escalate only truly ambiguous items to the LLM (cost-aware).
    """
    items = harvest_changes(
        repo_url=repo_url,
        from_ref=from_ref,
        to_ref=to_ref,
        cache_dir=cache_dir,
        include_files=include_files,
    )

    decided, ambiguous = filter_candidates(items)

    # Only call LLM if there is something ambiguous
    llm_results: List[Dict[str, Any]] = []
    if ambiguous:
        llm_results = llm_decide_ambiguous(ambiguous)

    all_items: List[Dict[str, Any]] = []
    all_items.extend(decided)
    all_items.extend(llm_results)

    normalize_review_status(all_items)

    return decided, ambiguous, all_items


def main() -> int:
    setup_logging(level="INFO", log_file=Path("outputs/run.log"))

    repo_url = "https://github.com/getlago/lago"
    from_ref = "v1.24.0"
    to_ref = "v1.25.0"
    cache_dir = Path(".cache")

    try:
        decided, ambiguous, all_items = run_pipeline(
            repo_url=repo_url,
            from_ref=from_ref,
            to_ref=to_ref,
            cache_dir=cache_dir,
            include_files=True,
        )
    except Exception as e:
        print("\nERROR:", str(e), file=sys.stderr)
        return 1

    print(f"\nHarvested items total: {len(decided) + len(ambiguous)}")
    print(f"Decided by rules:      {len(decided)}")
    print(f"Ambiguous (LLM stage): {len(ambiguous)}")
    print(f"All items total:       {len(all_items)}")

    # Quick sample output (safe)
    if ambiguous:
        print("\n--- Sample ambiguous decision (post-LLM) ---")
        llm_samples = [x for x in all_items if x.get("filter_stage") == "llm"]
        if llm_samples:
            s = llm_samples[0]
            print("subject:", s.get("subject"))
            print("include:", s.get("include"))
            print("review_status:", s.get("review_status"))
            print("category:", s.get("category"))
            print("title:", s.get("title"))
            print("description:", s.get("description"))
            print("needs_clarification:", s.get("needs_clarification"))
            print("clarification_question:", s.get("clarification_question"))
            print("reason:", s.get("filter_reason"))

    manifest = build_review_manifest(
        repo_url=repo_url,
        from_ref=from_ref,
        to_ref=to_ref,
        items=all_items,
    )

    write_review_manifest(
        manifest,
        Path("outputs/review.json"),
    )

    print("\nReview manifest written to outputs/review.json")

    # --- Human-in-the-loop gate (optional but enforceable) ---
    # The workflow is HITL-ready by design because publication always reads from review.json.
    # To make the HITL step explicit during evaluation, we can force an interactive pause.
    # This is intentionally controlled via HITL_ENFORCE to avoid blocking CI/non-interactive runs.
    if os.environ.get("HITL_ENFORCE", "0") == "1":
        print("\nHuman-in-the-loop enforced.")
        input("Edit outputs/review.json now, then press ENTER to continue publishing...")

    manifest = load_review_manifest(Path("outputs/review.json"))
    md = render_release_notes_markdown(manifest)
    write_markdown(md, Path("outputs/draft_release_notes.md"))
    print("Draft release notes written to outputs/draft_release_notes.md")

    # --- Multi-language pages generation ---
    targets = parse_target_langs()
    run_id = str(uuid.uuid4())

    public_md, internal_md = split_public_and_internal(md)

    pages_by_lang: Dict[str, str] = {}
    pages_by_lang["en"] = md  # base

    # --- Multi-language publishing ---
    # We translate only the public part of the release notes. The internal section is left in English
    # because it's a workflow artifact for internal collaboration (not user-facing documentation).

    for code, lang_name in targets.items():
        if code == "en":
            continue
        translated_public = translate_public_markdown(
            public_md,
            target_language=str(lang_name),
            model="azure-oai-gpt-4.1",
            run_id=run_id,
        )
        pages_by_lang[code] = join_translated_with_internal(translated_public, internal_md)

        # also write to outputs for convenience
        write_markdown(pages_by_lang[code], Path(f"outputs/draft_release_notes.{code}.md"))

    # Always write Italian example file (if present) already handled above by loop
    if "it" in pages_by_lang:
        print("Translated release notes written to outputs/draft_release_notes.it.md")

    # --- MkDocs publishing ---
    # docs/ contains the Markdown sources, while `mkdocs build` produces a static site under site/.
    # mkdocs.yml is generated to keep navigation consistent with the configured target languages.
    ensure_index_page(Path("docs"))
    publish_release_notes_pages(pages_by_lang=pages_by_lang, docs_dir=Path("docs"), basename="release-notes")
    write_mkdocs_yml(language_codes=list(pages_by_lang.keys()), out_path=Path("mkdocs.yml"), basename="release-notes")
    print("MkDocs pages written to docs/ and mkdocs.yml updated")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())