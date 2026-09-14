"""
Model access with retry, backoff, caching and graceful degradation.

Serves NFR-02. This module is where acceptance criterion A11 is actually
won or lost: the system must keep processing when the provider times out,
rate-limits, or disappears entirely, rather than stopping.

The contract is deliberately narrow. Callers ask for a completion and get
back a ModelResponse that either carries text or explains, in a value
rather than an exception, why it does not. No caller is ever required to
wrap a call in try/except to keep the run alive.

Three behaviours matter here:

  Retry with backoff -- free tiers throttle, and a 429 is an expected
  operating condition rather than an error. Retries are spaced with
  exponential backoff plus jitter so that a burst of tickets does not
  synchronise into a second burst of retries.

  Caching -- responses are cached on disk by a hash of the exact request.
  This saves free-tier allowance, makes an evaluation run reproducible,
  and means a re-run after a crash does not pay again for work already
  done. The pack explicitly encourages this.

  Degradation -- when the provider is unreachable, calls return
  ok=False with a reason. The pipeline treats that as "no model opinion
  available" and routes the ticket to a human, which is the safe default
  and keeps the unattended run moving.
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.config import settings

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# HTTP statuses worth trying again. 429 is rate limiting; 5xx are provider
# side. Everything else (401 bad key, 400 bad request) will fail again
# identically, so retrying only wastes the run's time budget.
RETRYABLE_STATUSES = frozenset({408, 409, 429, 500, 502, 503, 504})


@dataclass(frozen=True)
class ModelResponse:
    """
    The outcome of a model call, successful or not.

    `ok` False is a normal, expected result, not an exception in disguise.
    `reason` is written to be readable in a decision log by someone who is
    not the engineer -- a support manager reading why a ticket escalated.
    """

    ok: bool
    text: str = ""
    reason: str = ""
    from_cache: bool = False
    attempts: int = 0
    latency_seconds: float = 0.0

    @property
    def degraded(self) -> bool:
        """True when the pipeline should fall back to its no-model path."""
        return not self.ok


class ModelUnavailable(Exception):
    """
    Raised only by call_or_raise, for callers that genuinely cannot proceed.

    The pipeline itself does not use this. It exists so that a setup check
    or a one-off script can fail loudly when that is the desired behaviour.
    """


class ModelClient:
    """
    A thin, resilient wrapper over the chat-completions endpoint.

    Deliberately not a LangChain abstraction: the pipeline needs exactly
    one operation (prompt in, text out) and the retry, caching and
    degradation semantics above are the substance of this component. A
    wrapper around a wrapper would obscure them.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        cache_enabled: bool | None = None,
        cache_path: Path | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.openrouter_api_key
        self.model = model or settings.model_name
        self.cache_enabled = (
            settings.cache_enabled if cache_enabled is None else cache_enabled
        )
        self.cache_path = cache_path or settings.cache_path
        if self.cache_enabled:
            self.cache_path.mkdir(parents=True, exist_ok=True)

        # Counters surfaced in the metrics report so that provider
        # behaviour during a run is visible rather than inferred.
        self.calls_attempted = 0
        self.calls_served_from_cache = 0
        self.calls_failed = 0
        self.retries_performed = 0

    # -- availability --------------------------------------------------

    @property
    def available(self) -> bool:
        """
        Whether a live call is even worth attempting.

        False when no usable key is configured, or when `requests` is not
        installed. Checked before each call so that a run without a key
        degrades immediately rather than burning the retry budget on
        calls that cannot succeed.
        """
        if not self.api_key or self.api_key == "your_key_here":
            return False
        try:
            import requests  # noqa: F401
        except ImportError:
            return False
        return True

    # -- caching -------------------------------------------------------

    def _cache_key(self, prompt: str, temperature: float, max_tokens: int) -> str:
        """
        Hash the full request, not just the prompt.

        Model name and sampling parameters are part of the key because the
        same prompt at a different temperature is a different request, and
        a cache that ignored that would silently serve results from a
        configuration the run is no longer using.
        """
        payload = json.dumps(
            {
                "model": self.model,
                "prompt": prompt,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _cache_read(self, key: str) -> str | None:
        if not self.cache_enabled:
            return None
        path = self.cache_path / f"{key}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))["text"]
        except (OSError, ValueError, KeyError):
            # A corrupt cache entry is a cache miss, never a crash.
            return None

    def _cache_write(self, key: str, text: str) -> None:
        if not self.cache_enabled:
            return
        try:
            (self.cache_path / f"{key}.json").write_text(
                json.dumps({"text": text}), encoding="utf-8"
            )
        except OSError:
            # Failing to cache is not failing to answer.
            logger.debug("could not write cache entry %s", key)

    # -- the call ------------------------------------------------------

    def complete(
        self,
        prompt: str,
        *,
        temperature: float = 0.0,
        max_tokens: int = 800,
        system: str | None = None,
    ) -> ModelResponse:
        """
        Send a prompt and return the model's text, or a reason it is absent.

        Temperature defaults to 0.0 because every build prompt in this
        system parses its output as JSON and routes on it. Determinism
        matters more than variety here, and A5 requires that the same
        input produce the same routing decision.

        Never raises. That is the point of this method.
        """
        started = time.monotonic()
        self.calls_attempted += 1

        cache_key = self._cache_key(prompt, temperature, max_tokens)
        cached = self._cache_read(cache_key)
        if cached is not None:
            self.calls_served_from_cache += 1
            return ModelResponse(
                ok=True,
                text=cached,
                from_cache=True,
                attempts=0,
                latency_seconds=time.monotonic() - started,
            )

        if not self.available:
            self.calls_failed += 1
            reason = (
                "no API key configured"
                if not self.api_key or self.api_key == "your_key_here"
                else "the requests library is not installed"
            )
            return ModelResponse(
                ok=False,
                reason=f"Model not consulted because {reason}.",
                latency_seconds=time.monotonic() - started,
            )

        import requests  # imported lazily so the module loads without it

        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        last_reason = "unknown error"
        for attempt in range(1, settings.max_retries + 1):
            try:
                response = requests.post(
                    OPENROUTER_URL,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": messages,
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                    },
                    timeout=settings.request_timeout_seconds,
                )
            except Exception as exc:  # noqa: BLE001
                # Covers timeouts, DNS failure, refused connections and the
                # provider being entirely unreachable -- the A11 scenario.
                last_reason = f"could not reach the model provider ({type(exc).__name__})"
                if attempt < settings.max_retries:
                    self.retries_performed += 1
                    self._sleep_before_retry(attempt)
                    continue
                break

            if response.status_code == 200:
                try:
                    text = response.json()["choices"][0]["message"]["content"]
                except (ValueError, KeyError, IndexError, TypeError):
                    last_reason = "the provider returned a response in an unexpected shape"
                    break
                text = (text or "").strip()
                self._cache_write(cache_key, text)
                return ModelResponse(
                    ok=True,
                    text=text,
                    attempts=attempt,
                    latency_seconds=time.monotonic() - started,
                )

            if response.status_code in RETRYABLE_STATUSES:
                last_reason = (
                    f"the model provider returned {response.status_code}"
                    + (" (rate limited)" if response.status_code == 429 else "")
                )
                if attempt < settings.max_retries:
                    self.retries_performed += 1
                    self._sleep_before_retry(attempt, response=response)
                    continue
                break

            # Non-retryable: a bad key or a malformed request will fail
            # identically next time, so stop and report it plainly.
            if response.status_code == 401:
                last_reason = "the model provider rejected the API key"
            else:
                last_reason = f"the model provider returned {response.status_code}"
            break

        self.calls_failed += 1
        return ModelResponse(
            ok=False,
            reason=f"Model not consulted because {last_reason}.",
            attempts=attempt,
            latency_seconds=time.monotonic() - started,
        )

    def _sleep_before_retry(self, attempt: int, response: Any = None) -> None:
        """
        Wait before retrying, honouring Retry-After when the provider sends it.

        Exponential backoff with jitter. The jitter matters during a batch
        run: without it, many tickets hitting a rate limit at the same
        moment would all retry at the same moment, reproducing the burst
        that caused the limit.
        """
        delay = settings.retry_backoff_seconds * (2 ** (attempt - 1))

        if response is not None:
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    delay = max(delay, float(retry_after))
                except ValueError:
                    pass

        delay = min(delay, 60.0) * (0.5 + random.random())
        logger.info("retrying model call in %.1fs (attempt %d)", delay, attempt)
        time.sleep(delay)

    def call_or_raise(self, prompt: str, **kwargs: Any) -> str:
        """
        Convenience wrapper for setup checks and scripts that must fail loudly.

        Not used by the pipeline, which always prefers to degrade.
        """
        result = self.complete(prompt, **kwargs)
        if not result.ok:
            raise ModelUnavailable(result.reason)
        return result.text

    def stats(self) -> dict[str, int]:
        """Counters for the metrics report's governance and technical sections."""
        return {
            "calls_attempted": self.calls_attempted,
            "calls_served_from_cache": self.calls_served_from_cache,
            "calls_failed": self.calls_failed,
            "retries_performed": self.retries_performed,
        }


# A single shared client. The pipeline reuses one instance so that cache
# hits and the counters above accumulate across a whole run.
default_client = ModelClient()
