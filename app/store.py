"""SQLite store for pending triage results awaiting maintainer approval."""

import json
import sqlite3
import time


class PendingStore:
    def __init__(self, path="issuepilot.db"):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS pending (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo TEXT, issue_number INTEGER, payload TEXT,
                status TEXT DEFAULT 'pending', created_at REAL, decided_at REAL
            )""")
        self.conn.commit()

    def add(self, repo, issue_number, payload):
        cur = self.conn.execute(
            "INSERT INTO pending (repo, issue_number, payload, created_at) VALUES (?, ?, ?, ?)",
            (repo, issue_number, json.dumps(payload), time.time()))
        self.conn.commit()
        return cur.lastrowid

    def get(self, pid):
        row = self.conn.execute("SELECT * FROM pending WHERE id = ?", (pid,)).fetchone()
        return self._row(row) if row else None

    def list(self, status="pending"):
        rows = self.conn.execute("SELECT * FROM pending WHERE status = ? ORDER BY id DESC", (status,))
        return [self._row(r) for r in rows]

    def decide(self, pid, status, payload=None):
        if payload is not None:
            self.conn.execute("UPDATE pending SET payload = ? WHERE id = ?", (json.dumps(payload), pid))
        self.conn.execute("UPDATE pending SET status = ?, decided_at = ? WHERE id = ?",
                          (status, time.time(), pid))
        self.conn.commit()

    @staticmethod
    def _row(row):
        d = dict(row)
        d["payload"] = json.loads(d["payload"])
        return d
