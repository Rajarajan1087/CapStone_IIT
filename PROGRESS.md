# Capstone Progress Tracker

**Project:** CloudServe Solutions support system — IIT Roorkee FDE capstone
**Repo:** https://github.com/Rajarajan1087/CapStone_IIT
**Local:** `C:\Users\Rajarajan\Documents\FDE_Capstone_Complete\CapStone_IIT`
**Pack:** `..\Capstone_Pack\` (siblings folder — templates, datasets, reference docs)

| Key date | |
|---|---|
| Review milestone (coordinator) | **15 Sept 2026** |
| Submission deadline | **20 Sept 2026, 23:59** |
| Last updated | 8 Sept 2026 |

---

## Where things stand

| Stage | Status | Artefact |
|---|---|---|
| 1 — Discovery | ✅ Done | `docs/workbooks/Stage_1_Discovery_Workbook_Completed.docx` |
| 2 — Requirements (PRD v1) | ✅ Done | `docs/workbooks/Stage_2_PRD_v1.docx` |
| 3 — Prompt library | ✅ Done | `docs/workbooks/Stage_3_Prompt_Library_Completed.docx` + `prompts/` |
| 4 — Sprint plan | ✅ Done | `docs/workbooks/Stage_4_Sprint_Plan_Completed.docx` |
| **Build (B-01 → B-11)** | ⬜ **NOT STARTED — resume here** | `src/` stubs are empty |
| 5 — PRD revision + log | ⬜ Blocked on gate | Phase B |
| Governance framework | ⬜ Not started | Phase B |
| Report / video / package | ⬜ Not started | Phase B |

**Repo scaffold is done and verified:** folder structure, `evaluation/harness.py` (CLI contract proven against an arbitrary input path), `src/logging_store.py` (SQLite decision log, schema verified), `tests/test_harness_smoke.py`, datasets in `data/`.

---

## The finding everything rests on

Do not relitigate this — it is evidenced in Discovery §1 and §2:

| Figure | Value | Why it matters |
|---|---|---|
| Tickets answerable from existing docs | **70.7%** (410/580) | They do not lack answers |
| Historical escalations that were doc-answerable | **47.5%** (154/324) | Escalation is a *findability/confidence* failure, not a difficulty one |
| FCR baseline | 44.1% | Matches Marcus's own 42% estimate |
| Escalation rate baseline | 55.9% | |
| CSAT: escalated vs not | 2.56 vs 3.38 | Escalation itself drives dissatisfaction |
| Repeat contact: escalated vs not | 38.6% vs 0% | |
| `must_not_auto_respond` share | 17.4% (101/580) | 4 intents, 100% historically escalated |

**Problem statement (one line):** CloudServe doesn't lack answers — it lacks a way to surface the right answer at first contact with enough confidence to act on. So this is **not built as a chatbot**.

---

## Decisions already made (don't re-decide)

| Decision | Rationale |
|---|---|
| Not a conversational chatbot | Discovery evidence; recorded as out-of-scope in PRD §6 |
| Retrieve **only** from the 29 reviewed KB articles | Never from agent history / personal snippet files — Daniel's staleness risk |
| Guardrail is **two layers**: deterministic rules first, then model (PR-05) | A block must not depend on the provider being up (A11); also cheaper |
| Never-automate list is **code, not a prompt** (FR-08) | So no injection can override it |
| 4 never-automate intents | `security_incident`, `compliance_request`, `feature_request`, `unclear_request` |
| Prompts versioned in filenames, superseded files retained | So any decision-log row traces to exact prompt text |
| Harness takes `--input`/`--output` | Hidden 120-ticket set is never distributed (A9) |

**Known weakness documented, not hidden:** PR-03 (drafter) and PR-05 (judge) share a model family → correlated failure risk. Mitigated by the deterministic first layer + "block when uncertain".

---

## Resume here — Phase A build plan

Capacity ~31h across 8 days. Estimates assume Stage 3 prompts are already written (they are — that's why classifier/generator/guardrail are 3h not 4h).

| Day | Hrs | Target | Items |
|---|---|---|---|
| Tue 8 | 2.5 | Env + live model call | B-01, start B-02 |
| Wed 9 | 2.5 | 4-channel ingest ✓ *Day 1 checkpoint* | B-02 |
| Thu 10 | 2.5 | Corpus chunked + embedded | B-03 |
| Fri 11 | 2.5 | Retrieval traceable ✓ *Day 2 checkpoint* | B-04, start B-06 |
| **Sat 12** | **8** | Classify + route + log ✓ *Day 3* | B-06, B-07, B-10, start B-08 |
| **Sun 13** | **8** | Generate + guardrails + metrics ✓ *Day 4* | B-08, B-09, B-05 |
| **Mon 14** | 2.5 | 🔶 **GATE CLEARS** | B-11 |
| Tue 15 | 2.5 | Tests, clean checkout, fairness → review-ready | B-18, B-19, B-14 |

**Hard rule:** if Sat 12 ends without routing + logging working, cut intent-class coverage — never move the gate.

**Cut order if short:** 1) Grafana dashboards 2) 22 classes → top classes + fallback 3) CI pipeline 4) fairness narrative 5) second video take.
**Never cut:** B-11 gate, B-10 decision log, B-09 guardrail, B-18 tests, B-16 PRD revision, B-19 clean checkout.

---

## Blockers / open items

| Item | Status | Note |
|---|---|---|
| OpenRouter API key | ⬜ Needed for B-01 | Free tier. Goes in `.env` (already gitignored). Build can start offline-first without it — ingest, chunking, retrieval and all deterministic layers need no model call. |
| GitHub push token | ⬜ Revoked | Fine-grained PAT needs **Contents: Read and write**. Commits are landing locally either way. |

## Open questions carried from PRD §9

1. Is CSAT measurable at all here, or does it need a proxy? → decide before the gate run
2. Confidence threshold value — one global, or per intent class? → decide during B-07
3. Does chunking strategy materially change retrieval hit rate? → test during B-03
4. Does the enterprise escalation finding (60.4%, highest of all tiers) change routing, or only fairness reporting? → decide before Stage 5 revision
5. Escalation summary delivery — logged only, or does it need a UI? → decide at B-08

---

## The 12 acceptance criteria — tracker

| # | Criterion | Status |
|---|---|---|
| A1 | Runs from clean checkout via README | ⬜ B-19 |
| A2 | Four channels ingested + normalised | ⬜ B-02 |
| A3 | Classified w/ numeric confidence | ⬜ B-06 |
| A4 | Retrieval returns identifiable passages | ⬜ B-04 |
| A5 | Routing deterministic on threshold | ⬜ B-07 |
| A6 | Citations resolve to retrieved passages | ⬜ B-08 |
| A7 | Guardrail blocks when triggered | ⬜ B-09 |
| A8 | Every decision logged, reconciles | ⬜ B-10 |
| A9 | Full set processed unattended | ⬜ **B-11 — THE GATE** |
| A10 | Metrics report auto-produced | 🟡 harness emits structure, needs real figures (B-05) |
| A11 | Handles failure w/o crashing | 🟡 harness catches per-ticket errors; provider-outage path pending |
| A12 | Tests pass via one command | 🟡 smoke test passes; suite pending (B-18) |
