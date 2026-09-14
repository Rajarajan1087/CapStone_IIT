"""
Day-one setup check.

Run this first, before anything else. It answers three questions in order,
and stops at the first one that fails:

  1. Is configuration loading, and where is it pointing?
  2. Does ingest normalise a ticket from each of the four channels? (A2)
  3. Does the model provider answer? (B-01 definition of done)

Question 3 is allowed to fail. The pipeline is built to run without model
access, degrading to escalation, so a missing key is reported as a
degraded mode rather than as a broken install.

    python -m scripts.check_setup
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Allow running as a plain script as well as with -m.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import PROJECT_ROOT, settings  # noqa: E402
from src.ingest import KNOWN_CHANNELS, normalise_batch  # noqa: E402
from src.model_client import ModelClient  # noqa: E402

TICK = "[ ok ]"
CROSS = "[fail]"
WARN = "[warn]"


def section(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def check_configuration() -> bool:
    section("1. Configuration")
    print(f"  project root          {PROJECT_ROOT}")
    print(f"  provider              {settings.model_provider}")
    print(f"  model                 {settings.model_name}")
    print(f"  embedding model       {settings.embedding_model}")
    print(f"  chroma path           {settings.chroma_path}")
    print(f"  confidence threshold  {settings.confidence_threshold}")
    print(f"  retrieval top k       {settings.retrieval_top_k}")
    print(f"  response cache        {'on' if settings.cache_enabled else 'off'}")
    print(f"  api key configured    {'yes' if settings.has_model_access else 'no'}"
          f" (for {settings.model_provider})")
    print(f"{TICK} configuration loaded")
    return True


def check_ingest() -> bool:
    """A2: one ticket from each channel, normalised and printed."""
    section("2. Ingest — one normalised ticket per channel (A2)")

    data_file = PROJECT_ROOT / "data" / "development_tickets.json"
    if not data_file.exists():
        print(f"{CROSS} {data_file} not found")
        return False

    raw = json.loads(data_file.read_text(encoding="utf-8"))
    tickets = normalise_batch(raw)
    print(f"  normalised {len(tickets)} of {len(raw)} tickets\n")

    ok = True
    for channel in KNOWN_CHANNELS:
        sample = next((t for t in tickets if t.channel == channel), None)
        if sample is None:
            print(f"{CROSS} no ticket found on channel '{channel}'")
            ok = False
            continue

        preview = sample.text.replace("\n", " ")
        if len(preview) > 96:
            preview = preview[:93] + "..."

        print(f"  channel: {channel}")
        print(f"    ticket_id   {sample.ticket_id}")
        print(f"    subject     {sample.subject or '(none — expected on chat)'}")
        print(f"    tier/region {sample.customer_tier} / {sample.customer_region}")
        print(f"    fluency     {sample.language_fluency}")
        print(f"    received_at {sample.received_at}")
        print(f"    text        {preview}")
        print(f"    warnings    {sample.warnings or 'none'}")
        print()

    # The whole set must survive, since the evaluation run is unattended.
    empty = [t for t in tickets if t.is_empty]
    warned = [t for t in tickets if t.warnings]
    if empty:
        print(f"{WARN} {len(empty)} ticket(s) produced no usable content")
    if warned:
        print(f"{WARN} {len(warned)} ticket(s) carried warnings")
    if not empty and not warned:
        print(f"{TICK} all four channels normalised cleanly, no warnings")

    return ok


def check_model_access() -> bool:
    """B-01 definition of done. Allowed to fail — degradation is by design."""
    section("3. Model access")

    client = ModelClient()
    if not client.available:
        print(f"{WARN} no usable API key configured.")
        print("       The pipeline will run in degraded mode: every ticket that")
        print("       would need a model opinion is escalated to a human instead.")
        print(f"       To enable it: copy .env.example to .env, set")
        print(f"       MODEL_PROVIDER and the matching key for it.")
        return False

    print(f"  calling {settings.model_name} ...")
    result = client.complete("Reply with the single word: ready", max_tokens=16)

    if result.ok:
        print(f"{TICK} model replied: {result.text[:60]!r}")
        print(f"       attempts {result.attempts}, "
              f"{result.latency_seconds:.2f}s, "
              f"{'from cache' if result.from_cache else 'live call'}")
        return True

    print(f"{CROSS} {result.reason}")
    print(f"       attempts {result.attempts}. The pipeline still runs, degraded.")
    return False


def main() -> int:
    print("CloudServe support system — setup check")

    config_ok = check_configuration()
    ingest_ok = check_ingest()
    model_ok = check_model_access()

    section("Summary")
    print(f"  configuration  {'ok' if config_ok else 'FAILED'}")
    print(f"  ingest (A2)    {'ok' if ingest_ok else 'FAILED'}")
    print(f"  model access   {'ok' if model_ok else 'degraded (not fatal)'}")

    # Model access is deliberately excluded from the exit code: a run
    # without a key is a supported mode, and CI has no key.
    if config_ok and ingest_ok:
        print("\nReady to build on. Day 1 checkpoint met.")
        return 0
    print("\nSetup incomplete — see the failures above.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
