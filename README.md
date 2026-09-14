# CloudServe Support System

An AI-assisted customer support pipeline for CloudServe Solutions, built as
the IIT Roorkee Forward Deployed AI Engineering capstone.

## What this is, and what it deliberately is not

CloudServe asked for a chatbot. The discovery evidence says they should not
get one.

**70.7%** of their tickets are already answerable from their own 29-article
knowledge base, and **47.5%** of their historical escalations were tickets
that *had* a documented answer. The problem is not a shortage of knowledge.
It is that the right answer cannot be found and trusted quickly enough at
the moment a ticket arrives.

So this system does retrieval, confidence-scored routing, grounded
generation with verifiable citations, and blocking guardrails — and it
escalates to a human whenever it is not sure. It is not a conversational
assistant.

## Requirements

Python 3.10 or later. **No third-party packages are required** to run the
pipeline or the evaluation — it uses only the standard library. `requests`
and `python-dotenv` are used when present for live model calls and `.env`
loading, and the system degrades cleanly without them.

## Setup

```bash
# 1. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate         # macOS/Linux
# .venv\Scripts\activate          # Windows

# 2. Install dependencies (optional — see note above)
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# 3. Configure
cp .env.example .env
# then edit .env and add your OPENROUTER_API_KEY (the free tier is enough)
```

**The system runs without an API key.** Without one it uses its
deterministic classifier and extractive generator, which quote the
documentation verbatim rather than composing prose. Answers are more
conservative, never absent. This is the mode CI runs in.

## Verify the install

```bash
python -m scripts.check_setup
```

Confirms configuration loads, all four channels normalise, and reports
whether model access is live or degraded.

## Run it

```bash
# Process any ticket file end to end, unattended, and produce metrics.
python -m evaluation.harness --input data/validation_tickets.json \
                             --output evaluation/results/

# Tests
python -m pytest tests/ -v

# Fairness audit over a completed run
python -m scripts.fairness_audit \
    --metrics evaluation/results/metrics_report.json

# Compare chunking strategies
python -m scripts.compare_chunking
```

`--input` and `--output` are arguments, never constants. The harness is run
after submission against a hidden set of 120 tickets that is not in this
repository, so it must work against a file it has never seen.

## Results on the supplied data

Full 580-ticket run (500 development + 80 validation), no model access,
deterministic path only:

| Measure | Result | Baseline |
|---|---|---|
| Tickets processed | 580 | — |
| Processing errors | 0 | — |
| Classification accuracy | 80.7% | — |
| Routing accuracy | 69.7% | — |
| Citation accuracy | 92.7% | — |
| First contact resolution | 47.6% | 44.1% |
| Escalation rate | 36.4% | 55.9% |
| **Never-automate violations** | **0 of 101** | — |
| Decisions logged | 2,490 (reconciles) | — |
| Runtime | 2.7 s | — |

## How it works

```
ingest → classify → retrieve → route → generate → validate
                                 ↓
                    decision log (every stage, every ticket)
```

| Module | Does | Requirement |
|---|---|---|
| `src/ingest.py` | Four channels → one shape; never raises | FR-01 / A2 |
| `src/classify.py` | Intent, urgency, calibrated confidence | FR-02 / A3 |
| `src/corpus.py` | Section-aware chunking of the corpus | FR-03 |
| `src/retrieve.py` | TF-IDF search; returns nothing on weak match | FR-03 / A4 |
| `src/route.py` | Four ordered gates; deterministic | FR-04, FR-08 / A5 |
| `src/generate.py` | Grounded, cited answers; extractive fallback | FR-05, FR-06 / A6 |
| `src/guardrails.py` | Blocks — needs no model call | FR-07 / A7 |
| `src/logging_store.py` | Every decision, with its reason | FR-09 / A8 |
| `evaluation/harness.py` | The unattended run and its metrics | FR-11 / A9, A10 |

Prompts live in `prompts/`, versioned by filename, and are loaded from
disk rather than embedded in code.

## Design decisions worth knowing

**No external vector database.** Retrieval is TF-IDF with cosine similarity
in pure Python over ~145 passages. At this scale an ANN index would add
dependencies and failure modes for no measurable gain. The cost is that
TF-IDF matches words rather than meanings, so query-side synonym expansion
bridges the gap between how customers describe problems and how the
documentation titles them.

**Answerability is decided by intent, not by similarity score.** Retrieval
precision never exceeds ~78% at any threshold, because a feature request
discusses real product areas in the documentation's own vocabulary and
therefore scores as highly as a genuine question. No cut-off can separate
them, so the router excludes never-automate intents outright before score
is consulted.

**The guardrail is deterministic first.** A block must not depend on the
model provider being reachable. Every block this system can perform, it can
perform offline.

**The never-automate list is code, not a prompt.** So no injected
instruction can widen it.

**Degradation is toward caution.** Without a model the generator quotes the
documentation verbatim. It cannot hallucinate, because every sentence it
emits already exists in a reviewed article.

## Repository layout

```
src/              pipeline components
prompts/          versioned prompts (build, evaluation, specification, review)
tests/            62 tests
evaluation/       harness and dated run outputs
scripts/          setup check, fairness audit, chunking comparison
data/             ticket and documentation files
docs/             architecture and completed stage workbooks
storage/          generated at runtime — never committed
```

## AI tool use

Declared in the project report, per the Submission Guide.
