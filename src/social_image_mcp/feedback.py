from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path

from .intent import Intent
from .models import ImageCandidate


class FeedbackStore:
    """Small local preference store used as a reranking prior."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS feedback (query_key TEXT NOT NULL, platform TEXT NOT NULL, candidate_id TEXT NOT NULL, accepted INTEGER NOT NULL, created REAL NOT NULL)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_feedback_lookup ON feedback(query_key, platform, candidate_id)")

    @staticmethod
    def query_key(intent: Intent) -> str:
        tokens = " ".join(sorted(intent.tokens))
        return hashlib.sha256(tokens.encode("utf-8", "ignore")).hexdigest()

    def record(self, intent: Intent, candidate: ImageCandidate, accepted: bool) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute("INSERT INTO feedback(query_key, platform, candidate_id, accepted, created) VALUES (?, ?, ?, ?, ?)", (self.query_key(intent), candidate.platform.value, candidate.id, int(accepted), time.time()))

    def bias(self, intent: Intent, candidate: ImageCandidate) -> float:
        key = self.query_key(intent)
        with sqlite3.connect(self.path) as conn:
            rows = conn.execute("SELECT accepted FROM feedback WHERE query_key = ? AND platform = ? AND candidate_id = ? ORDER BY created DESC LIMIT 8", (key, candidate.platform.value, candidate.id)).fetchall()
        if not rows:
            return 0.0
        accepted = sum(row[0] for row in rows)
        rejected = len(rows) - accepted
        return min(0.8, accepted * 0.2) - min(0.8, rejected * 0.3)
