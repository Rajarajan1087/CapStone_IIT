# Video demo — tickets guaranteed to produce each required outcome

The submission checklist requires the live demonstration to show **a success, an
escalation, and a guardrail firing**. These are taken from the gate run, so each
one is verified to behave as described.

Run any of them with:

    python -m scripts.run_pipeline --ticket <TICKET_ID>

---

## 1. Success — auto-answered with citations

| Ticket | Intent | Confidence | Cites |
|---|---|---|---|
| **DEV-0017** | account_access | 0.88 | DOC-ACCT-002, DOC-ACCT-001 |
| DEV-0019 | account_access | 0.88 | DOC-ACCT-001, DOC-SEC-001 |
| DEV-0023 | account_access | 0.88 | DOC-ACCT-001 |

Use **DEV-0017** — highest confidence, two citations, clean answer.

---

## 2. Guardrail firing — blocked before sending

Only **6 of 580** tickets triggered a block, all `forbidden_commitment`:

| Ticket | Intent | Violation |
|---|---|---|
| **DEV-0073** | billing_query | forbidden_commitment |
| DEV-0079 | billing_query | forbidden_commitment |
| DEV-0151 | billing_query | forbidden_commitment |
| DEV-0228 | billing_query | forbidden_commitment |
| VAL-0072 | billing_query | forbidden_commitment |
| VAL-0073 | billing_query | forbidden_commitment |

Use **DEV-0073**. Say on camera what it caught: the draft made a commitment
about a refund or credit, which is not the system's to make, so it was blocked
rather than sent.

---

## 3. Escalation — two kinds worth showing

**Gate 1, never-automate intent** (130 tickets). Shows the list working
*before* confidence is consulted:

| Ticket | Intent |
|---|---|
| **DEV-0003** | compliance_request |
| DEV-0005 | feature_request |
| DEV-0008 | unclear_request |

**Gate 2, safety vocabulary** (9 tickets). The stronger demo — these are the
ones the *classifier missed* and the independent keyword net caught:

| Ticket | Classified as | Caught on |
|---|---|---|
| **DEV-0132** | api_usage_question | "former employee" |
| DEV-0016 | api_key_issue | "exposed key" |
| VAL-0001 | api_key_issue | "exposed key" |

Use **DEV-0132**. It makes the best point in the whole demo: the classifier
thought this was a routine API question. A cheap deterministic net under a
probabilistic component is what stopped it being auto-answered.

---

## Suggested demo order (about 7 minutes)

1. **DEV-0017** — it works, and it cites its source
2. **DEV-0003** — it refuses on principle, before any score is looked at
3. **DEV-0132** — the classifier was wrong and the safety net caught it anyway
4. **DEV-0073** — the guardrail blocks a reply that was already drafted
5. Full unattended run — 580 tickets, 2.7 s, 0 errors, decision log reconciling
