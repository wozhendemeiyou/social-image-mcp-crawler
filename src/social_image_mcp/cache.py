from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any


class SearchCache:
    def __init__(self, path: str, ttl_seconds: int = 900) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_seconds
        with sqlite3.connect(self.path) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS search_cache (key TEXT PRIMARY KEY, created REAL NOT NULL, payload TEXT NOT NULL)")

    @staticmethod
    def key(*parts: Any) -> str:
        encoded = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str).encode()
        return hashlib.sha256(encoded).hexdigest()

    def get(self, key: str) -> Any | None:
        with sqlite3.connect(self.path) as conn:
            row = conn.execute("SELECT created, payload FROM search_cache WHERE key = ?", (key,)).fetchone()
        if not row or time.time() - row[0] > self.ttl_seconds:
            return None
        return json.loads(row[1])

    def set(self, key: str, payload: Any) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute("INSERT OR REPLACE INTO search_cache(key, created, payload) VALUES (?, ?, ?)", (key, time.time(), json.dumps(payload, ensure_ascii=False)))

    def clear(self) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute("DELETE FROM search_cache")
