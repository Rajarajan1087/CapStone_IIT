# Capstone Progress Tracker

**Project:** CloudServe Solutions support system — IIT Roorkee FDE capstone
**Repo:** https://github.com/Rajarajan1087/CapStone_IIT
**Local:** `C:\Users\Rajarajan\Documents\FDE_Capstone_Complete\CapStone_IIT`

| Key date | |
|---|---|
| Coordinator review | **15 Sept 2026** |
| Submission deadline | **20 Sept 2026, 23:59** |
| Phase A status | **COMPLETE — the gate has cleared** |

---

## Stage status

| Stage | Status |
|---|---|
| 1 — Discovery | Done |
| 2 — Requirements (PRD v1) | Done |
| 3 — Prompt library | Done |
| 4 — Sprint plan | Done |
| **Phase A build (B-01 → B-19)** | **Done — gate cleared** |
| 5 — PRD revision + log | **Done — Wed 16 Sept** |
| Governance framework + kill switch | **Done — Wed 16 Sept** (pulled forward from Thu 17) |
| Report (30pp PDF) | **Done — Thu 17 to Fri 18 Sept** |
| Effort log + PRD v2.0 document | **Done — Fri 18 Sept** |
| Video | Sat 19 Sept |
| Package + submit | Sun 20 Sept |

---

## Phase B log

### Wed 16 Sept — Stage 5 revision and governance
- PRD v2.0 issued: **4 requirements changed** (FR-03, FR-04, FR-07, NFR-02),
  **2 added** (FR-13 independent safety net, FR-14 kill switch), 0 removed.
- `Stage_5_PRD_Revision_Log_Completed.docx` — every entry names a measurement,
  not an opinion.
- `Governance_Framework_Completed.docx` — 9-risk register with a named control
  and file for each, fairness audit, 6-step incident procedure, declaration.
- **New code:** `src/kill_switch.py` + `tests/test_kill_switch.py` (8 tests).
  Writing the governance doc found the gap; the control was built before the
  document claimed it existed. Test count 62 → **78**.

### Thu 17 – Fri 18 Sept — The report
- `RajarajanVenkatesan_Capstone_Report.pdf` — **30 pages**, prescribed section
  order, 5 numbered figures, 5 appendices.
- AI-use declaration placed on **page 2**, before the contents.
- All three fairness gaps reported in full, with an action for each.
- Found while writing Appendix B: `rate_limit` classifies at **F1 0.38**, the
  weakest class, confusing both ways with `quota_or_overage`. Not a safety
  issue; recorded as the clearest available improvement.

### Fri 18 Sept — Effort log and PRD v2.0
- `RajarajanVenkatesan_Effort_Log.pdf` — 6 pages, assembled from the daily
  records in this file and LEARNING_LOG.md, not from memory.
  **79.5 hrs against 60.5 planned (+19.0).** Week and stage totals reconcile.
  - Routing: 2.5 est → **6.5 actual**. The estimate was "derive a threshold";
    the reality was discovering no threshold can carry the decision.
  - The gate: 4.0 est → **1.0 actual**. No debugging needed, because the
    harness and offline path were already right.
  - Provider access: **4.0 hrs, nothing assessable.** Recorded, not hidden.
- `Stage_2_PRD_v2.pdf` — 6 pages. Amber rows revised, green rows new, so a
  reviewer sees what moved without opening v1.0.
  - 4 assumptions tested: 2 held, 2 failed. The enterprise one failed usefully
    — the tier-specific threshold was deleted and moved to out-of-scope.
  - 5 open questions from v1.0: 4 resolved, 1 deferred. 3 new ones added.
  - The threshold-per-class question **dissolved** rather than being answered:
    gate one excludes classes before confidence is read.
- `docs/VIDEO_DEMO_TICKETS.md` — verified ticket IDs for Saturday's recording.

### Demo tickets for the video (verified against the gate run)

| Show | Ticket | Why this one |
|---|---|---|
| Success | `DEV-0017` | conf 0.88, two citations |
| Escalation, gate 1 | `DEV-0003` | compliance_request — refused before any score is read |
| Escalation, gate 2 | `DEV-0132` | classifier said routine API question; keyword net caught "former employee" |
| Guardrail block | `DEV-0073` | forbidden_commitment — 1 of only 6 blocks in 580 |

---

## The 12 acceptance criteria

| # | Criterion | Status |
|---|---|---|
| A1 | Runs from clean checkout via README | **Pass** — verified in an empty directory |
| A2 | Four channels ingested and normalised | **Pass** — 580/580, zero warnings |
| A3 | Classified with numeric confidence | **Pass** — 80.7% accuracy |
| A4 | Retrieval returns identifiable passages | **Pass** — all doc_ids resolve |
| A5 | Routing deterministic | **Pass** — verified by repeat runs |
| A6 | Citations resolve to retrieved passages | **Pass** — 92.7% citation accuracy |
| A7 | Guardrail blocks when triggered | **Pass** — and needs no model call |
| A8 | Every decision logged, reconciles | **Pass** — 2,490 records |
| A9 | Full set processed unattended | **Pass — THE GATE** |
| A10 | Metrics report auto-produced | **Pass** |
| A11 | Handles failure without crashing | **Pass** — incl. total provider outage |
| A12 | Tests pass via one command | **Pass** — 62 tests |

---

## Gate run results (580 tickets)

| Measure | Result | Baseline |
|---|---|---|
| Tickets processed | 580 | — |
| Processing errors | 0 | — |
| Classification accuracy | 80.7% | — |
| Routing accuracy | 69.7% | — |
| Citation accuracy | 92.7% | — |
| First contact resolution | 47.6% | 44.1% |
| Escalation rate | 36.4% | 55.9% |
| Never-automate violations | **0 of 101** | — |
| Runtime | 2.7 s | — |

Run without model access, on the deterministic path only.

---

## Fairness findings (need explanation in the report)

| Finding | Gap |
|---|---|
| Europe vs Latin America auto-answer rate | 10.8 pts (70.5% vs 59.7%) |
| Enterprise vs standard auto-answer rate | 8.2 pts (69.2% vs 61.0%) |
| Non-fluent guardrail block rate | 3x (2.2% vs 0.7%) |

---

## Decisions settled — do not relitigate

| Decision | Why |
|---|---|
| Not a chatbot | 70.7% of tickets already answerable from docs |
| No external vector DB | 145 passages; pure-Python TF-IDF is faster to run and to reason about |
| Section-aware chunking | 87.7% vs 85.4% hit@1, and never splits a repair sequence |
| Answerability decided by intent, not score | Retrieval precision caps at ~78% regardless of threshold |
| Confidence threshold 0.45 | Zero safety leaks; keeps margin for the model path |
| Never-automate list in code, not prompt | So no injection can widen it |
| Guardrail deterministic-first | A block must not need the provider online |
| Circuit breaker after 3 failures | Without it an outage makes the run take hours |

---

## Known limitations (state these, do not hide them)

- Search matches words, not meanings; synonym expansion bridges common cases.
- Routing accuracy 69.7% — most shortfall is over-escalation, the safe direction.
- The live-model path is built and tested but has never run against a real key.
  Every number above is from the deterministic path.
- Three fairness gaps need investigation, not a patch.

---

## For Phase B

Bring `evaluation/results/gate/metrics_report.json` — it contains every figure
the report needs, including the fairness segmentation.

Read `LEARNING_LOG.md` for the narrative of what was built and why.
