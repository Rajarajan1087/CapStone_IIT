"""
Evaluation harness: process a whole ticket file, unattended, and measure it.

Serves FR-11. Checked by acceptance criteria A9 and A10.

    python -m evaluation.harness --input data/validation_tickets.json \
                                 --output evaluation/results/

The input path is an ARGUMENT, never a constant. After submission this is
run against a hidden set of 120 tickets that does not exist in this
repository, and a harness that only works against its author's own copy of
the data cannot be run at all.

Two guarantees:

  Nothing stops the run.  Every ticket produces an outcome. A ticket that
  fails in an unanticipated way is recorded as an escalation with the
  error attached, and processing continues to the next one.

  Nothing is hand-calculated.  Every figure in the metrics report is
  computed here, from the run's own results. Labels are used for scoring
  when present and ignored when absent, so the same command works on a
  labelled development file and on an unlabelled production one.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import settings, sqlite_path_from_url  # noqa: E402
from src.corpus import build_chunks, load_corpus  # noqa: E402
from src.logging_store import DecisionLog  # noqa: E402
from src.model_client import default_client  # noqa: E402
from src.pipeline import Pipeline, TicketOutcome  # noqa: E402
from src.retrieve import Retriever  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_tickets(path: str) -> list[Any]:
    file = Path(path)
    if not file.exists():
        print(f"ERROR: input file not found: {path}", file=sys.stderr)
        raise SystemExit(2)
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except ValueError as exc:
        print(f"ERROR: {path} is not valid JSON: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    if not isinstance(data, list):
        print(f"ERROR: expected a JSON list of tickets in {path}", file=sys.stderr)
        raise SystemExit(2)
    return data


def _safe_div(numerator: float, denominator: float) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _per_class_scores(pairs: list[tuple[str, str]]) -> dict[str, dict[str, float]]:
    """
    Precision, recall and F1 per intent class.

    Reported per class rather than only in aggregate because the 22 classes
    are unevenly distributed, and an aggregate figure hides a class the
    system never gets right (NFR-03).
    """
    classes = sorted({c for pair in pairs for c in pair})
    scores: dict[str, dict[str, float]] = {}
    for cls in classes:
        tp = sum(1 for true, pred in pairs if pred == cls and true == cls)
        fp = sum(1 for true, pred in pairs if pred == cls and true != cls)
        fn = sum(1 for true, pred in pairs if pred != cls and true == cls)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        scores[cls] = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "support": tp + fn,
        }
    return scores


def _segment(outcomes: list[TicketOutcome], attribute: str) -> dict[str, dict]:
    """
    Fairness segmentation (NFR-06).

    Reported by tier, region and language fluency. A difference found and
    reported is worth more than a table showing no variation anywhere,
    which usually means the analysis was not sensitive enough to detect it.
    """
    groups: dict[str, list[TicketOutcome]] = defaultdict(list)
    for outcome in outcomes:
        groups[getattr(outcome, attribute)].append(outcome)

    result = {}
    for name, group in sorted(groups.items()):
        answered = sum(1 for o in group if o.answered)
        blocked = sum(1 for o in group if o.guardrail_blocked)
        cited = sum(1 for o in group if o.citations)
        result[name] = {
            "tickets": len(group),
            "auto_respond_rate": _safe_div(answered, len(group)),
            "escalation_rate": _safe_div(len(group) - answered, len(group)),
            "guardrail_block_rate": _safe_div(blocked, len(group)),
            "citation_rate": _safe_div(cited, len(group)),
            "mean_confidence": round(
                statistics.mean([o.confidence for o in group]), 4) if group else None,
        }
    return result


def build_metrics(outcomes: list[TicketOutcome], raw_tickets: list[Any],
                  elapsed: float, decisions_logged: int) -> dict[str, Any]:
    """Compute every figure the Build Specification requires."""
    total = len(outcomes)
    answered = [o for o in outcomes if o.answered]
    escalated = [o for o in outcomes if not o.answered]
    blocked = [o for o in outcomes if o.guardrail_blocked]
    errored = [o for o in outcomes if o.processing_error]
    degraded = [o for o in outcomes if o.degraded]
    latencies = sorted(o.latency_seconds for o in outcomes)

    # Labels are optional: present in development and validation files,
    # absent in a production one. Scoring adapts rather than failing.
    labelled: list[tuple[TicketOutcome, dict]] = []
    for outcome, raw in zip(outcomes, raw_tickets):
        if isinstance(raw, dict) and isinstance(raw.get("labels"), dict):
            labelled.append((outcome, raw["labels"]))

    technical: dict[str, Any] = {
        "retrieval_hit_rate": _safe_div(
            sum(1 for o in outcomes if o.citations), total),
        "latency_p50_seconds": round(statistics.median(latencies), 4) if latencies else None,
        "latency_p95_seconds": (round(latencies[int(len(latencies) * 0.95) - 1], 4)
                                if len(latencies) >= 20 else
                                (round(max(latencies), 4) if latencies else None)),
        "latency_mean_seconds": round(statistics.mean(latencies), 4) if latencies else None,
    }
    business: dict[str, Any] = {
        "escalation_rate": _safe_div(len(escalated), total),
        "auto_respond_rate": _safe_div(len(answered), total),
    }
    governance: dict[str, Any] = {
        "decisions_logged": decisions_logged,
        "tickets_processed": total,
        "decisions_reconcile": decisions_logged >= total,
        "guardrail_activations_by_type": dict(Counter(
            v for o in outcomes for v in o.guardrail_violations)),
        "guardrail_blocks_total": len(blocked),
        "private_data_detections": sum(
            1 for o in outcomes if "private_data" in o.guardrail_violations),
        "tickets_processed_degraded": len(degraded),
        "processing_errors": len(errored),
    }

    if labelled:
        pairs = [(labels["intent"], o.intent) for o, labels in labelled]
        correct_intent = sum(1 for true, pred in pairs if true == pred)
        technical["classification_accuracy"] = _safe_div(correct_intent, len(pairs))
        technical["classification_by_class"] = _per_class_scores(pairs)

        routing_correct = sum(
            1 for o, labels in labelled if o.action == labels.get("expected_route"))
        business["routing_accuracy"] = _safe_div(routing_correct, len(labelled))

        # First contact resolution: answered automatically, and the label
        # agrees it should have been. The historical baseline is 44.1%.
        fcr = sum(1 for o, labels in labelled
                  if o.answered and labels.get("expected_route") == "auto_respond")
        business["first_contact_resolution"] = _safe_div(fcr, len(labelled))
        business["baseline_first_contact_resolution"] = 0.441
        business["baseline_escalation_rate"] = 0.559

        # The governance number that matters most: a ticket that must never
        # be auto-answered, that was. Target is zero.
        violations = sum(1 for o, labels in labelled
                         if o.answered and labels.get("must_not_auto_respond"))
        governance["must_not_auto_respond_violations"] = violations
        governance["must_not_auto_respond_total"] = sum(
            1 for _, labels in labelled if labels.get("must_not_auto_respond"))

        # Citation accuracy: did we cite a document the label expects?
        with_expected = [(o, labels) for o, labels in labelled
                         if labels.get("expected_doc_ids")]
        if with_expected:
            hits = sum(1 for o, labels in with_expected
                       if set(o.citations) & set(labels["expected_doc_ids"]))
            technical["citation_accuracy"] = _safe_div(hits, len(with_expected))

    return {
        "run": {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": round(elapsed, 2),
            "tickets_per_second": _safe_div(total, elapsed),
            "model": settings.model_name,
            "model_available": default_client.available,
            "confidence_threshold": settings.confidence_threshold,
            "retrieval_score_threshold": settings.retrieval_score_threshold,
            "labels_present": bool(labelled),
        },
        "volume": {
            "tickets_processed": total,
            "answered_automatically": len(answered),
            "escalated": len(escalated),
            "blocked_by_guardrails": len(blocked),
            "processing_errors": len(errored),
            "by_channel": dict(Counter(o.channel for o in outcomes)),
            "by_routing_gate": dict(Counter(o.routing_gate for o in outcomes)),
        },
        "business": business,
        "technical": technical,
        "governance": governance,
        "fairness": {
            "by_customer_tier": _segment(outcomes, "customer_tier"),
            "by_customer_region": _segment(outcomes, "customer_region"),
            "by_language_fluency": _segment(outcomes, "language_fluency"),
        },
        "model_client": default_client.stats(),
    }


def run_evaluation(input_path: str, output_path: str,
                   quiet: bool = False) -> dict[str, Any]:
    """Process every ticket in the file and write the report. Unattended."""
    started = time.monotonic()
    raw_tickets = _load_tickets(input_path)

    out_dir = Path(output_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    articles = load_corpus(PROJECT_ROOT / "data" / "documentation.json")
    retriever = Retriever(build_chunks(articles, "section_aware"),
                          score_threshold=0.12,
                          top_k=settings.retrieval_top_k)

    db_path = sqlite_path_from_url(settings.database_url)
    decision_log = DecisionLog(db_path=str(db_path))
    pipeline = Pipeline(retriever, decision_log=decision_log)

    if not quiet:
        print(f"Processing {len(raw_tickets)} tickets from {input_path}")
        print(f"  model available: {default_client.available}")
        print(f"  confidence threshold: {settings.confidence_threshold}")

    outcomes: list[TicketOutcome] = []
    for index, raw in enumerate(raw_tickets, start=1):
        outcomes.append(pipeline.process(raw))
        if not quiet and index % 100 == 0:
            print(f"  {index}/{len(raw_tickets)} ...")

    elapsed = time.monotonic() - started

    try:
        decisions_logged = decision_log.count_all()
    except Exception:  # noqa: BLE001
        decisions_logged = 0

    metrics = build_metrics(outcomes, raw_tickets, elapsed, decisions_logged)

    (out_dir / "results.json").write_text(
        json.dumps([o.to_dict() for o in outcomes], indent=2), encoding="utf-8")
    (out_dir / "metrics_report.json").write_text(
        json.dumps(metrics, indent=2), encoding="utf-8")

    if not quiet:
        _print_summary(metrics, out_dir)
    return metrics


def _print_summary(m: dict[str, Any], out_dir: Path) -> None:
    v, b, g = m["volume"], m["business"], m["governance"]
    print()
    print(f"Processed {v['tickets_processed']} tickets in "
          f"{m['run']['elapsed_seconds']}s")
    print(f"  answered automatically  {v['answered_automatically']}")
    print(f"  escalated               {v['escalated']}")
    print(f"  blocked by guardrails   {v['blocked_by_guardrails']}")
    print(f"  processing errors       {v['processing_errors']}")
    if b.get("routing_accuracy") is not None:
        print(f"  routing accuracy        {b['routing_accuracy']:.1%}")
    if b.get("first_contact_resolution") is not None:
        print(f"  first contact resolution {b['first_contact_resolution']:.1%} "
              f"(baseline {b['baseline_first_contact_resolution']:.1%})")
    if g.get("must_not_auto_respond_violations") is not None:
        print(f"  never-automate violations {g['must_not_auto_respond_violations']} "
              f"(target 0)")
    print(f"  decisions logged        {g['decisions_logged']} "
          f"({'reconciles' if g['decisions_reconcile'] else 'DOES NOT RECONCILE'})")
    print()
    print(f"Results  {out_dir / 'results.json'}")
    print(f"Metrics  {out_dir / 'metrics_report.json'}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Process a ticket file end to end, unattended, and "
                    "produce a metrics report.")
    parser.add_argument("--input", required=True,
                        help="Path to a JSON list of tickets.")
    parser.add_argument("--output", required=True,
                        help="Directory for results.json and metrics_report.json.")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress progress output.")
    args = parser.parse_args()
    run_evaluation(args.input, args.output, quiet=args.quiet)


if __name__ == "__main__":
    main()
