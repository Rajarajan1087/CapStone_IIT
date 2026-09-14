"""
Tests for the model client's resilience behaviour.

Covers the provider-failure half of TC-14 (NFR-02, acceptance criterion
A11): the system must degrade and continue under timeout, outage, rate
limiting and a rejected key, rather than stopping.

These tests never touch the network. Failure modes are injected, so the
suite is deterministic and runs in CI without a key.
"""
from __future__ import annotations

import json

import pytest

from src.model_client import ModelClient, ModelResponse, ModelUnavailable


# --- Degradation without a key --------------------------------------------


def test_missing_key_degrades_rather_than_raising(tmp_path):
    """A11: no key configured is an operating mode, not a crash."""
    client = ModelClient(api_key="", cache_enabled=False, cache_path=tmp_path)
    result = client.complete("anything")
    assert isinstance(result, ModelResponse)
    assert result.ok is False
    assert result.degraded is True
    assert "no API key" in result.reason


def test_placeholder_key_is_treated_as_absent(tmp_path):
    """
    The shipped .env.example contains 'your_key_here'. A user who copies it
    without editing should get a clear degraded reason, not a 401 loop that
    burns the retry budget on every ticket in the run.
    """
    client = ModelClient(api_key="your_key_here", cache_enabled=False,
                         cache_path=tmp_path)
    assert client.available is False
    assert client.complete("x").ok is False


def test_call_or_raise_does_raise_when_asked(tmp_path):
    """
    The pipeline always degrades, but setup checks want to fail loudly.
    Both behaviours must be available, and distinct.
    """
    client = ModelClient(api_key="", cache_enabled=False, cache_path=tmp_path)
    with pytest.raises(ModelUnavailable):
        client.call_or_raise("x")


# --- Retry and backoff on injected failures --------------------------------


class _FakeResponse:
    def __init__(self, status_code, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}

    def json(self):
        return self._payload


def _ok_payload(text):
    return {"choices": [{"message": {"content": text}}]}


def test_rate_limit_is_retried_then_succeeds(monkeypatch, tmp_path):
    """
    A11: 429 is an expected condition on a free tier, not an error. The
    client must back off and try again rather than failing the ticket.
    """
    calls = {"n": 0}

    def fake_post(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            return _FakeResponse(429, headers={"Retry-After": "0"})
        return _FakeResponse(200, _ok_payload("recovered"))

    import src.model_client as mc
    monkeypatch.setattr(mc.time, "sleep", lambda *_: None)  # no real waiting
    fake_requests = type("R", (), {"post": staticmethod(fake_post)})
    monkeypatch.setitem(__import__("sys").modules, "requests", fake_requests)

    client = ModelClient(api_key="k", cache_enabled=False, cache_path=tmp_path)
    result = client.complete("x")

    assert result.ok is True
    assert result.text == "recovered"
    assert calls["n"] == 3
    assert client.retries_performed == 2


def test_total_outage_degrades_after_exhausting_retries(monkeypatch, tmp_path):
    """
    A11's hardest case: the provider is disconnected entirely. The client
    must exhaust its retries and then return a degraded result, so the
    pipeline can escalate the ticket and keep the run moving.
    """
    def fake_post(*args, **kwargs):
        raise ConnectionError("provider unreachable")

    import src.model_client as mc
    monkeypatch.setattr(mc.time, "sleep", lambda *_: None)
    fake_requests = type("R", (), {"post": staticmethod(fake_post)})
    monkeypatch.setitem(__import__("sys").modules, "requests", fake_requests)

    client = ModelClient(api_key="k", cache_enabled=False, cache_path=tmp_path)
    result = client.complete("x")

    assert result.ok is False
    assert "could not reach the model provider" in result.reason
    assert client.calls_failed == 1


def test_rejected_key_is_not_retried(monkeypatch, tmp_path):
    """
    A 401 will fail identically on every attempt. Retrying it wastes the
    run's time budget, so it must stop immediately and say so plainly.
    """
    calls = {"n": 0}

    def fake_post(*args, **kwargs):
        calls["n"] += 1
        return _FakeResponse(401)

    import src.model_client as mc
    monkeypatch.setattr(mc.time, "sleep", lambda *_: None)
    fake_requests = type("R", (), {"post": staticmethod(fake_post)})
    monkeypatch.setitem(__import__("sys").modules, "requests", fake_requests)

    client = ModelClient(api_key="k", cache_enabled=False, cache_path=tmp_path)
    result = client.complete("x")

    assert result.ok is False
    assert "rejected the API key" in result.reason
    assert calls["n"] == 1, "a bad key must not be retried"


def test_malformed_provider_response_degrades(monkeypatch, tmp_path):
    """A 200 carrying an unexpected shape must not raise a KeyError."""
    def fake_post(*args, **kwargs):
        return _FakeResponse(200, {"unexpected": "shape"})

    import src.model_client as mc
    fake_requests = type("R", (), {"post": staticmethod(fake_post)})
    monkeypatch.setitem(__import__("sys").modules, "requests", fake_requests)

    client = ModelClient(api_key="k", cache_enabled=False, cache_path=tmp_path)
    result = client.complete("x")
    assert result.ok is False
    assert "unexpected shape" in result.reason


# --- Caching ---------------------------------------------------------------


def test_cache_hit_avoids_the_network(tmp_path):
    """
    Caching saves free-tier allowance and makes an evaluation run
    reproducible. A hit must not require the provider at all.
    """
    client = ModelClient(api_key="k", cache_enabled=True, cache_path=tmp_path)
    key = client._cache_key("hello", 0.0, 800)
    client._cache_write(key, "cached answer")

    result = client.complete("hello", temperature=0.0, max_tokens=800)
    assert result.ok is True
    assert result.from_cache is True
    assert result.text == "cached answer"
    assert client.calls_served_from_cache == 1


def test_cache_key_includes_sampling_parameters(tmp_path):
    """
    The same prompt at a different temperature is a different request.
    A key that ignored that would serve results from a configuration the
    run is no longer using.
    """
    client = ModelClient(api_key="k", cache_enabled=True, cache_path=tmp_path)
    assert client._cache_key("p", 0.0, 800) != client._cache_key("p", 0.7, 800)
    assert client._cache_key("p", 0.0, 800) != client._cache_key("p", 0.0, 400)


def test_corrupt_cache_entry_is_a_miss_not_a_crash(tmp_path):
    client = ModelClient(api_key="", cache_enabled=True, cache_path=tmp_path)
    key = client._cache_key("hello", 0.0, 800)
    (tmp_path / f"{key}.json").write_text("{ this is not json")
    result = client.complete("hello")
    assert result.from_cache is False
    assert result.ok is False  # degraded because no key, but did not raise


def test_stats_are_reported_for_the_metrics_report(tmp_path):
    client = ModelClient(api_key="", cache_enabled=False, cache_path=tmp_path)
    client.complete("a")
    stats = client.stats()
    assert set(stats) == {
        "calls_attempted", "calls_served_from_cache",
        "calls_failed", "retries_performed",
    }
    assert stats["calls_attempted"] == 1
