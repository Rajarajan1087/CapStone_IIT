"""
Evaluation harness entry point.

Runs the full support pipeline (ingest -> classify -> retrieve -> route ->
generate -> validate) over a JSON file of tickets, unattended, and writes a
metrics report at the end. Must never be invoked interactively and must
never hardcode an input file, because it is run after submission against a
hidden ticket set with the same schema.

Usage:
    python -m evaluation.harness --input data/validation_tickets.json --output evaluation/results/

This file is a scaffold: the pipeline stages currently return placeholders.
Wire in src.ingest / src.classify / src.retrieve / src.route / src.generate /
src.guardrails as they are built, without changing this CLI contract.
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def load_tickets(input_path: str) -> list[dict]:
    path = Path(input_path)
    if not path.exists():
        print(f"ERROR: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)
    return json.loads(path.read_text())


def process_ticket(ticket: dict) -> dict:
    """
    Placeholder for the full pipeline. Replace with real calls into
    src.ingest, src.classify, src.retrieve, src.route, src.generate,
    src.guardrails once each stage exists. Must never raise on a single
    bad ticket -- catch, log, and continue (see A11).
    """
    return {
        "ticket_id": ticket.get("ticket_id", "UNKNOWN"),
        "channel": ticket.get("channel", "unknown"),
        "action_taken": "escalate",  # safe placeholder default
        "confidence": 0.0,
        "guardrail_blocked": False,
    }


def run_evaluation(input_path: str, output_path: str) -> None:
    started = time.monotonic()
    tickets = load_tickets(input_path)

    out_dir = Path(output_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    results = []
    errors = []
    for ticket in tickets:
        try:
            results.append(process_ticket(ticket))
        except Exception as exc:  # noqa: BLE001 -- must never crash the run (A11)
            errors.append({"ticket_id": ticket.get("ticket_id", "UNKNOWN"), "error": str(exc)})
            results.append({
                "ticket_id": ticket.get("ticket_id", "UNKNOWN"),
                "channel": ticket.get("channel", "unknown"),
                "action_taken": "escalate",
                "confidence": 0.0,
                "guardrail_blocked": False,
                "processing_error": str(exc),
            })

    elapsed = time.monotonic() - started

    volume = {
        "tickets_processed": len(results),
        "answered_automatically": sum(1 for r in results if r["action_taken"] == "auto_respond"),
        "escalated": sum(1 for r in results if r["action_taken"] == "escalate"),
        "blocked_by_guardrail": sum(1 for r in results if r.get("guardrail_blocked")),
    }

    metrics = {
        "run_started_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(elapsed, 2),
        "input_path": str(input_path),
        "volume": volume,
        "business": {
            "first_contact_resolution": None,  # TODO: wire once routing is real
            "mean_response_time_seconds": None,
            "median_response_time_seconds": None,
            "escalation_rate": volume["escalated"] / volume["tickets_processed"] if volume["tickets_processed"] else None,
        },
        "technical": {
            "classification_precision_by_class": {},
            "classification_recall_by_class": {},
            "retrieval_hit_rate": None,
            "latency_p50_seconds": None,
            "latency_p95_seconds": None,
        },
        "governance": {
            "decisions_logged": len(results),
            "guardrail_activations_by_type": {},
            "private_data_detections": 0,
        },
        "errors": errors,
    }

    results_path = out_dir / "results.json"
    metrics_path = out_dir / "metrics_report.json"
    results_path.write_text(json.dumps(results, indent=2))
    metrics_path.write_text(json.dumps(metrics, indent=2))

    print(f"Processed {len(results)} tickets in {elapsed:.1f}s")
    print(f"Results written to {results_path}")
    print(f"Metrics report written to {metrics_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the full support pipeline, unattended, over a ticket file.")
    parser.add_argument("--input", required=True, help="Path to a JSON file of tickets (same schema as development/validation sets).")
    parser.add_argument("--output", required=True, help="Directory to write results.json and metrics_report.json into.")
    args = parser.parse_args()
    run_evaluation(args.input, args.output)


if __name__ == "__main__":
    main()
