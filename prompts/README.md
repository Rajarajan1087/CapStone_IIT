# Prompt Library

Prompts are design artefacts, not throwaway strings. Each one is versioned, traced to
the requirement it serves, and changed only with a recorded reason.

## Register

| ID | Prompt | Category | Path | Version | Serves | Acceptance criteria |
|---|---|---|---|---|---|---|
| PR-01 | `requirement_to_spec` | Specification | `specification/requirement_to_spec_v1.0.txt` | 1.0 | All FRs (design-time) | — |
| PR-02 | `classify_ticket` | Build | `build/classify_ticket_v1.0.txt` | 1.0 | FR-02, FR-06 | A3 |
| PR-03 | `generate_answer` | Build | `build/generate_answer_v1.0.txt` | 1.0 | FR-05, FR-06, FR-12 | A6 |
| PR-04 | `escalation_summary` | Build | `build/escalation_summary_v1.0.txt` | 1.0 | FR-10 | Build Spec §03 "Route" |
| PR-05 | `groundedness_guardrail` | Build (validate) | `build/groundedness_guardrail_v1.0.txt` | 1.0 | FR-07 | A7 |
| PR-06 | `component_review` | Review | `review/component_review_v1.0.txt` | 1.0 | Traceability (design-time) | — |
| PR-07 | `answer_quality_judge` | Evaluation | `evaluation/answer_quality_judge_v1.0.txt` | 1.0 | Evaluation harness scoring | — |

## Versioning rule

The version number in the filename changes whenever the prompt text changes. The old
file is kept, not overwritten, so a result recorded in the decision log can always be
traced back to the exact prompt that produced it. Every decision log row records the
`prompt_version` in force at the time.

## Categories

| Category | Runs where | When |
|---|---|---|
| Specification | Design time, by the engineer | Before building, converting requirements into specs |
| Build | Inside the live system | Every ticket |
| Review | Design time, by the engineer | After each component is drafted |
| Evaluation | Inside the evaluation harness | During evaluation runs only, never in the live path |

## Injection defence

Every Build prompt that consumes customer text (PR-02, PR-03, PR-04) delimits that
text inside `<ticket>` tags and states explicitly that the content is untrusted data
rather than instructions, with a directive to disregard any embedded attempt to
redirect behaviour. PR-05 applies the same treatment to the drafted reply it reviews.
This is the FR-06 control.

## Guardrail layering

PR-05 is the *second* of two guardrail layers. A deterministic rule check
(`src/guardrails.py`) runs first and can block without a model call — cheaper, faster,
and unaffected by provider outage. PR-05 catches only what pattern matching cannot: a
fluent, plausible claim that no retrieved passage actually supports.

## Change history

| Date | Prompt | From → To | Reason |
|---|---|---|---|
| 2026-09-08 | all | — → 1.0 | Initial library, written from PRD v1.0 |
