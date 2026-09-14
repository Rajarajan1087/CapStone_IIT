"""
Tests for the kill switch.

Required by the Governance Framework: a way to stop the system answering
automatically, immediately, without a deployment. The framework's test for
real governance is whether a control exists rather than a hope, so the
control is tested like any other component.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from src import kill_switch
from src.corpus import build_chunks, load_corpus
from src.logging_store import DecisionLog
from src.pipeline import Pipeline
from src.retrieve import Retriever

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def pipeline() -> Pipeline:
    articles = load_corpus(ROOT / "data" / "documentation.json")
    retriever = Retriever(build_chunks(articles, "section_aware"),
                          score_threshold=0.12)
    return Pipeline(retriever,
                    decision_log=DecisionLog(db_path=tempfile.mktemp(suffix=".db")))


@pytest.fixture(scope="module")
def tickets() -> list[dict]:
    return json.loads(
        (ROOT / "data" / "validation_tickets.json").read_text())[:8]


@pytest.fixture(autouse=True)
def _clean_switch():
    """Every test starts and ends with the switch released."""
    kill_switch.release()
    yield
    kill_switch.release()


# --- The control itself ----------------------------------------------------


def test_switch_is_released_by_default():
    """A system that starts up refusing to work is not useful either."""
    assert kill_switch.check().engaged is False
    assert kill_switch.check().automatic_answering_allowed is True


def test_engaging_stops_automatic_answering(pipeline, tickets):
    """
    The control must actually block automated replies, not merely warn.
    Every ticket still receives an outcome, so an unattended run completes
    (A9) -- it is just that no reply reaches a customer.
    """
    before = [pipeline.process(t) for t in tickets]
    assert any(o.answered for o in before), "fixture should answer some tickets"

    kill_switch.engage("automated test")
    after = [pipeline.process(t) for t in tickets]

    assert not any(o.answered for o in after), "no reply may be released"
    assert all(o.action == "escalate" for o in after)
    assert all(o.routing_gate == "kill_switch_engaged" for o in after)


def test_releasing_resumes_without_a_redeploy(pipeline, tickets):
    """
    The point of a file-based switch is that recovery needs no engineer
    and no deployment either.
    """
    kill_switch.engage("automated test")
    assert not any(pipeline.process(t).answered for t in tickets)

    kill_switch.release()
    assert any(pipeline.process(t).answered for t in tickets)


def test_switch_takes_effect_mid_run(pipeline, tickets):
    """
    A switch consulted only at startup cannot stop a run that is already
    going, which is precisely the situation it exists for. It is therefore
    read per ticket.
    """
    first = pipeline.process(tickets[0])
    kill_switch.engage("engaged mid-run")
    second = pipeline.process(tickets[0])

    assert second.routing_gate == "kill_switch_engaged"
    assert second.answered is False
    assert first.routing_gate != "kill_switch_engaged"


def test_environment_variable_also_engages(monkeypatch):
    """Container deployments cannot always write a file into a running image."""
    monkeypatch.setenv(kill_switch.ENV_VARIABLE, "true")
    state = kill_switch.check()
    assert state.engaged is True
    assert state.source == "environment"


def test_a_note_is_carried_into_the_reason(tmp_path):
    """
    Whoever engaged it can say why, and that explanation reaches the
    decision log rather than living in someone's memory.
    """
    path = tmp_path / "KILL_SWITCH_ENGAGED"
    kill_switch.engage("Wrong answers reported on billing tickets", path)
    state = kill_switch.check(path)
    assert state.engaged is True
    assert "billing tickets" in state.reason


def test_it_fails_closed_when_the_state_cannot_be_read(monkeypatch, tmp_path):
    """
    The most important property. A control whose failure mode is "carry on
    answering customers" is worse than no control, because it creates
    confidence that is not warranted.
    """
    path = tmp_path / "KILL_SWITCH_ENGAGED"

    def explode(self):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "exists", explode)
    state = kill_switch.check(path)

    assert state.engaged is True, "an unreadable switch must be treated as ON"
    assert state.source == "check_failed"
    assert "fails closed" in state.reason


def test_the_decision_is_logged(pipeline, tickets):
    """
    A8: every decision is recorded. Engaging the switch is a decision with
    customer impact, so it is logged like any other.
    """
    kill_switch.engage("logging check")
    outcome = pipeline.process(tickets[0])
    assert outcome.routing_gate == "kill_switch_engaged"
    assert pipeline.log.count_for_ticket(outcome.ticket_id) > 0
