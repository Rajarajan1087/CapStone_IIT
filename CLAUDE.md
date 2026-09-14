# CloudServe Support System — working agreement

IIT Roorkee FDE capstone. Read `PROGRESS.md` first for current state and the
day-by-day plan. This file states the constraints that must not be
relitigated and the rules that decide how work is done here.

---

## The one thing to understand about this project

The client asked for a chatbot. Discovery established they should not get
one. **70.7%** of tickets are already answerable from CloudServe's own
29-article knowledge base, and **47.5%** of historical escalations were
tickets that *had* a documented answer.

The problem is **findability and confidence at first contact**, not a
shortage of knowledge. Every design decision follows from that. If a
proposed change only makes sense for a conversational assistant, it is
wrong for this system.

---

## Non-negotiable constraints

These are settled. Do not re-open them without explicit instruction.

| Constraint | Why |
|---|---|
| Retrieve **only** from the 29 reviewed KB articles in `data/documentation.json` | Agents' personal answer files are unreviewed and known to contain stale answers. Learning from them would scale an existing error. |
| The never-automate list is **code, not a prompt** (`FR-08`) | So no prompt injection can override it. Hard-coded, not model-mediated. |
| Guardrails are **two layers**: deterministic rules first, model second | A block must not depend on the provider being reachable (A11). Cheaper and faster too. |
| Retrieval returns **nothing** rather than a weak match | Always returning something hides failure. An empty result is a correct outcome. |
| Generation states **"I don't know"** rather than filling a gap | The client's single defined failure condition is a confidently wrong answer reaching a customer. |
| Prompts are **versioned files** in `prompts/`, never inline strings | Version lives in the filename; superseded files are retained so any decision-log row traces to exact prompt text. |
| The harness takes `--input` / `--output` **paths** | The hidden 120-ticket grading set is never distributed. A hardcoded path fails A9 outright. |
| Nothing in the repo references an AI assistant | Commits are authored by Rajarajan Venkatesan only. The AI-use declaration goes in the report, per Submission Guide §09 — not in code or commit metadata. |

**The four never-automate intents:** `security_incident`, `compliance_request`,
`feature_request`, `unclear_request`. All 101 such tickets in the dataset were
escalated by humans historically. Auto-responding to one is a governance
failure, not a scoring loss.

---

## How to work in this repo

**Verify, don't assume.** Reading code is not running it. Every component gets
run against the real 580-ticket dataset before it is called done. A stage that
has only been reasoned about is not finished.

**Never break the unattended run.** No component may raise on a single bad
ticket. Malformed input produces a warning and a safe default; the run
continues. One unusable record in a file of 120 must not end the run (A9/A11).

**Escalate on uncertainty, never guess.** When confidence is low, retrieval is
empty, or the model is unavailable, the answer is "escalate to a human". That
is the safe default and it is always acceptable.

**Small, verified steps.** Build one component, test it against real data,
show the result, then move on. Do not write three modules before running any
of them.

**Cut scope, never the gate.** If time runs short, reduce intent-class
coverage or drop monitoring. Never postpone the full unattended run. A system
handling a reduced set properly and running unattended passes; a broad,
fragile one does not.

---

## Architecture

```
ingest → classify → retrieve → route → generate → validate
                                  ↓
                         decision log (every stage)
```

| Module | Serves | Criterion | State |
|---|---|---|---|
| `src/config.py` | all | — | done |
| `src/ingest.py` | FR-01 | A2 | done |
| `src/model_client.py` | NFR-02 | A11 | done |
| `src/retrieve.py` | FR-03 | A4 | **next** |
| `src/classify.py` | FR-02 | A3 | to do |
| `src/route.py` | FR-04, FR-08 | A5 | to do |
| `src/generate.py` | FR-05, FR-06 | A6 | to do |
| `src/guardrails.py` | FR-07 | A7 | to do |
| `src/logging_store.py` | FR-09 | A8 | scaffold done, needs wiring |
| `evaluation/harness.py` | FR-11 | A9, A10 | CLI proven, metrics are placeholders |

Prompts for classify, generate, escalation-summary and the guardrail are
**already written** in `prompts/build/`. Load them from file; do not rewrite
them inline.

---

## Commands

```bash
python -m scripts.check_setup                    # day-one verification
python -m pytest tests/ -v                       # test suite (A12)
python -m evaluation.harness --input data/validation_tickets.json \
                             --output evaluation/results/    # the gate (A9)
```

---

## Config

All settings load through `src/config.py` from `.env`. Nothing else reads
`os.environ` directly. Relative paths resolve against the project root — this
is deliberate: Chroma silently returns zero results when written and read from
different working directories, and anchoring paths here prevents that.

The system runs **without** an API key, in a degraded mode where tickets
needing a model opinion are escalated. Offline development and CI depend on
this, so do not introduce a hard requirement for a key.

---

## The 12 acceptance criteria

Pass/fail, no partial credit. Checked before any document is read.

| # | Criterion | Status |
|---|---|---|
| A1 | Runs from a clean checkout via the README | pending |
| A2 | Four channels ingested and normalised | **done** |
| A3 | Classified with numeric confidence | pending |
| A4 | Retrieval returns identifiable source passages | pending |
| A5 | Routing deterministic against a threshold | pending |
| A6 | Citations resolve to retrieved passages | pending |
| A7 | A guardrail blocks when triggered | pending |
| A8 | Every decision logged and reconciles | pending |
| A9 | Full set processed unattended | **the gate** |
| A10 | Metrics report produced automatically | partial |
| A11 | Handles failure without crashing | partial |
| A12 | Tests pass via one documented command | partial (33 passing) |

---

## Key figures (measured, not estimated)

Use these; do not recompute or contradict them.

| Metric | Value |
|---|---|
| Tickets answerable from docs | 70.7% (410/580) |
| Escalations that were doc-answerable | 47.5% (154/324) |
| First-contact resolution baseline | 44.1% |
| Escalation rate baseline | 55.9% |
| CSAT: escalated vs not | 2.56 vs 3.38 |
| Repeat contact: escalated vs not | 38.6% vs 0% |
| `must_not_auto_respond` share | 17.4% (101/580) |
| Non-fluent customers | 24.0% |
| Enterprise escalation rate | 60.4% — highest of all tiers |
