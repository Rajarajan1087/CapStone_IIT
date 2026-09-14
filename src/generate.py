"""
Answer generation, grounded in retrieved passages, with resolvable citations.

Serves FR-05, FR-06 and FR-12. Checked by acceptance criterion A6.

Like classification, this has a model path and a deterministic fallback.
The fallback here is EXTRACTIVE: it quotes the retrieved documentation
rather than composing new prose. That is a deliberate choice, not a
limitation worked around.

An extractive answer cannot hallucinate, because every sentence it emits
came verbatim from a reviewed article and carries that article's id. When
the provider is unavailable the system therefore degrades toward being
more conservative rather than less -- which is the correct direction for a
client whose single stated failure condition is "it sends something wrong
to a customer".

Every generated answer, on either path, is marked as automated (FR-12).
Ravi's point in discovery was that he calibrates how much he trusts a
reply by who wrote it, and that hiding the distinction is the thing that
would annoy him.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from src.config import PROJECT_ROOT
from src.ingest import NormalisedTicket
from src.model_client import ModelClient, default_client
from src.retrieve import RetrievalResult

# Appended to every automated reply. FR-12: the recipient is always told.
AUTOMATION_NOTICE = (
    "\n\n---\nThis reply was drafted automatically from CloudServe's support "
    "documentation and the sources are cited above. A support engineer will "
    "confirm it."
)

# Claims that must never appear, whatever the sources say. Derived from the
# must_not_claim values in ground_truth_responses.json plus the commitments
# Daniel flagged in discovery as becoming contractual.
_FORBIDDEN_PATTERNS = (
    (r"\brefund(ed|ing)?\b", "promises or references a refund"),
    (r"\bcredit(ed)?\s+(your|the)\s+account\b", "promises an account credit"),
    (r"\bhas been (fixed|resolved|corrected)\b", "claims the issue is already fixed"),
    (r"\bwe have (fixed|resolved|escalated)\b", "claims action already taken"),
    (r"\bwill be (fixed|released|available) (on|by|in)\b", "gives a fix timeline"),
    (r"\bby (next|the) (week|month|sprint|release)\b", "gives a delivery date"),
    (r"\bI have (checked|reviewed|accessed|looked at) your\b", "claims to have accessed the account"),
    (r"\bI can see (in )?your (account|logs|billing)\b", "claims to have inspected the account"),
)


@dataclass
class Generation:
    """A drafted reply, or an explicit refusal to draft one."""

    answer_possible: bool
    reply: str = ""
    citations: list[str] = field(default_factory=list)
    unresolved: str = ""
    confidence: float = 0.0
    source: str = "extractive"   # "model" or "extractive"
    degraded: bool = False

    def to_dict(self) -> dict:
        return {
            "answer_possible": self.answer_possible,
            "reply": self.reply,
            "citations": self.citations,
            "unresolved": self.unresolved,
            "confidence": round(self.confidence, 4),
            "source": self.source,
            "degraded": self.degraded,
        }


def _load_prompt() -> str:
    path = PROJECT_ROOT / "prompts" / "build" / "generate_answer_v1.0.txt"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _format_sources(retrieved: list[RetrievalResult]) -> str:
    return "\n\n".join(
        f"[{r.doc_id}] {r.chunk.title} — {r.chunk.section}\n{r.chunk.text}"
        for r in retrieved
    )


def generate_extractive(ticket: NormalisedTicket,
                        retrieved: list[RetrievalResult]) -> Generation:
    """
    Build a reply by quoting the retrieved documentation verbatim.

    Cannot hallucinate: every line emitted is text that already exists in a
    reviewed article, and each is attributed to the article it came from.
    Used when the model is unavailable, and as the safe floor the system
    degrades to rather than falling silent.
    """
    if not retrieved:
        return Generation(
            answer_possible=False,
            unresolved=("The support documentation does not cover this question, "
                        "so no grounded answer could be drafted."),
            source="extractive",
        )

    # Prefer sections that actually tell the customer what to do.
    ranked = sorted(
        retrieved,
        key=lambda r: (
            0 if "resolution" in r.chunk.section.lower() else
            1 if "cause" in r.chunk.section.lower() else
            2 if "symptom" in r.chunk.section.lower() else 3,
            -r.score,
        ),
    )
    chosen = ranked[:2]

    parts = ["Based on CloudServe's support documentation:"]
    citations: list[str] = []
    for result in chosen:
        body = result.chunk.text
        # Strip the "Title — Section" header the chunker prepended; the
        # citation line below carries that information already.
        if "\n" in body:
            body = body.split("\n", 1)[1].strip()
        parts.append(f"\nFrom \"{result.chunk.title}\" [{result.doc_id}]:\n{body}")
        if result.doc_id not in citations:
            citations.append(result.doc_id)

    reply = "\n".join(parts) + AUTOMATION_NOTICE
    top_score = max(r.score for r in chosen)
    return Generation(
        answer_possible=True,
        reply=reply,
        citations=citations,
        confidence=min(0.75, 0.4 + top_score),
        source="extractive",
    )


def _parse_model_output(raw: str, allowed_docs: set[str]) -> Generation | None:
    """
    Parse the generator's JSON and reject any citation it invented.

    A citation to a document that was not retrieved is exactly the failure
    A6 checks for, so it is caught here rather than left for the guardrail.
    """
    if not raw:
        return None
    text = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None

    possible = bool(data.get("answer_possible"))
    reply = str(data.get("reply") or "").strip()
    citations = [str(c) for c in (data.get("citations") or [])]

    if possible and not reply:
        return None
    # Any invented citation invalidates the whole generation.
    if any(c not in allowed_docs for c in citations):
        return None
    # A reply citing nothing is not grounded, whatever it claims.
    if possible and not citations:
        return None

    try:
        confidence = float(data.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5

    return Generation(
        answer_possible=possible,
        reply=(reply + AUTOMATION_NOTICE) if possible else "",
        citations=citations,
        unresolved=str(data.get("unresolved") or ""),
        confidence=max(0.0, min(1.0, confidence)),
        source="model",
    )


def generate(ticket: NormalisedTicket,
             retrieved: list[RetrievalResult],
             client: ModelClient | None = None) -> Generation:
    """Draft a reply. Never raises. Falls back to extractive quoting."""
    client = client or default_client

    if not retrieved:
        return Generation(
            answer_possible=False,
            unresolved=("Nothing in the support documentation matched this "
                        "ticket, so no grounded answer could be drafted."),
            source="extractive",
            degraded=not client.available,
        )

    template = _load_prompt()
    if client.available and template:
        prompt = (template
                  .replace("{{retrieved_passages}}", _format_sources(retrieved))
                  .replace("{{channel}}", ticket.channel)
                  .replace("{{subject}}", ticket.subject)
                  .replace("{{body}}", ticket.body))
        response = client.complete(prompt, temperature=0.0, max_tokens=900)
        if response.ok:
            parsed = _parse_model_output(response.text, {r.doc_id for r in retrieved})
            if parsed is not None:
                return parsed

    result = generate_extractive(ticket, retrieved)
    result.degraded = not client.available
    return result
