"""
Smoke test: the harness CLI contract itself (A9's most common failure mode).
Confirms --input/--output are accepted as arguments, a file that doesn't
hardcode a path works, and a metrics report is produced without manual
intervention. Extend with real pipeline assertions as components land.
"""
import json
import subprocess
import sys
from pathlib import Path


def test_harness_runs_on_arbitrary_input(tmp_path):
    sample = [
        {
            "ticket_id": "TEST-0001",
            "channel": "email",
            "subject": "test",
            "body": "test ticket body",
            "received_at": "2026-01-01T00:00:00Z",
            "customer_id": "CUST-TEST",
            "customer_name": "Test Customer",
            "customer_tier": "standard",
            "customer_region": "north_america",
            "language_fluency": "fluent",
            "labels": {
                "intent": "authentication_failure",
                "urgency": "low",
                "expected_route": "auto_respond",
                "answerable_from_docs": True,
                "expected_doc_ids": ["DOC-AUTH-001"],
                "must_not_auto_respond": False,
            },
            "history": {
                "first_contact_resolution": True,
                "resolution_time_minutes": 5,
                "csat_rating": 5,
                "escalated": False,
                "repeat_contact": False,
            },
        }
    ]
    input_path = tmp_path / "arbitrary_name.json"
    input_path.write_text(json.dumps(sample))
    output_dir = tmp_path / "out"

    result = subprocess.run(
        [sys.executable, "-m", "evaluation.harness", "--input", str(input_path), "--output", str(output_dir)],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert (output_dir / "metrics_report.json").exists()
    assert (output_dir / "results.json").exists()

    metrics = json.loads((output_dir / "metrics_report.json").read_text())
    assert metrics["volume"]["tickets_processed"] == 1
