"""
Persistent decision log (A8). Every automated decision the system makes --
classification, routing, generation, guardrail check -- is written here so
the log can be reconciled against tickets processed.

Minimum record schema per the Setup Guide / Governance Framework:
    decision_id, created_at, ticket_id, stage, prediction, confidence,
    threshold, action_taken, reason, sources_used, guardrails,
    prompt_version, requirement_ids
"""
import sqlite3
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    decision_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    ticket_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    prediction TEXT,
    confidence REAL,
    threshold REAL,
    action_taken TEXT NOT NULL,
    reason TEXT NOT NULL,
    sources_used TEXT,
    guardrails TEXT,
    prompt_version TEXT,
    requirement_ids TEXT
)
"""


class DecisionLog:
    def __init__(self, db_path: str = "./storage/decisions.db"):
        """
        Open the decision log, falling back to a writable location.

        SQLite needs to create lock files alongside the database, which some
        filesystems do not allow -- network shares, certain mounted volumes,
        and read-only checkouts all produce "disk I/O error" here rather
        than a clear permission message. Found on a Windows folder mounted
        into a Linux sandbox, where the harness could not start at all.

        Since the decision log is required for A8 and the run must not be
        stoppable by where it happens to be checked out, a failure to open
        the configured path falls back to the system temporary directory
        and says so, rather than ending the run.
        """
        self.db_path = Path(db_path)
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(str(self.db_path))
            self.connection.execute(SCHEMA)
            self.connection.commit()
        except (sqlite3.Error, OSError) as exc:
            fallback = Path(tempfile.gettempdir()) / "cloudserve_decisions.db"
            print(
                f"WARNING: could not open the decision log at {self.db_path} "
                f"({exc}). Falling back to {fallback}. The run continues and "
                f"decisions are still recorded.",
                file=sys.stderr,
            )
            self.db_path = fallback
            try:
                fallback.unlink()
            except OSError:
                pass
            self.connection = sqlite3.connect(str(fallback))
            self.connection.execute(SCHEMA)
            self.connection.commit()

    def log(self, *, ticket_id: str, stage: str, action_taken: str, reason: str,
            prediction: str | None = None, confidence: float | None = None,
            threshold: float | None = None, sources_used: str | None = None,
            guardrails: str | None = None, prompt_version: str | None = None,
            requirement_ids: str | None = None) -> str:
        decision_id = str(uuid.uuid4())
        self.connection.execute(
            """INSERT INTO decisions
               (decision_id, created_at, ticket_id, stage, prediction, confidence,
                threshold, action_taken, reason, sources_used, guardrails,
                prompt_version, requirement_ids)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (decision_id, datetime.now(timezone.utc).isoformat(), ticket_id, stage,
             prediction, confidence, threshold, action_taken, reason, sources_used,
             guardrails, prompt_version, requirement_ids),
        )
        self.connection.commit()
        return decision_id

    def count_for_ticket(self, ticket_id: str) -> int:
        cur = self.connection.execute(
            "SELECT COUNT(*) FROM decisions WHERE ticket_id = ?", (ticket_id,)
        )
        return cur.fetchone()[0]

    def count_all(self) -> int:
        """
        Total decisions recorded.

        Used by the harness to reconcile logged decisions against tickets
        processed (A8). A gap between the two is visible immediately.
        """
        cur = self.connection.execute("SELECT COUNT(*) FROM decisions")
        return cur.fetchone()[0]

    def close(self):
        self.connection.close()
