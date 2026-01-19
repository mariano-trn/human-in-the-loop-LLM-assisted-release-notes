1. Release Notes Automation (LLM-powered, Human-in-the-Loop)

This project implements an LLM-powered workflow for automating release notes
publication, with a clear Human-in-the-Loop (HITL) checkpoint and strong
guardrails for safety, auditability, and developer experience.


2. Goal

Automate the generation and publication of high-quality, user-facing release
notes by:

  1) Harvesting changes between two Git refs from a public GitHub repository
  2) Filtering out non user-facing noise (merges, bumps, chores, refactors, etc.)
  3) Classifying changes into features and bug fixes
  4) Using an LLM only where rules are insufficient
  5) Introducing an explicit Human-in-the-Loop review step
  6) Rendering release notes to Markdown
  7) (Bonus) Translating release notes into multiple languages
  8) (Bonus) Publishing them as an MkDocs site


3. Key Design Principles

3.1 Human-in-the-Loop is Explicit (not implicit)

- The workflow produces a review manifest (review.json) that acts as a contract
  between automation and human validation.
- LLMs never publish directly.
- Documentation owners can:
    -- Remove entries
    -- Edit titles and descriptions
    -- Fix categories
    -- Answer clarification questions
- Publishing reads only from the reviewed manifest.

3.2 Deterministic Publishing

All steps after the review manifest are fully deterministic:

- No LLM calls
- No heuristics
- No hidden logic

This makes publishing:

- CI-safe
- Reproducible
- Auditable

3.3 LLMs as Assistants, Not Authors

LLMs are used only for:

- Ambiguous classification
- Drafting titles and descriptions
- Translation

And always behind:

- A strict schema
- JSON-only responses
- Validation via Pydantic


4. High-level Architecture

Git repository
    ↓
harvest.py
    → commits between refs
    ↓
filtering.py
    → rule-based inclusion / exclusion
    ↓
filtering_llm.py
    → LLM used only for ambiguous cases
    ↓
schema.py
    → strict validation of LLM output
    ↓
review.py
    → review.json (Human-in-the-Loop checkpoint)
    ↓
render.py
    → deterministic Markdown
    ↓
translate.py
    → optional multi-language output
    ↓
mkdocs_publish.py
    → MkDocs site


5. Project Structure

.
├── main.py
├── .env
├── mkdocs.yml
├── docs/
│   ├── index.md
│   ├── release-notes.en.md
│   ├── release-notes.it.md
│   └── ...
├── outputs/
│   ├── review.json
│   ├── run.log
│   ├── draft_release_notes.md
│   ├── draft_release_notes.it.md
│   └── ...
└── src/
    └── rn/
        ├── harvest.py
        ├── filtering.py
        ├── filtering_llm.py
        ├── schema.py
        ├── review.py
        ├── render.py
        ├── translate.py
        ├── mkdocs_publish.py
        └── logging_utils.py


6. Configuration

Environment variables (.env):

- API_KEY=xxxx
- BASE_URL=xxxx
- TARGET_LANGS=en,it,fr,de
- HITL_ENFORCE=1

Variables:

API_KEY
  API key

TARGET_LANGS
  Comma-separated list of language codes

HITL_ENFORCE
  If set to 1, pauses execution after review.json generation


7. How to Run

7.1 Generate release notes

  python main.py

If HITL_ENFORCE=1, the pipeline will pause with:

  Edit outputs/review.json now, then press ENTER to continue publishing...


7.2 Review & edit

Open outputs/review.json.

You can:

- Change review_status
- Edit title and description
- Fix categories
- Answer clarification questions


7.3 Continue publishing

Press ENTER in the terminal.

Outputs:

- outputs/draft_release_notes.md
- outputs/draft_release_notes.<lang>.md
- docs/release-notes.<lang>.md
- mkdocs.yml


7.4 Build and serve MkDocs site

Choose either:

- mkdocs serve
- mkdocs build


8. Multi-language Support (Bonus)

- Controlled via TARGET_LANGS
- English is always the base language
- Only public sections are translated
- Internal workflow sections remain in English by design

Example:

  TARGET_LANGS=en,it,fr,de

Generates:

- release-notes.en.md
- release-notes.it.md
- release-notes.fr.md
- release-notes.de.md


9. LLM Guardrails

- Schema-first prompting (schema.py)
- JSON-only responses
- Strict validation via Pydantic
- Low temperature
- Token usage and latency logged

Example log entry:

  llm_call op=filter_ambiguous model=azure-oai-gpt-4.1 latency_ms=2240 usage={...}


Summary

This project demonstrates how LLMs can be safely embedded into a real release
workflow, enhancing productivity without sacrificing control, correctness, or
accountability.
