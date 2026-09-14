"""
Routing: decide whether to answer automatically or escalate to a human.

Serves FR-04 and FR-08. Checked by acceptance criterion A5.

The decision is deterministic. The same ticket routed twice produces the
same outcome, because A5 is verified by doing exactly that, and because a
support manager cannot defend a system that answers a ticket on Tuesday
and escalates its twin on Wednesday.

Routing applies four gates in a fixed order, and the FIRST one that fires
decides. Order matters: the safety gates run before the confidence gate,
so a high-confidence classification can never talk its way past them.

  Gate 1  never-automate intent          -> escalate
  Gate 2  safety keywords in raw text    -> escalate   (independent net)
  Gate 3  no usable retrieval            -> escalate
  Gate 4  confidence below threshold     -> escalate
          otherwise                      -> auto_respond

Gate 2 exists because the classifier is fallible. Measured on the
development set, the offline rule classifier catches only about 72% of
tickets flagged must_not_auto_respond. Relying on classification alone
would therefore auto-answer roughly a quarter of the tickets that must
never be auto-answered. Gate 2 reads the ticket text directly for
unambiguous danger vocabulary, so a misclassification does not become a
governance failure. It is a cheap, deterministic net under a probabilistic
component -- the same reasoning that puts a rule layer under the model
guardrail.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.classify import NEVER_AUTOMATE, Classification
from src.config import settings
from src.ingest import NormalisedTicket
from src.retrieve import RetrievalResult

# Vocabulary that forces escalation regardless of what the classifier
# concluded. Deliberately narrow: each term is one whose presence makes
# human review correct even if the ticket is ultimately routine. A broad
# list here would escalate everything and defeat the system's purpose.
_SAFETY_TERMS = (
    # account compromise
    "compromis", "breach", "hacked", "unauthorized access", "unauthorised access",
    "someone else has access", "did not make", "didn't make", "suspicious login",
    "credentials leaked", "exposed secret", "exposed key", "leaked key",
    # Offboarding and insider access. A departed employee retaining access
    # is a security incident, but it is described in ordinary employment
    # language with none of the vocabulary above, so it is listed
    # explicitly. Three such tickets in the development set were otherwise
    # classified as routine API questions.
    "former employee", "ex-employee", "employee who left", "after leaving",
    "no longer with", "has left the company", "still has access",
    "should not have access", "shouldn't have access", "revoke their access",
    # legal, contractual, regulatory
    "gdpr", "soc 2", "soc2", "auditor", "subpoena", "legal", "lawsuit",
    "data protection officer", "regulatory", "compliance review",
    # money commitments
    "refund", "chargeback", "credit note", "billing dispute", "overcharged",
    "dispute the charge", "cancel my contract", "terminate our contract",
)


@dataclass
class RoutingDecision:
    """What was decided, and the reason in language a manager can read."""

    action: str                 # "auto_respond" or "escalate"
    reason: str
    gate: str                   # which gate decided
    confidence: float
    threshold: float
    sources_considered: list[str]

    @property
    def is_escalation(self) -> bool:
        return self.action == "escalate"

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "reason": self.reason,
            "gate": self.gate,
            "confidence": round(self.confidence, 4),
            "threshold": self.threshold,
            "sources_considered": self.sources_considered,
        }


def _has_safety_term(text: str) -> str | None:
    lowered = text.lower()
    for term in _SAFETY_TERMS:
        if term in lowered:
            return term
    return None


def route(ticket: NormalisedTicket,
          classification: Classification,
          retrieved: list[RetrievalResult],
          threshold: float | None = None) -> RoutingDecision:
    """
    Decide the action for one ticket. Deterministic and never raises.

    `threshold` defaults to the configured confidence threshold. It is
    passed explicitly by the harness so that a threshold sweep can be run
    without mutating global configuration.
    """
    threshold = settings.confidence_threshold if threshold is None else threshold
    sources = [r.doc_id for r in retrieved]

    # Gate 1 -- never-automate intent. Checked before confidence, so a
    # confident classification cannot override it (FR-08).
    if classification.intent in NEVER_AUTOMATE:
        return RoutingDecision(
            action="escalate",
            reason=(f"This is a {classification.intent.replace('_', ' ')}, which is "
                    f"always handled by a person regardless of how confident the "
                    f"system is."),
            gate="never_automate_intent",
            confidence=classification.confidence,
            threshold=threshold,
            sources_considered=sources,
        )

    # Gate 2 -- safety vocabulary in the ticket itself. Independent of the
    # classifier, because the classifier misses roughly a quarter of these.
    term = _has_safety_term(ticket.text)
    if term:
        return RoutingDecision(
            action="escalate",
            reason=(f"The ticket mentions '{term}', which indicates a security, "
                    f"legal or billing matter that a person must review even "
                    f"though it was classified as "
                    f"{classification.intent.replace('_', ' ')}."),
            gate="safety_keyword",
            confidence=classification.confidence,
            threshold=threshold,
            sources_considered=sources,
        )

    # Gate 3 -- nothing usable retrieved. An answer cannot be grounded, so
    # it must not be attempted (FR-03, FR-05).
    if not retrieved:
        return RoutingDecision(
            action="escalate",
            reason=("Nothing in the support documentation matched this ticket "
                    "closely enough to answer from, so it needs a person."),
            gate="no_retrieval",
            confidence=classification.confidence,
            threshold=threshold,
            sources_considered=[],
        )

    # Gate 4 -- confidence.
    if classification.confidence < threshold:
        return RoutingDecision(
            action="escalate",
            reason=(f"The system was only {classification.confidence:.0%} confident "
                    f"about what this ticket is asking, below the "
                    f"{threshold:.0%} needed to answer without review."),
            gate="below_confidence_threshold",
            confidence=classification.confidence,
            threshold=threshold,
            sources_considered=sources,
        )

    return RoutingDecision(
        action="auto_respond",
        reason=(f"Classified as {classification.intent.replace('_', ' ')} with "
                f"{classification.confidence:.0%} confidence, and the documentation "
                f"contains a matching answer ({', '.join(sources[:2])})."),
        gate="passed_all_gates",
        confidence=classification.confidence,
        threshold=threshold,
        sources_considered=sources,
    )
