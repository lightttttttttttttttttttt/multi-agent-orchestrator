from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

STAGES = ["design", "critique", "judgment", "implementation", "verification", "audit", "report"]


class TaskStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS projects(
          id TEXT PRIMARY KEY, name TEXT NOT NULL, repo TEXT NOT NULL, goal TEXT NOT NULL,
          status TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS stages(
          id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, stage TEXT NOT NULL,
          model TEXT NOT NULL, prompt TEXT NOT NULL, artifact TEXT NOT NULL, created_at INTEGER NOT NULL,
          UNIQUE(project_id, stage));
        CREATE TABLE IF NOT EXISTS evidence(
          id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, kind TEXT NOT NULL,
          data TEXT NOT NULL, created_at INTEGER NOT NULL);
        """)
        self.db.commit()

    def close(self): self.db.close()

    def create_project(self, name: str, repo: str, goal: str) -> str:
        project_id = uuid.uuid4().hex[:12]
        now = int(time.time())
        self.db.execute("INSERT INTO projects VALUES(?,?,?,?,?,?,?)", (project_id, name, repo, goal, "INTAKE", now, now))
        self.db.commit()
        return project_id

    def get_project(self, project_id: str) -> dict:
        row = self.db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if not row: raise KeyError(project_id)
        return dict(row)

    def next_stage(self, project_id: str) -> str | None:
        completed = {r[0] for r in self.db.execute("SELECT stage FROM stages WHERE project_id=?", (project_id,))}
        return next((stage for stage in STAGES if stage not in completed), None)

    def record_stage(self, project_id: str, stage: str, model: str, prompt: str, artifact: str):
        expected = self.next_stage(project_id)
        if stage != expected:
            raise ValueError(f"invalid transition: expected {expected}, got {stage}")
        now = int(time.time())
        self.db.execute("INSERT INTO stages(project_id,stage,model,prompt,artifact,created_at) VALUES(?,?,?,?,?,?)", (project_id, stage, model, prompt, artifact, now))
        status = "DONE" if stage == STAGES[-1] else stage.upper()
        self.db.execute("UPDATE projects SET status=?,updated_at=? WHERE id=?", (status, now, project_id))
        self.db.commit()

    def get_artifact(self, project_id: str, stage: str) -> str:
        row = self.db.execute("SELECT artifact FROM stages WHERE project_id=? AND stage=?", (project_id, stage)).fetchone()
        return row[0] if row else ""

    def list_projects(self) -> list[dict]:
        return [dict(row) for row in self.db.execute("SELECT * FROM projects ORDER BY updated_at DESC")]

    def list_stages(self, project_id: str) -> list[dict]:
        return [dict(row) for row in self.db.execute("SELECT stage,model,artifact,created_at FROM stages WHERE project_id=? ORDER BY id", (project_id,))]

    def add_evidence(self, project_id: str, kind: str, data: dict):
        self.db.execute("INSERT INTO evidence(project_id,kind,data,created_at) VALUES(?,?,?,?)", (project_id, kind, json.dumps(data), int(time.time())))
        self.db.commit()
