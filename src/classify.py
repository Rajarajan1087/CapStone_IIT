"""
Intent and urgency classification with a calibrated confidence.

Serves FR-02. Checked by acceptance criterion A3.

Two paths, in this order:

  1. The model, using prompts/build/classify_ticket_v1.0.txt.
  2. A deterministic keyword classifier, when the model is unavailable or
     returns something unparseable.

The fallback is not a stub. It is a real classifier scored against the
labelled development set, and it is what keeps the unattended run moving
during a provider outage (A11). A system that stops classifying when
OpenRouter is throttling has failed the gate, whatever its accuracy was
on the tickets it did manage.

Confidence is the load-bearing output here, because routing consumes it.
It is deliberately NOT reported as uniformly high: the rule classifier
derives confidence from margin of victory between the best and second-best
intent, so a ticket matching two intents equally reports genuine doubt and
gets escalated rather than guessed at.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from src.config import PROJECT_ROOT
from src.ingest import NormalisedTicket
from src.model_client import ModelClient, default_client

# The 22 intent classes present in the data. Fixed set: the classifier may
# never invent a class outside it.
INTENTS = (
    "account_access", "api_key_issue", "api_usage_question",
    "authentication_failure", "billing_query", "compliance_request",
    "configuration_help", "data_export", "data_residency", "database_issue",
    "deployment_failure", "feature_request", "integration_help", "onboarding",
    "performance_degradation", "quota_or_overage", "rate_limit",
    "rollback_request", "security_incident", "sso_configuration",
    "unclear_request", "webhook_issue",
)

URGENCIES = ("high", "medium", "low")

# Intents that must never receive an automated response, whatever the
# confidence. Enforced here and again in the router. Held in code rather
# than in a prompt precisely so that no prompt injection can widen it
# (FR-08).
NEVER_AUTOMATE = frozenset({
    "security_incident",    # account compromise; always a human
    "compliance_request",   # becomes contractual or regulatory quickly
    "feature_request",      # no documentation exists to ground an answer
    "unclear_request",      # by definition not yet understood
})

# Keyword evidence per intent, derived from the development set's own
# vocabulary. Weight 2 marks a term that is near-decisive for that intent;
# weight 1 marks supporting evidence.
_INTENT_KEYWORDS: dict[str, dict[str, int]] = {
    "authentication_failure": {"login": 2, "password": 2, "credential": 2, "signin": 2, "locked": 2, "authenticate": 2, "mfa": 1, "2fa": 1},
    "sso_configuration": {"sso": 2, "saml": 2, "identity": 1, "idp": 2, "okta": 2, "single": 1, "signon": 2},
    "api_key_issue": {"api": 1, "key": 2, "token": 2, "rotate": 2, "revoked": 2, "scope": 1, "secret": 1},
    "api_usage_question": {"api": 2, "endpoint": 2, "pagination": 2, "cursor": 2, "request": 1, "parameter": 1, "documentation": 1},
    "rate_limit": {"rate": 2, "limit": 2, "429": 2, "throttl": 2, "backoff": 2, "quota": 1},
    "quota_or_overage": {"quota": 2, "overage": 2, "exceeded": 2, "cap": 2, "spend": 2, "usage": 1, "limit": 1},
    "billing_query": {"invoice": 2, "bill": 2, "charge": 2, "payment": 2, "refund": 2, "proration": 2, "plan": 1, "price": 1},
    "deployment_failure": {"deploy": 2, "build": 2, "container": 2, "health": 1, "dependency": 2, "pipeline": 1, "release": 1},
    "rollback_request": {"rollback": 2, "roll back": 2, "undo": 2, "redeploy": 1, "previous version": 2, "last release": 2, "revert the": 2, "revert to": 2, "revert our": 2, "revert my": 2},
    "performance_degradation": {"slow": 2, "latency": 2, "performance": 2, "timeout": 2, "degrad": 2, "response": 1},
    "database_issue": {"database": 2, "connection": 2, "pool": 2, "query": 1, "postgres": 2, "exhaust": 2},
    "data_export": {"export": 2, "extract": 2, "download": 2, "dump": 2, "csv": 1, "scheduled": 1},
    "data_residency": {"residency": 2, "region": 2, "regional": 2, "stored": 1, "location": 2, "sovereignty": 2, "eu": 1},
    "security_incident": {"breach": 2, "compromise": 2, "unauthorized": 2, "hacked": 2, "suspicious": 2, "incident": 2, "attack": 2, "exposed": 2},
    "compliance_request": {"compliance": 2, "audit": 2, "gdpr": 2, "soc2": 2, "evidence": 2, "auditor": 2, "regulation": 2, "certification": 2},
    "account_access": {"access": 2, "permission": 2, "role": 2, "member": 2, "team": 1, "invite": 2, "user": 1},
    "integration_help": {"integrat": 2, "connect": 2, "ci": 1, "jenkins": 2, "github": 1, "monitoring": 2, "forward": 1},
    "webhook_issue": {"webhook": 2, "delivery": 1, "signature": 2, "retry": 1, "callback": 2, "payload": 1},
    "configuration_help": {"configur": 2, "setting": 2, "environment": 2, "variable": 2, "setup": 1, "secret": 1},
    "onboarding": {"onboard": 2, "getting": 1, "started": 2, "first": 1, "migrat": 2, "new": 1, "walkthrough": 2},
    "feature_request": {"feature": 2, "request": 1, "would": 1, "roadmap": 2, "suggestion": 2, "add": 1, "wish": 2, "useful": 1},
    "unclear_request": {},  # never keyword-matched; it is the fallback
}

# Urgency signals. Absence of any high signal does not imply low; the
# default is medium, because over-calling urgency wastes agent attention
# and under-calling it breaches the SLA on the tickets that matter.
_HIGH_URGENCY = ("down", "outage", "urgent", "asap", "critical", "blocked",
                 "cannot ship", "production", "immediately", "emergency",
                 "breach", "compromise", "unauthorized", "losing", "broken")
_LOW_URGENCY = ("wondering", "curious", "no rush", "not urgent", "whenever",
                "planning", "future", "just checking", "question about")


@dataclass
class Classification:
    """Intent, urgency, confidence, and the alternatives considered."""

    intent: str
    urgency: str
    confidence: float
    alternatives: list[dict] = field(default_factory=list)
    reasoning: str = ""
    source: str = "rules"       # "model" or "rules"
    degraded: bool = False      # true when the model path was unavailable

    @property
    def must_not_auto_respond(self) -> bool:
        """FR-08: hard exclusion, consulted before any confidence check."""
        return self.intent in NEVER_AUTOMATE

    def to_dict(self) -> dict:
        return {
            "intent": self.intent,
            "urgency": self.urgency,
            "confidence": round(self.confidence, 4),
            "alternatives": self.alternatives,
            "reasoning": self.reasoning,
            "source": self.source,
            "degraded": self.degraded,
        }


def _load_prompt() -> str:
    path = PROJECT_ROOT / "prompts" / "build" / "classify_ticket_v1.0.txt"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def classify_by_rules(ticket: NormalisedTicket) -> Classification:
    """
    Deterministic keyword classifier. Always available, never raises.

    Confidence comes from the margin between the best and second-best
    intent rather than from the winner's raw score. A ticket that matches
    two intents almost equally is genuinely uncertain, and reporting that
    honestly is what lets the router escalate it instead of guessing.
    """
    text = ticket.text.lower()
    if not text.strip():
        return Classification(
            intent="unclear_request", urgency="medium", confidence=0.15,
            reasoning="The ticket contains no usable text to classify.",
            source="rules",
        )

    scores: dict[str, float] = {}
    for intent, keywords in _INTENT_KEYWORDS.items():
        score = 0.0
        for term, weight in keywords.items():
            if term in text:
                score += weight
        if score > 0:
            scores[intent] = score

    if not scores:
        return Classification(
            intent="unclear_request", urgency=_urgency_from_text(text),
            confidence=0.25,
            reasoning="No recognisable product area was mentioned in the ticket.",
            source="rules",
        )

    # A vague "it is not working" with no product area named is an unclear
    # request, whatever stray keywords it happens to contain. Caught here
    # because one validation ticket -- "nothing is loading properly today
    # ... kindly check and revert" -- was otherwise read as a rollback
    # request on the word "revert", which in Indian English business usage
    # means "reply", not "roll back a deployment".
    _VAGUE_PHRASES = (
        "not working", "nothing is loading", "does not work", "doesn't work",
        "it is broken", "its broken", "having issues", "having problems",
        "something is wrong", "please help", "kindly check", "can someone look",
    )
    _PRODUCT_TERMS = (
        "deploy", "build", "api", "key", "token", "login", "password", "sso",
        "saml", "invoice", "bill", "billing", "quota", "rate", "database",
        "webhook", "export", "backup", "region", "residency", "permission",
        "role", "member", "container", "pipeline", "latency", "endpoint",
    )
    if (any(phrase in text for phrase in _VAGUE_PHRASES)
            and not any(term in text for term in _PRODUCT_TERMS)):
        return Classification(
            intent="unclear_request",
            urgency=_urgency_from_text(text),
            confidence=0.28,
            reasoning=("The ticket reports that something is wrong but names no "
                       "product area, so what is actually failing cannot be "
                       "determined without asking."),
            source="rules",
        )

    # A content-free follow-up ("any update?", "following up on my previous
    # message") states no problem at all, so it cannot be classified or
    # answered -- it is the definition of an unclear request. It is caught
    # explicitly because such tickets otherwise match stray keywords: three
    # in the development set scored as rollback requests purely on the word
    # "update", and were auto-answered as a result.
    _FOLLOWUP_PHRASES = (
        "following up on my previous", "any update", "any updates",
        "just checking in on my", "chasing this up", "bumping this",
        "still waiting", "no response yet", "did you get my",
    )
    _stripped = text.strip()
    if any(phrase in text for phrase in _FOLLOWUP_PHRASES) and len(_stripped) < 320:
        return Classification(
            intent="unclear_request",
            urgency=_urgency_from_text(text),
            confidence=0.30,
            reasoning=("The ticket is a follow-up that does not restate the "
                       "underlying problem, so there is nothing to act on "
                       "without a person retrieving the earlier context."),
            source="rules",
        )

    # A feature request is identified by its GRAMMAR, not its nouns. A
    # customer asking for per-project spend caps uses the same vocabulary
    # as one reporting a quota problem -- "spend", "cap", "limit" -- so
    # keyword scoring alone routinely reads a request as an incident.
    # Measured on the development set this single confusion accounted for
    # 17 of 23 tickets that should never have been auto-answered. These
    # phrases mark a request for something that does not exist yet, which
    # by definition cannot be answered from documentation.
    _REQUEST_PHRASES = (
        "would be useful", "would be very useful", "would like", "it would be",
        "would be great", "please add", "could you add", "feature request",
        "any plans to", "is there a plan", "on the roadmap", "roadmap",
        "we would love", "it would help if", "wish there was", "hoping you could add",
        "would be nice", "consider adding", "ability to",
    )
    if any(phrase in text for phrase in _REQUEST_PHRASES):
        return Classification(
            intent="feature_request",
            urgency=_urgency_from_text(text),
            confidence=0.72,
            reasoning=("The ticket asks for functionality that does not exist yet, "
                       "which has no documentation to answer from."),
            source="rules",
        )

    # Safety-critical intents win ties. Under-calling a security incident
    # or a compliance request means a ticket that must reach a human might
    # be auto-answered instead, which the governance framework treats as a
    # failure rather than a scoring loss. Over-calling one costs an
    # unnecessary escalation, which is cheap by comparison. The asymmetry
    # is deliberate and is applied before ranking, not after.
    for safety_intent in ("security_incident", "compliance_request"):
        if safety_intent in scores:
            scores[safety_intent] *= 1.6

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_intent, best_score = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0

    # Margin-based confidence, bounded well below certainty. A keyword
    # classifier should never claim 0.95; leaving headroom is what makes
    # the number usable as a routing signal rather than decoration.
    margin = (best_score - runner_up) / best_score if best_score else 0.0
    coverage = min(1.0, best_score / 6.0)
    confidence = max(0.20, min(0.88, 0.35 + 0.35 * margin + 0.25 * coverage))

    return Classification(
        intent=best_intent,
        urgency=_urgency_from_text(text),
        confidence=confidence,
        alternatives=[{"intent": i, "confidence": round(s / best_score * confidence, 3)}
                      for i, s in ranked[1:4]],
        reasoning=(f"Matched vocabulary characteristic of {best_intent.replace('_', ' ')}"
                   + (f", though {ranked[1][0].replace('_', ' ')} was also plausible."
                      if runner_up and margin < 0.34 else ".")),
        source="rules",
    )


def _urgency_from_text(text: str) -> str:
    if any(signal in text for signal in _HIGH_URGENCY):
        return "high"
    if any(signal in text for signal in _LOW_URGENCY):
        return "low"
    return "medium"


def _parse_model_output(raw: str) -> Classification | None:
    """
    Parse the model's JSON, tolerating fences and surrounding prose.

    Returns None on anything unusable, which sends the caller to the rule
    classifier. A small free-tier model wraps JSON in markdown often enough
    that treating it as fatal would make the model path useless.
    """
    if not raw:
        return None
    text = raw.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None

    intent = str(data.get("intent", "")).strip().lower()
    if intent not in INTENTS:
        return None  # never accept an invented class
    urgency = str(data.get("urgency", "")).strip().lower()
    if urgency not in URGENCIES:
        urgency = "medium"
    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        return None
    confidence = max(0.0, min(1.0, confidence))

    alternatives = data.get("alternatives") or []
    if not isinstance(alternatives, list):
        alternatives = []

    return Classification(
        intent=intent, urgency=urgency, confidence=confidence,
        alternatives=alternatives[:3],
        reasoning=str(data.get("reasoning", ""))[:400],
        source="model",
    )


def classify(ticket: NormalisedTicket,
             client: ModelClient | None = None) -> Classification:
    """
    Classify one ticket. Never raises.

    Tries the model, falls back to rules. The returned object records
    which path produced it, so the decision log and the metrics report can
    show how much of a run was served in degraded mode rather than hiding
    it in an average.
    """
    client = client or default_client
    prompt_template = _load_prompt()

    if client.available and prompt_template:
        prompt = (prompt_template
                  .replace("{{channel}}", ticket.channel)
                  .replace("{{subject}}", ticket.subject)
                  .replace("{{body}}", ticket.body))
        response = client.complete(prompt, temperature=0.0, max_tokens=400)
        if response.ok:
            parsed = _parse_model_output(response.text)
            if parsed is not None:
                return parsed
        # Fall through: unavailable, unparseable, or an invented class.

    result = classify_by_rules(ticket)
    result.degraded = not client.available
    return result
