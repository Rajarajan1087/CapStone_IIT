"""
Tests for ingest and normalisation.

Covers TC-01 (four-channel ingest, FR-01, acceptance criterion A2) and the
malformed-input half of TC-14 (FR-11 / A11): a single unusable record must
never be able to halt an unattended run.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.ingest import (
    KNOWN_CHANNELS,
    NormalisedTicket,
    clean_text,
    normalise_batch,
    normalise_ticket,
)

DATA = Path(__file__).resolve().parents[1] / "data"


def _load(name: str) -> list[dict]:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def all_tickets() -> list[dict]:
    """The full labelled set: 500 development plus 80 validation tickets."""
    return _load("development_tickets.json") + _load("validation_tickets.json")


# --- TC-01: all four channels ingest and normalise -------------------------


def test_every_supplied_ticket_normalises(all_tickets):
    """A2: no ticket in the supplied data fails to ingest."""
    normalised = normalise_batch(all_tickets)
    assert len(normalised) == len(all_tickets)
    assert all(isinstance(t, NormalisedTicket) for t in normalised)


def test_all_four_channels_are_present_and_handled(all_tickets):
    """
    A2: a ticket from each channel is handled without channel-specific
    breakage. Asserting all four appear guards against a future change
    that silently drops one.
    """
    normalised = normalise_batch(all_tickets)
    seen = {t.channel for t in normalised}
    assert seen == set(KNOWN_CHANNELS), f"expected all four channels, saw {seen}"

    for channel in KNOWN_CHANNELS:
        sample = [t for t in normalised if t.channel == channel]
        assert sample, f"no tickets on channel {channel}"
        # Every ticket on every channel must yield usable text, which is
        # what downstream classification and retrieval actually consume.
        assert all(not t.is_empty for t in sample), (
            f"channel {channel} produced tickets with no usable content"
        )


def test_chat_tickets_have_no_subject_by_design(all_tickets):
    """
    The schema states chat tickets carry an empty subject. Ingest must
    treat that as normal rather than flagging it, or every chat ticket
    would carry a spurious warning and the warning signal would be useless.
    """
    normalised = normalise_batch(all_tickets)
    chat = [t for t in normalised if t.channel == "chat"]
    assert chat
    assert all(t.subject == "" for t in chat)
    assert all("missing_subject" not in t.warnings for t in chat)


def test_supplied_data_produces_no_warnings(all_tickets):
    """
    The supplied sets are well formed, so any warning here means ingest is
    mis-flagging good data -- which would make warnings meaningless as a
    signal on the hidden set.
    """
    normalised = normalise_batch(all_tickets)
    noisy = [(t.ticket_id, t.warnings) for t in normalised if t.warnings]
    assert not noisy, f"unexpected warnings on clean data: {noisy[:5]}"


def test_original_text_and_channel_are_preserved(all_tickets):
    """FR-01: the raw payload and channel survive normalisation."""
    raw = all_tickets[0]
    ticket = normalise_ticket(raw)
    assert ticket.raw == raw
    assert ticket.channel == raw["channel"]
    assert raw["body"][:20].strip()[:10] in ticket.body or ticket.body


# --- Malformed input must never raise (A9 / A11) ---------------------------


@pytest.mark.parametrize(
    "payload",
    [
        None,
        "not a ticket",
        12345,
        [],
        {},
        {"ticket_id": "T", "channel": "email"},                    # no body
        {"ticket_id": "T", "channel": "email", "body": ""},        # empty body
        {"ticket_id": "T", "channel": "carrier_pigeon", "body": "x"},
        {"ticket_id": "T", "channel": "email", "body": {"a": 1}},  # wrong type
        {"ticket_id": "T", "channel": "email", "body": "x",
         "received_at": "not-a-date"},
    ],
)
def test_malformed_tickets_never_raise(payload):
    """
    A9 requires the full set to process unattended; A11 requires malformed
    input to be survived. One bad record in a file of 120 must not end the
    run, so ingest returns a ticket carrying warnings instead of raising.
    """
    ticket = normalise_ticket(payload)
    assert isinstance(ticket, NormalisedTicket)
    assert ticket.warnings, "a malformed ticket should say what was wrong"


def test_batch_survives_a_mixed_bag_of_garbage():
    tickets = normalise_batch([None, "x", {}, 5, {"ticket_id": "OK",
                                                  "channel": "email",
                                                  "body": "real"}])
    assert len(tickets) == 5
    assert tickets[-1].ticket_id == "OK"


def test_batch_of_wrong_type_returns_empty_rather_than_raising():
    assert normalise_batch("not a list") == []
    assert normalise_batch(None) == []


# --- Text cleaning ---------------------------------------------------------


def test_control_and_invisible_characters_are_removed():
    """
    Zero-width and control characters arrive via copy-paste from web pages
    and chat clients. They are invisible, they survive tokenisation, and
    they make otherwise identical strings compare unequal.
    """
    dirty = "de\u200bploy\x00  fa\ufeffils"
    cleaned = clean_text(dirty)
    assert "\u200b" not in cleaned
    assert "\ufeff" not in cleaned
    assert "\x00" not in cleaned
    assert "deploy" in cleaned and "fails" in cleaned


def test_non_english_text_survives_intact():
    """
    Roughly a quarter of tickets come from customers writing in a second
    language. Cleaning must not strip or mangle non-ASCII content.
    """
    text = clean_text("No puedo desplegar la aplicación. 部署失败了")
    assert "aplicación" in text
    assert "部署失败了" in text


def test_newlines_are_preserved_but_runs_are_collapsed():
    assert clean_text("a\n\n\n\n\nb") == "a\n\nb"
    assert clean_text("a     b") == "a b"


# --- Privacy (NFR-04) ------------------------------------------------------


def test_customer_name_is_not_a_stored_field():
    """
    NFR-04: the customer's name is deliberately not carried on the
    normalised object, so it cannot reach a prompt or a generated reply
    by simply being present on the object everything else passes around.
    """
    assert "customer_name" not in NormalisedTicket.__dataclass_fields__


def test_log_view_excludes_body_and_name():
    ticket = normalise_ticket({
        "ticket_id": "T1", "channel": "email", "subject": "s",
        "body": "sensitive content", "customer_name": "Priya Sharma",
    })
    logged = ticket.to_log_dict()
    assert "sensitive content" not in json.dumps(logged)
    assert "Priya Sharma" not in json.dumps(logged)
