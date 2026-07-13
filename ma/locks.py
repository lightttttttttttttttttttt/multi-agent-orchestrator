from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path


class FileLockError(RuntimeError):
    pass


class FileLockManager:
    """Cross-process file locks backed by SQLite (+ in-process mutex)."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path or (Path.home() / ".ma" / "file_locks.sqlite"))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Lock()
        self._local: dict[str, str] = {}
        self.db = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS locks(
              path TEXT PRIMARY KEY,
              owner TEXT NOT NULL,
              project_id TEXT,
              created_at INTEGER NOT NULL
            )
            """
        )
        self.db.commit()

    def close(self):
        with self._thread:
            self.db.close()

    def acquire(self, task_id: str, files: list[str], *, project_id: str = "", wait_s: float = 0):
        deadline = time.time() + max(0.0, wait_s)
        while True:
            try:
                self._acquire_once(task_id, files, project_id=project_id)
                return
            except FileLockError:
                if time.time() >= deadline:
                    raise
                time.sleep(0.2)

    def _acquire_once(self, task_id: str, files: list[str], *, project_id: str):
        with self._thread:
            # process-local first
            conflicts = [f for f in files if f in self._local and self._local[f] != task_id]
            if conflicts:
                raise FileLockError(f"local lock conflict for {task_id}: {conflicts}")
            now = int(time.time())
            try:
                self.db.execute("BEGIN IMMEDIATE")
                for path in files:
                    row = self.db.execute("SELECT owner FROM locks WHERE path=?", (path,)).fetchone()
                    if row and row[0] != task_id:
                        self.db.execute("ROLLBACK")
                        raise FileLockError(f"cross-process lock conflict for {task_id}: {path} held by {row[0]}")
                for path in files:
                    self.db.execute(
                        "INSERT INTO locks(path, owner, project_id, created_at) VALUES(?,?,?,?) "
                        "ON CONFLICT(path) DO UPDATE SET owner=excluded.owner, project_id=excluded.project_id, created_at=excluded.created_at",
                        (path, task_id, project_id, now),
                    )
                    self._local[path] = task_id
                self.db.commit()
            except FileLockError:
                raise
            except Exception:
                self.db.execute("ROLLBACK")
                raise

    def release(self, task_id: str, files: list[str] | None = None):
        with self._thread:
            if files is None:
                files = [f for f, owner in self._local.items() if owner == task_id]
            self.db.execute("BEGIN IMMEDIATE")
            for path in files:
                self.db.execute("DELETE FROM locks WHERE path=? AND owner=?", (path, task_id))
                if self._local.get(path) == task_id:
                    del self._local[path]
            self.db.commit()


class BudgetExceeded(RuntimeError):
    pass


class Budget:
    """Tracks model calls and rough token usage for a project run."""

    def __init__(self, max_calls: int | None = None, max_tokens: int | None = None):
        self.max_calls = max_calls
        self.max_tokens = max_tokens
        self.calls = 0
        self.tokens = 0
        self._lock = threading.Lock()

    def charge(self, *, prompt: str = "", content: str = "", model: str = ""):
        # rough estimate: chars/4
        used = max(1, (len(prompt) + len(content)) // 4)
        with self._lock:
            if self.max_calls is not None and self.calls + 1 > self.max_calls:
                raise BudgetExceeded(f"max_calls exceeded ({self.max_calls})")
            if self.max_tokens is not None and self.tokens + used > self.max_tokens:
                raise BudgetExceeded(f"max_tokens exceeded ({self.max_tokens}); need +{used}")
            self.calls += 1
            self.tokens += used
            return {"model": model, "calls": self.calls, "tokens": self.tokens, "charged": used}

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "calls": self.calls,
                "tokens": self.tokens,
                "max_calls": self.max_calls,
                "max_tokens": self.max_tokens,
            }
