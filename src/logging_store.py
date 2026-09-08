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
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(db_path)
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

    def close(self):
        self.connection.close()
