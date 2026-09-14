"""
Fairness audit (NFR-06).

Segments outcomes by customer tier, region and language fluency, and
reports the spread on each. Run against a metrics report produced by the
harness:

    python -m scripts.fairness_audit --metrics evaluation/results/gate/metrics_report.json

A difference found and reported is worth considerably more than a table
showing no variation anywhere, which usually means the analysis was not
sensitive enough to detect one. This script therefore states the widest
gap on each dimension explicitly rather than leaving a reader to spot it.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# A spread wider than this on a rate is called out as a finding requiring
# an explanation rather than left as an observation.
MATERIAL_GAP = 0.05

DIMENSIONS = {
    "by_customer_tier": "Customer tier",
    "by_customer_region": "Customer region",
    "by_language_fluency": "Language fluency",
}

RATES = {
    "auto_respond_rate": "automated answer rate",
    "citation_rate": "citation rate",
    "guardrail_block_rate": "guardrail block rate",
}


def audit(metrics: dict) -> list[str]:
    findings: list[str] = []
    fairness = metrics.get("fairness", {})

    for key, label in DIMENSIONS.items():
        segments = fairness.get(key, {})
        if not segments:
            continue
        print(f"\n{label}")
        print("-" * len(label))
        header = f"{'segment':<18}{'n':>6}" + "".join(f"{n:>22}" for n in RATES.values())
        print(header)
        for name, values in segments.items():
            row = f"{name:<18}{values['tickets']:>6}"
            for rate_key in RATES:
                value = values.get(rate_key)
                row += f"{(f'{value:.1%}' if value is not None else 'n/a'):>22}"
            print(row)

        for rate_key, rate_label in RATES.items():
            points = {n: v.get(rate_key) for n, v in segments.items()
                      if v.get(rate_key) is not None}
            if len(points) < 2:
                continue
            best = max(points, key=points.get)
            worst = min(points, key=points.get)
            gap = points[best] - points[worst]
            if gap >= MATERIAL_GAP:
                findings.append(
                    f"{label}: {rate_label} varies by {gap:.1%} "
                    f"({best} {points[best]:.1%} vs {worst} {points[worst]:.1%})"
                )
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Segment outcomes and report gaps.")
    parser.add_argument("--metrics", required=True,
                        help="Path to a metrics_report.json produced by the harness.")
    args = parser.parse_args()

    path = Path(args.metrics)
    if not path.exists():
        print(f"ERROR: {path} not found. Run the harness first.", file=sys.stderr)
        return 2

    metrics = json.loads(path.read_text(encoding="utf-8"))
    print("Fairness audit")
    print("==============")
    print(f"source: {path}")
    print(f"tickets: {metrics['volume']['tickets_processed']}")

    findings = audit(metrics)

    print("\n\nFindings")
    print("--------")
    if not findings:
        print("No segment differs by more than "
              f"{MATERIAL_GAP:.0%} on any measured rate.")
        print("Treat this with suspicion rather than satisfaction: a completely "
              "flat table often means the analysis was not sensitive enough.")
    else:
        for finding in findings:
            print(f"  - {finding}")
        print("\nEach gap above needs an explanation in the report. A difference "
              "that is found and explained is a stronger result than a table "
              "showing no variation at all.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
