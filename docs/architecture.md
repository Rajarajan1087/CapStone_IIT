# Architecture

> Draft this once discovery and requirements v1 are complete — the shape of
> the pipeline follows from the problem statement, not the other way round.
> Placeholder structure below matches the Build Specification's component
> list (`03 What each component must do`).

## Pipeline stages

1. **Ingest** — normalise tickets from email, chat, docs_comment, forum into one internal representation.
2. **Classify** — intent + urgency + numeric confidence, with a defined fallback.
3. **Retrieve** — search `documentation.json` corpus, return ranked passages with resolvable IDs, threshold-gated.
4. **Route** — deterministic auto_respond / escalate decision from a data-derived threshold, reasoned and logged.
5. **Generate** — grounded, cited answer; explicit "don't know" path; ticket text isolated from system instructions.
6. **Validate** — guardrails that can block (not just warn), checked on every response.

## Decision log

Every stage writes to `storage/decisions.db` via `src/logging_store.py`
(schema: decision_id, created_at, ticket_id, stage, prediction, confidence,
threshold, action_taken, reason, sources_used, guardrails, prompt_version,
requirement_ids).

## Open design decisions to record here once made

- Chunking strategy for the documentation corpus, and why.
- Confidence threshold for auto-respond vs escalate, and how it was derived from data.
- Which guardrail(s) are implemented and what each blocks.
- How the system behaves when the model provider is unavailable (A11).
