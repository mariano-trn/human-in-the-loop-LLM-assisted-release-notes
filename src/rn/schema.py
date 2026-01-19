# src/rn/schema.py
"""
LLM output contract (schema).

Purpose:
- Define a strict, machine-validated contract for all LLM decisions.
- Prevent free-form or unsafe outputs from influencing publication.
- Act as a guardrail between probabilistic LLM output and deterministic code.

Design principles:
- Explicit fields instead of implicit assumptions
- Validation via Pydantic before any downstream use
- Schema-first prompting: the LLM is instructed to conform to this model
"""
from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Optional, Literal

# Only user-facing categories are allowed.
# Internal or ambiguous changes must not invent new categories.
Category = Literal["feature", "bugfix"]

# Canonical representation of an LLM decision.
# Any LLM output that does not conform to this schema is rejected.
class LLMDecision(BaseModel):

    # Master switch: controls whether the entry can ever be published
    include: bool = Field(..., description="Whether this change should appear in user-facing release notes.")

    # Category is constrained and only meaningful if include=true
    category: Optional[Category] = Field(None, description="Required if include=true.")

    # Short, scannable title written for end users (not developers)
    title: Optional[str] = Field(None, description="User-facing short title. Required if include=true.")

    # Concise description optimized for release notes browsing
    description: Optional[str] = Field(None, description="User-facing concise description. Required if include=true.")

    # Explicit signal for uncertainty instead of forced classification
    needs_clarification: bool = Field(False, description="True if info is insufficient or ambiguous.")

    # Single concrete question to unblock human review
    clarification_question: Optional[str] = Field(None, description="Question to ask the author if needs_clarification=true.")

    # Mandatory reasoning for transparency and auditability
    reason: str = Field(..., description="Short reasoning for include/exclude and categorization.")
