"""
Ingest: turn a raw ticket from any of the four channels into one internal
representation the rest of the pipeline can rely on.

Serves FR-01. Checked by acceptance criterion A2.

CloudServe receives tickets through email, live chat, comments left on
documentation pages, and the community forum. The four arrive with
different shapes -- chat tickets carry no subject at all, forum and
docs_comment posts are written in a more public register, email is the
only channel where a subject line reliably carries signal. Everything
downstream (classification, retrieval, routing) should not have to care
which of the four a ticket came from, so the differences are absorbed
here and nowhere else.

Two rules govern this module:

1. It never raises on a ticket. A malformed, empty, or partially missing
   ticket produces a NormalisedTicket carrying warnings, because the
   evaluation run must process every ticket in the file without manual
   intervention (A9) and one bad record must not halt it (A11).

2. It never discards the original. The raw payload and the source channel
   are both preserved, because the channel matters downstream and because
   a decision must remain auditable against exactly what arrived.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# The four channels CloudServe actually receives tickets through. A value
# outside this set is preserved verbatim but flagged, rather than being
# silently coerced, so an unexpected channel in the hidden evaluation set
# is visible in the warnings rather than hidden by a default.
KNOWN_CHANNELS = ("email", "chat", "docs_comment", "forum")

KNOWN_TIERS = ("enterprise", "business", "standard")
KNOWN_REGIONS = ("north_america", "europe", "asia_pacific", "latin_america")
KNOWN_FLUENCY = ("fluent", "non_fluent")

# Control characters break both the vector store and the model prompt, but
# tab, newline and carriage return are legitimate content. Strip the rest.
_CONTROL_CHARS = "".join(
    chr(c) for c in range(0x20) if chr(c) not in "\t\n\r"
) + chr(0x7F)
_CONTROL_RE = re.compile(f"[{re.escape(_CONTROL_CHARS)}]")

# Zero-width and directional-formatting characters arrive via copy-paste
# from web pages and chat clients. They are invisible, they survive
# tokenisation, and they make otherwise identical strings compare unequal.
_INVISIBLE_RE = re.compile(
    "[\u200b-\u200f\u202a-\u202e\u2060\ufeff]"
)

_WHITESPACE_RUN_RE = re.compile(r"[ \t]{2,}")
_BLANK_LINE_RUN_RE = re.compile(r"\n{3,}")


def clean_text(value: Any) -> str:
    """
    Normalise arbitrary input to safe, comparable text.

    Applies Unicode NFKC normalisation, removes control and invisible
    characters, and collapses runaway whitespace, while leaving genuine
    content -- including non-ASCII text -- untouched. Roughly a quarter of
    CloudServe's tickets come from customers writing in a second language,
    so this must not strip or mangle non-English characters.

    Any non-string input (None, a number, a nested object) degrades to an
    empty string rather than raising, because the hidden evaluation set may
    contain a field this code has never seen.
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        # Numbers and booleans are stringified; containers are not, since a
        # dict rendered into a prompt is noise rather than content.
        if isinstance(value, (int, float, bool)):
            value = str(value)
        else:
            return ""

    text = unicodedata.normalize("NFKC", value)
    text = _CONTROL_RE.sub(" ", text)
    text = _INVISIBLE_RE.sub("", text)
    text = _WHITESPACE_RUN_RE.sub(" ", text)
    text = _BLANK_LINE_RUN_RE.sub("\n\n", text)
    return text.strip()


def _parse_timestamp(value: Any) -> datetime | None:
    """
    Parse an ISO-8601 timestamp, returning None rather than raising.

    The dataset uses a trailing 'Z', which datetime.fromisoformat does not
    accept before Python 3.11, so it is translated explicitly. A ticket
    with an unparseable timestamp is still a processable ticket -- the
    value is simply unavailable for latency and ageing calculations.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


@dataclass
class NormalisedTicket:
    """
    One ticket, in the single shape the rest of the pipeline consumes.

    `text` is the field downstream stages should use. It is the channel's
    meaningful content assembled into one block, so that a classifier or a
    retriever never has to decide whether this particular channel populates
    `subject`.
    """

    ticket_id: str
    channel: str
    subject: str
    body: str
    text: str
    received_at: datetime | None

    customer_id: str
    customer_tier: str
    customer_region: str
    language_fluency: str

    # Non-fatal observations made during normalisation: an empty body, an
    # unknown channel, a timestamp that would not parse. Carried forward so
    # they can be logged against the decision (A8) and counted in the
    # metrics report, rather than being discovered by their absence.
    warnings: list[str] = field(default_factory=list)

    # The untouched input, preserved so any decision can be audited against
    # exactly what arrived rather than against this module's interpretation.
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        """True when there is no usable content to classify or answer."""
        return not self.text.strip()

    @property
    def customer_name(self) -> str:
        """
        The customer's name, read from the raw payload on demand.

        Deliberately not a stored field. It is needed for nothing the
        pipeline decides, and keeping it off the normalised object makes it
        materially harder for a name to reach a prompt or a generated reply
        by accident (NFR-04).
        """
        return clean_text(self.raw.get("customer_name"))

    def to_log_dict(self) -> dict[str, Any]:
        """
        A compact, privacy-safe view for the decision log.

        Excludes the ticket body and the customer name: the log records
        what was decided and why, and does not need to duplicate customer
        content to do so.
        """
        return {
            "ticket_id": self.ticket_id,
            "channel": self.channel,
            "customer_tier": self.customer_tier,
            "customer_region": self.customer_region,
            "language_fluency": self.language_fluency,
            "received_at": self.received_at.isoformat() if self.received_at else None,
            "warnings": list(self.warnings),
        }


def _assemble_text(channel: str, subject: str, body: str) -> str:
    """
    Build the single text block downstream stages read.

    Chat tickets carry an empty subject by design, so for those the body is
    the whole of the content. Where a subject exists it is placed first and
    labelled, because a support subject line often states the problem more
    directly than the body does and losing that ordering measurably weakens
    retrieval.
    """
    subject = subject.strip()
    body = body.strip()

    if subject and body:
        return f"Subject: {subject}\n\n{body}"
    if subject:
        return f"Subject: {subject}"
    return body


def _coerce_enum(value: Any, allowed: tuple[str, ...], field_name: str,
                 warnings: list[str]) -> str:
    """
    Normalise a categorical field, flagging anything unrecognised.

    The value is preserved as given rather than replaced with a default,
    because a segment that appears only in the hidden evaluation set should
    show up in the fairness audit as itself, not silently folded into an
    existing bucket. The warning is what makes it visible.
    """
    text = clean_text(value).lower().replace(" ", "_").replace("-", "_")
    if not text:
        warnings.append(f"missing_{field_name}")
        return "unknown"
    if text not in allowed:
        warnings.append(f"unexpected_{field_name}:{text}")
    return text


def normalise_ticket(raw: Any) -> NormalisedTicket:
    """
    Convert one raw ticket into a NormalisedTicket. Never raises.

    Anything that is not a JSON object at all still produces a ticket --
    one that is empty and carries a warning -- so that the caller can log
    it, escalate it, and carry on. A single unusable record in a file of
    120 must not be able to end the run.
    """
    warnings: list[str] = []

    if not isinstance(raw, dict):
        return NormalisedTicket(
            ticket_id="UNKNOWN",
            channel="unknown",
            subject="",
            body="",
            text="",
            received_at=None,
            customer_id="",
            customer_tier="unknown",
            customer_region="unknown",
            language_fluency="unknown",
            warnings=["not_an_object"],
            raw={},
        )

    ticket_id = clean_text(raw.get("ticket_id"))
    if not ticket_id:
        warnings.append("missing_ticket_id")
        ticket_id = "UNKNOWN"

    channel = clean_text(raw.get("channel")).lower()
    if not channel:
        warnings.append("missing_channel")
        channel = "unknown"
    elif channel not in KNOWN_CHANNELS:
        # Preserved rather than remapped: an unfamiliar channel is
        # information, and the pipeline handles it on the generic path.
        warnings.append(f"unknown_channel:{channel}")

    subject = clean_text(raw.get("subject"))
    body = clean_text(raw.get("body"))

    # Chat tickets have no subject by schema, so its absence there is
    # expected and is not worth a warning. On any other channel it is.
    if not subject and channel not in ("chat", "unknown"):
        warnings.append("missing_subject")

    if not body:
        warnings.append("empty_body")

    received_at = _parse_timestamp(raw.get("received_at"))
    if received_at is None and raw.get("received_at"):
        warnings.append("unparseable_received_at")
    elif not raw.get("received_at"):
        warnings.append("missing_received_at")

    text = _assemble_text(channel, subject, body)
    if not text:
        warnings.append("no_usable_content")

    return NormalisedTicket(
        ticket_id=ticket_id,
        channel=channel,
        subject=subject,
        body=body,
        text=text,
        received_at=received_at,
        customer_id=clean_text(raw.get("customer_id")),
        customer_tier=_coerce_enum(raw.get("customer_tier"), KNOWN_TIERS,
                                   "customer_tier", warnings),
        customer_region=_coerce_enum(raw.get("customer_region"), KNOWN_REGIONS,
                                     "customer_region", warnings),
        language_fluency=_coerce_enum(raw.get("language_fluency"), KNOWN_FLUENCY,
                                      "language_fluency", warnings),
        warnings=warnings,
        raw=raw,
    )


def normalise_batch(raw_tickets: Any) -> list[NormalisedTicket]:
    """
    Normalise a list of raw tickets, tolerating a malformed collection.

    Returns an empty list for input that is not a list at all, rather than
    raising, so that a caller reading an unexpected file shape reports zero
    tickets processed instead of a traceback.
    """
    if not isinstance(raw_tickets, list):
        return []
    return [normalise_ticket(item) for item in raw_tickets]
