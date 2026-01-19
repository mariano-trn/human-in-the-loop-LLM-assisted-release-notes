# src/rn/filtering.py
"""
Rule-based candidate filtering (deterministic stage).

Goal:
- Quickly exclude non user-facing changes (merge commits, bumps, chores, CI, tests, etc.)
- Classify obvious user-facing changes via Conventional Commits (feat -> feature, fix -> bugfix)
- Escalate anything uncertain to the LLM stage instead of guessing

Design principles:
- Determinism-first: prefer fast, reproducible heuristics over LLM calls.
- Conservative by default: ambiguous items are NOT forced into release notes.
- Explainability: every include/exclude has an explicit reason + confidence.
- Extensibility: exclude patterns and file signals are centralized and can be moved to config (YAML) later.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

# Minimal decision envelope used by the rules stage.
# LLM stage will produce richer fields (title/description/clarification_question).
@dataclass
class FilterDecision:
    include: bool
    reason: str
    stage: str  # "rules" or "llm"
    confidence: float = 0.75  # heuristic confidence


# ---------- Regex helpers ----------

MERGE_RE = re.compile(r"^Merge pull request\s+#\d+\s+", re.IGNORECASE)

# Conventional Commits are a high-signal heuristic for release note classification:
# - feat(...) -> user-facing feature
# - fix(...)  -> user-facing bug fix
# Anything else remains ambiguous and goes to LLM/HITL.
CONVENTIONAL_RE = re.compile(r"^(?P<type>[a-zA-Z]+)(\([^)]+\))?:\s+", re.IGNORECASE)

# Strong exclusion patterns: these are typically maintenance / internal / process changes.
# Kept intentionally broad to reduce noise in public release notes.
# (Can be externalized to config for production.)
DEFAULT_EXCLUDE_SUBJECT_PATTERNS = [
    r"\bbump\b",
    r"\bversion\b",
    r"\brelease\b",
    r"^chore\b",
    r"^refactor\b",
    r"^ci\b",
    r"^test\b",
    r"^build\b",
]

DEFAULT_EXCLUDE_BODY_PATTERNS = [
    r"\bbump\b",
    r"\bversion\b",
]

# File-path heuristics are "soft signals" only.
# We DO NOT exclude solely based on file paths because internal-looking changes can still affect users.
SOFT_INTERNAL_FILE_PATTERNS = [
    r"^\.github/",
    r"^docs?/",
    r"^test/",
    r".*_test\.go$",
]

# Strong include by type
INCLUDE_TYPES = {"feat": "feature", "fix": "bugfix"}


def _matches_any(patterns: List[str], text: str) -> Optional[str]:
    for p in patterns:
        if re.search(p, text, flags=re.IGNORECASE):
            return p
    return None


def _soft_internal_files_score(files: List[str]) -> float:
    """
    Returns a score in [0, 1] indicating how 'internal' the change looks from files.
    1 => mostly internal files. 0 => none internal.
    This is intentionally soft; we should not exclude just based on files.
    Note: this is used only as an annotation for downstream reasoning (LLM / reviewer), not as a hard rule.
    """
    if not files:
        return 0.0
    internal = 0
    for f in files:
        for p in SOFT_INTERNAL_FILE_PATTERNS:
            if re.search(p, f, flags=re.IGNORECASE):
                internal += 1
                break
    return internal / max(1, len(files))


def detect_type(subject: str) -> Optional[str]:
    """
    If subject follows conventional commits, return the type (feat/fix/etc).
    """
    m = CONVENTIONAL_RE.match(subject.strip())
    if not m:
        return None
    return m.group("type").lower()


def rule_based_filter(item: Dict[str, Any]) -> Tuple[Optional[FilterDecision], Dict[str, Any]]:
    """
    Returns (decision_or_none, annotations).
    If decision is None, item is ambiguous and should go to LLM stage.
    Output contract:
      - (FilterDecision, annotations) for decided items
      - (None, annotations) for ambiguous items to be handled by the LLM stage
    """
    subject = (item.get("subject") or "").strip()
    body = (item.get("body") or "").strip()
    files = item.get("files") or []

    annotations: Dict[str, Any] = {}

    # 1) Hard exclude: merge commits (typically not user-facing)
    if MERGE_RE.match(subject):
        # However, sometimes merge commit body has the actual PR title; we still exclude and rely on non-merge commit.
        return FilterDecision(False, "Excluded merge commit (not user-facing entry).", "rules", 0.95), annotations

    # 2) Conventional type signals
    ctype = detect_type(subject)
    annotations["conventional_type"] = ctype

    # 3) Hard exclude patterns (subject/body)
    p = _matches_any(DEFAULT_EXCLUDE_SUBJECT_PATTERNS, subject)
    if p:
        return FilterDecision(False, f"Excluded by subject pattern: {p}", "rules", 0.9), annotations

    p2 = _matches_any(DEFAULT_EXCLUDE_BODY_PATTERNS, body)
    if p2 and ("feat" not in (ctype or "") and "fix" not in (ctype or "")):
        # body contains bump/version and it's not clearly a feat/fix
        return FilterDecision(False, f"Excluded by body pattern: {p2}", "rules", 0.85), annotations

    # 4) Strong include: feat/fix
    if ctype in INCLUDE_TYPES:
        annotations["suggested_category"] = INCLUDE_TYPES[ctype]
        return FilterDecision(True, f"Included as {INCLUDE_TYPES[ctype]} (conventional commit).", "rules",
                              0.9), annotations

    # 5) Soft signals: file paths (do not exclude automatically)
    internal_score = _soft_internal_files_score(files)
    annotations["internal_files_score"] = internal_score

    # If it's heavily internal and not clearly user-facing, send to LLM
    # (still ambiguous because internal changes can be user-facing too)
    return None, annotations


def filter_candidates(
    items: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Apply rule-based filtering.
    Returns:
      - decided: items with include/exclude decision added
      - ambiguous: items to be sent to LLM stage
    Notes:
      - decided items include/exclude + reason + confidence + (optional) suggested category
      - ambiguous items are explicitly marked for the LLM stage
    """
    decided: List[Dict[str, Any]] = []
    ambiguous: List[Dict[str, Any]] = []

    for it in items:
        decision, annotations = rule_based_filter(it)
        it2 = dict(it)
        it2.update({"filter_annotations": annotations})

        if decision is None:
            it2.update({"include": None, "filter_reason": None, "filter_stage": "rules"})
            ambiguous.append(it2)
        else:
            it2.update(
                {
                    "include": decision.include,
                    "filter_reason": decision.reason,
                    "filter_stage": decision.stage,
                    "filter_confidence": decision.confidence,
                    "category": it2.get("category") or annotations.get("suggested_category"),
                    "title": it2.get("title"),
                    "description": it2.get("description"),
                }
            )
            decided.append(it2)

    return decided, ambiguous
