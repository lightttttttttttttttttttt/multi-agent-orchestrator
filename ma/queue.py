from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path


class QueueError(RuntimeError):
    pass


class JobQueue:
    """Minimal durable job queue for remote/local workers."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or (Path.home() / ".ma" / "queue.sqlite"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs(
              id TEXT PRIMARY KEY,
              repo TEXT NOT NULL,
              goal TEXT NOT NULL,
              verify_command TEXT,
              status TEXT NOT NULL,
              worker_id TEXT,
              result TEXT,
              error TEXT,
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL
            );
            """
        )
        self.db.commit()

    def close(self):
        self.db.close()

    def enqueue(self, *, repo: str, goal: str, verify_command: str | None = None) -> str:
        job_id = uuid.uuid4().hex[:12]
        now = int(time.time())
        self.db.execute(
            "INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?)",
            (job_id, repo, goal, verify_command, "QUEUED", None, None, None, now, now),
        )
        self.db.commit()
        return job_id

    def claim(self, worker_id: str) -> dict | None:
        self.db.execute("BEGIN IMMEDIATE")
        row = self.db.execute(
            "SELECT * FROM jobs WHERE status='QUEUED' ORDER BY created_at LIMIT 1"
        ).fetchone()
        if not row:
            self.db.execute("COMMIT")
            return None
        now = int(time.time())
        self.db.execute(
            "UPDATE jobs SET status=?, worker_id=?, updated_at=? WHERE id=? AND status='QUEUED'",
            ("RUNNING", worker_id, now, row["id"]),
        )
        self.db.commit()
        return dict(self.db.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone())

    def complete(self, job_id: str, result: dict):
        self.db.execute(
            "UPDATE jobs SET status=?, result=?, updated_at=? WHERE id=?",
            ("DONE", json.dumps(result), int(time.time()), job_id),
        )
        self.db.commit()

    def fail(self, job_id: str, error: str):
        self.db.execute(
            "UPDATE jobs SET status=?, error=?, updated_at=? WHERE id=?",
            ("FAILED", error, int(time.time()), job_id),
        )
        self.db.commit()

    def get(self, job_id: str) -> dict:
        row = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise QueueError(f"job not found: {job_id}")
        item = dict(row)
        if item.get("result"):
            try:
                item["result"] = json.loads(item["result"])
            except json.JSONDecodeError:
                pass
        return item

    def list(self, limit: int = 50) -> list[dict]:
        rows = self.db.execute(
            "SELECT id,repo,goal,status,worker_id,created_at,updated_at FROM jobs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
