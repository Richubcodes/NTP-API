"""Lightweight SQLite store for NTP poll history.

Stores the most recent N polls per host and exposes a small query API
used by the dashboard. SQLite is intentionally chosen - no external
infrastructure to stand up for a portfolio project, but the interface
is narrow enough to swap for Postgres later.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

from app.poller import NTPResult


SCHEMA = """
CREATE TABLE IF NOT EXISTS poll_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    host TEXT NOT NULL,
    reachable INTEGER NOT NULL,
    stratum INTEGER,
    offset_ms REAL,
    delay_ms REAL,
    server_time_utc TEXT,
    error TEXT,
    queried_at_utc TEXT NOT NULL,
    status TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_poll_results_host_time
    ON poll_results(host, queried_at_utc DESC);
"""


class PollStore:
    def __init__(self, db_path: str | Path = "ntp_monitor.db") -> None:
        self.db_path = str(db_path)
        self._init_schema()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def record_results(self, results: Iterable[NTPResult]) -> None:
        rows = [
            (
                r.host,
                1 if r.reachable else 0,
                r.stratum,
                r.offset_ms,
                r.delay_ms,
                r.server_time_utc,
                r.error,
                r.queried_at_utc,
                r.status,
            )
            for r in results
        ]
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO poll_results
                    (host, reachable, stratum, offset_ms, delay_ms,
                     server_time_utc, error, queried_at_utc, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def latest_per_host(self) -> list[dict]:
        """Most recent poll for each known host, ordered by host."""
        with self._connect() as conn:
            cur = conn.execute(
                """
                SELECT pr.*
                FROM poll_results pr
                INNER JOIN (
                    SELECT host, MAX(queried_at_utc) AS latest
                    FROM poll_results
                    GROUP BY host
                ) latest_pr
                ON pr.host = latest_pr.host
                   AND pr.queried_at_utc = latest_pr.latest
                ORDER BY pr.host
                """
            )
            return [dict(row) for row in cur.fetchall()]

    def history_for_host(self, host: str, limit: int = 100) -> list[dict]:
        with self._connect() as conn:
            cur = conn.execute(
                """
                SELECT * FROM poll_results
                WHERE host = ?
                ORDER BY queried_at_utc DESC
                LIMIT ?
                """,
                (host, limit),
            )
            return [dict(row) for row in cur.fetchall()]

    def summary(self) -> dict:
        """Counts by status across the most-recent poll per host."""
        latest = self.latest_per_host()
        counts = {"healthy": 0, "drifting": 0, "unsynchronised": 0, "unreachable": 0}
        for row in latest:
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        return {"total": len(latest), "by_status": counts}
