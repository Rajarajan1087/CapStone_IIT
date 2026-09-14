"""
The pipeline: one ticket in, one decided outcome out.

Wires ingest -> classify -> retrieve -> route -> generate -> validate, and
writes every stage to the decision log (FR-09 / A8).

The contract this module guarantees to its caller is the one the gate
depends on: process_ticket NEVER raises. Whatever goes wrong -- malformed
input, provider outage, an unparseable model response, an unexpected
exception anywhere in a stage -- the ticket comes back with an outcome and
a reason, and the run continues (A9, A11).

Every ticket produces either a sent answer or a logged escalation. None
are silently dropped.
"""
from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from typing import Any

from src.classify import Classification, classify, classify_by_rules
from src.config import settings
from src.generate import Generation, generate
from src.guardrails import GuardrailResult, validate
from src.ingest import NormalisedTicket, normalise_ticket
from src.logging_store import DecisionLog
from src.model_client import ModelClient, default_client
from src.retrieve import Retriever

PROMPT_VERSIONS = {
    "classify": "classify_ticket_v1.0",
    "generate": "generate_answer_v1.0",
    "guardrail": "groundedness_guardrail_v1.0",
}


@dataclass
class TicketOutcome:
    """Everything decided about one ticket, ready for metrics and audit."""

    ticket_id: str
    channel: str
    customer_tier: str
    customer_region: str
    language_fluency: str

    action: str                       # auto_respond | escalate
    intent: str
    urgency: str
    confidence: float
    routing_gate: str
    routing_reason: str

    citations: list[str] = field(default_factory=list)
    guardrail_verdict: str = "allow"
    guardrail_violations: list[str] = field(default_factory=list)
    guardrail_blocked: bool = False

    answered: bool = False            # a reply was actually released
    degraded: bool = False            # some stage ran without the model
    latency_seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)
    processing_error: str = ""

    reply: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticket_id": self.ticket_id,
            "channel": self.channel,
            "customer_tier": self.customer_tier,
            "customer_region": self.customer_region,
            "language_fluency": self.language_fluency,
            "action_taken": self.action,
            "intent": self.intent,
            "urgency": self.urgency,
            "confidence": round(self.confidence, 4),
            "routing_gate": self.routing_gate,
            "routing_reason": self.routing_reason,
            "citations": self.citations,
            "guardrail_verdict": self.guardrail_verdict,
            "guardrail_violations": self.guardrail_violations,
            "guardrail_blocked": self.guardrail_blocked,
            "answered": self.answered,
            "degraded": self.degraded,
            "latency_seconds": round(self.latency_seconds, 4),
            "warnings": self.warnings,
            "processing_error": self.processing_error,
            "reply": self.reply,
        }


class Pipeline:
    """Holds the retriever, the model client and the decision log for a run."""

    def __init__(self, retriever: Retriever,
                 decision_log: DecisionLog | None = None,
                 client: ModelClient | None = None,
                 threshold: float | None = None) -> None:
        self.retriever = retriever
        self.log = decision_log
        self.client = client or default_client
        self.threshold = settings.confidence_threshold if threshold is None else threshold

    def _record(self, ticket_id: str, stage: str, action: str, reason: str,
                **kwargs: Any) -> None:
        """Write one decision. A logging failure must never fail a ticket."""
        if self.log is None:
            return
        try:
            self.log.log(ticket_id=ticket_id, stage=stage, action_taken=action,
                         reason=reason, **kwargs)
        except Exception:  # noqa: BLE001
            pass

    def process(self, raw_ticket: Any) -> TicketOutcome:
        """Process one raw ticket. Never raises."""
        started = time.monotonic()
        ticket: NormalisedTicket | None = None
        try:
            ticket = normalise_ticket(raw_ticket)
            return self._process_normalised(ticket, started)
        except Exception as exc:  # noqa: BLE001
            # The last line of defence. If anything above failed in a way
            # no stage anticipated, the ticket still gets an outcome -- a
            # safe one -- and the run continues (A11).
            ticket_id = getattr(ticket, "ticket_id", None) or "UNKNOWN"
            detail = f"{type(exc).__name__}: {exc}"
            self._record(ticket_id, "pipeline", "escalate",
                         f"Unhandled error while processing; escalated for safety. {detail}")
            return TicketOutcome(
                ticket_id=ticket_id,
                channel=getattr(ticket, "channel", "unknown"),
                customer_tier=getattr(ticket, "customer_tier", "unknown"),
                customer_region=getattr(ticket, "customer_region", "unknown"),
                language_fluency=getattr(ticket, "language_fluency", "unknown"),
                action="escalate",
                intent="unclear_request",
                urgency="medium",
                confidence=0.0,
                routing_gate="processing_error",
                routing_reason="The system could not process this ticket, so it "
                               "was escalated to a person.",
                latency_seconds=time.monotonic() - started,
                processing_error=detail,
                degraded=True,
            )

    def _process_normalised(self, ticket: NormalisedTicket,
                            started: float) -> TicketOutcome:
        from src.route import route  # local import avoids a cycle

        # --- classify -------------------------------------------------
        try:
            classification = classify(ticket, self.client)
        except Exception:  # noqa: BLE001
            classification = classify_by_rules(ticket)
            classification.degraded = True

        self._record(
            ticket.ticket_id, "classify", classification.intent,
            classification.reasoning or "Intent and urgency assigned.",
            prediction=classification.intent,
            confidence=classification.confidence,
            prompt_version=PROMPT_VERSIONS["classify"],
            requirement_ids="FR-02",
        )

        # --- retrieve -------------------------------------------------
        try:
            retrieved = self.retriever.search(ticket.text)
        except Exception:  # noqa: BLE001
            retrieved = []

        self._record(
            ticket.ticket_id, "retrieve",
            "passages_found" if retrieved else "no_passages",
            (f"Retrieved {len(retrieved)} passage(s) above the relevance floor."
             if retrieved else
             "No documentation passage matched closely enough to be usable."),
            sources_used=",".join(r.doc_id for r in retrieved),
            requirement_ids="FR-03",
        )

        # --- route ----------------------------------------------------
        decision = route(ticket, classification, retrieved, threshold=self.threshold)
        self._record(
            ticket.ticket_id, "route", decision.action, decision.reason,
            prediction=classification.intent,
            confidence=classification.confidence,
            threshold=decision.threshold,
            sources_used=",".join(decision.sources_considered),
            requirement_ids="FR-04,FR-08",
        )

        outcome = TicketOutcome(
            ticket_id=ticket.ticket_id,
            channel=ticket.channel,
            customer_tier=ticket.customer_tier,
            customer_region=ticket.customer_region,
            language_fluency=ticket.language_fluency,
            action=decision.action,
            intent=classification.intent,
            urgency=classification.urgency,
            confidence=classification.confidence,
            routing_gate=decision.gate,
            routing_reason=decision.reason,
            warnings=list(ticket.warnings),
            degraded=classification.degraded,
        )

        # An escalation stops here. It carries its sources so tier two
        # receives context rather than a bare forward (FR-10).
        if decision.is_escalation:
            outcome.citations = decision.sources_considered[:3]
            outcome.latency_seconds = time.monotonic() - started
            return outcome

        # --- generate -------------------------------------------------
        try:
            generation = generate(ticket, retrieved, self.client)
        except Exception:  # noqa: BLE001
            generation = Generation(answer_possible=False,
                                    unresolved="Generation failed.",
                                    degraded=True)

        self._record(
            ticket.ticket_id, "generate",
            "drafted" if generation.answer_possible else "declined",
            ("A grounded reply was drafted from the retrieved passages."
             if generation.answer_possible else
             generation.unresolved or "The sources did not support an answer."),
            confidence=generation.confidence,
            sources_used=",".join(generation.citations),
            prompt_version=PROMPT_VERSIONS["generate"],
            requirement_ids="FR-05,FR-06",
        )

        if not generation.answer_possible:
            outcome.action = "escalate"
            outcome.routing_gate = "generation_declined"
            outcome.routing_reason = (
                generation.unresolved
                or "The documentation did not support a grounded answer.")
            outcome.citations = [r.doc_id for r in retrieved][:3]
            outcome.degraded = outcome.degraded or generation.degraded
            outcome.latency_seconds = time.monotonic() - started
            return outcome

        # --- validate -------------------------------------------------
        try:
            guard: GuardrailResult = validate(generation.reply, retrieved, ticket)
        except Exception:  # noqa: BLE001
            # A guardrail that errors must fail closed, never open.
            guard = GuardrailResult(verdict="block", layer="rules")

        self._record(
            ticket.ticket_id, "validate", guard.verdict,
            ("Response passed all release checks."
             if not guard.blocked else
             "Response blocked: " + "; ".join(v.explanation for v in guard.violations)),
            guardrails=",".join(guard.checks_performed),
            prompt_version=PROMPT_VERSIONS["guardrail"],
            requirement_ids="FR-07",
        )

        outcome.guardrail_verdict = guard.verdict
        outcome.guardrail_violations = [v.type for v in guard.violations]
        outcome.citations = generation.citations
        outcome.degraded = outcome.degraded or generation.degraded

        if guard.blocked:
            outcome.guardrail_blocked = True
            outcome.action = "escalate"
            outcome.routing_gate = "guardrail_blocked"
            outcome.routing_reason = (
                "The drafted reply was blocked before sending: "
                + "; ".join(v.explanation for v in guard.violations))
        else:
            outcome.answered = True
            outcome.reply = generation.reply

        outcome.latency_seconds = time.monotonic() - started
        return outcome
