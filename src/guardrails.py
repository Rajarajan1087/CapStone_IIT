"""
Guardrails: the checks that can block a response before it is sent.

Serves FR-07. Checked by acceptance criterion A7.

Two layers, in this order:

  Layer 1  deterministic rules  -- runs always, needs no model
  Layer 2  model groundedness   -- runs when the model is available

Layer 1 comes first deliberately. A guardrail that depends on a model call
is not a guardrail during an outage, which is precisely when a system is
most likely to misbehave. Every block this system can perform, it can
perform with the provider disconnected.

The Build Specification is explicit that "a guardrail that only warns is
not a guardrail". Blocking here means the response is withheld and the
ticket is escalated to a human, never sent with a caveat attached.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.generate import _FORBIDDEN_PATTERNS
from src.ingest import NormalisedTicket
from src.retrieve import RetrievalResult

# Personally identifying or secret material that must never appear in an
# outbound reply (NFR-04).
_PII_PATTERNS = (
    (r"\bCUST-\d{3,}\b", "customer identifier"),
    (r"\b[\w.+-]+@[\w-]+\.[\w.]+\b", "email address"),
    (r"\bsk-[A-Za-z0-9_-]{12,}\b", "API key"),
    (r"\bgithub_pat_[A-Za-z0-9_]{20,}\b", "access token"),
    (r"\bBearer\s+[A-Za-z0-9._-]{16,}\b", "bearer token"),
    (r"\b(?:\d[ -]?){13,19}\b", "payment card number"),
    (r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", "IP address"),
)

CHECKS_PERFORMED = (
    "private_data",
    "forbidden_commitment",
    "unsupported_citation",
    "ungrounded_answer",
)


@dataclass
class Violation:
    type: str
    quote: str
    explanation: str

    def to_dict(self) -> dict:
        return {"type": self.type, "quote": self.quote,
                "explanation": self.explanation}


@dataclass
class GuardrailResult:
    """
    The outcome of validation.

    `checks_performed` is always fully populated, whether or not anything
    was found. The Build Specification requires the validator to record
    what it checked as well as what it caught, so a clean pass is evidence
    rather than silence.
    """

    verdict: str                       # "allow" or "block"
    violations: list[Violation] = field(default_factory=list)
    checks_performed: list[str] = field(default_factory=lambda: list(CHECKS_PERFORMED))
    layer: str = "rules"

    @property
    def blocked(self) -> bool:
        return self.verdict == "block"

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "violations": [v.to_dict() for v in self.violations],
            "checks_performed": self.checks_performed,
            "layer": self.layer,
        }


def _excerpt(text: str, match: re.Match, width: int = 60) -> str:
    start = max(0, match.start() - width // 2)
    return text[start:match.end() + width // 2].replace("\n", " ").strip()


def validate(reply: str,
             retrieved: list[RetrievalResult],
             ticket: NormalisedTicket | None = None) -> GuardrailResult:
    """
    Run every deterministic check. Blocks on the first category that fires.

    Never raises, and never needs the model.
    """
    violations: list[Violation] = []
    if not reply.strip():
        return GuardrailResult(verdict="allow")

    # 1. Private data in an outbound reply.
    for pattern, label in _PII_PATTERNS:
        match = re.search(pattern, reply)
        if match:
            violations.append(Violation(
                type="private_data",
                quote=_excerpt(reply, match),
                explanation=f"The reply contains what looks like a {label}, "
                            f"which must never be sent in an automated response.",
            ))
            break

    # 2. Commitments the system has no authority to make.
    for pattern, description in _FORBIDDEN_PATTERNS:
        match = re.search(pattern, reply, re.IGNORECASE)
        if match:
            violations.append(Violation(
                type="forbidden_commitment",
                quote=_excerpt(reply, match),
                explanation=f"The reply {description}, which only a person may do.",
            ))
            break

    # 3. Citations that do not correspond to anything retrieved (A6).
    allowed = {r.doc_id for r in retrieved}
    cited = set(re.findall(r"\[(DOC-[A-Z]+-\d+)\]", reply))
    invented = cited - allowed
    if invented:
        violations.append(Violation(
            type="unsupported_citation",
            quote=", ".join(sorted(invented)),
            explanation=("The reply cites documentation that was not retrieved "
                         "for this ticket, so the citation cannot be verified."),
        ))

    # 4. An answer with no citation at all is not grounded.
    #
    # Length is a poor test for this -- an uncited instruction of 119
    # characters is exactly as unverifiable as one of 200. What actually
    # matters is whether the reply makes a CLAIM: if it tells the customer
    # that something is the case or instructs them to do something, that
    # assertion needs a source. A short acknowledgement that asserts
    # nothing does not.
    _CLAIM_MARKERS = (
        " should ", " must ", " need to ", " will ", " can ", " is ", " are ",
        " try ", " run ", " check ", " clear ", " set ", " use ", " enable ",
        " disable ", " restart ", " contact ", " ensure ", " confirm ",
    )
    _body = reply.strip()
    _makes_claim = any(marker in f" {_body.lower()} " for marker in _CLAIM_MARKERS)
    if not cited and _makes_claim:
        violations.append(Violation(
            type="ungrounded_answer",
            quote=reply.strip()[:80],
            explanation=("The reply makes statements without citing any source "
                         "passage, so none of it can be verified."),
        ))

    return GuardrailResult(
        verdict="block" if violations else "allow",
        violations=violations,
        layer="rules",
    )
