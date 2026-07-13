from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path


# Rough USD / 1M tokens. Estimates only — not provider invoices.
DEFAULT_RATES = {
    "default": {"in": 0.5, "out": 1.5},
    "sol": {"in": 5.0, "out": 15.0},
    "gpt-5": {"in": 5.0, "out": 15.0},
    "deepseek": {"in": 0.3, "out": 0.8},
    "glm": {"in": 0.4, "out": 1.0},
    "grok": {"in": 2.0, "out": 6.0},
    "gemini": {"in": 0.2, "out": 0.6},
}


def _rate_for(model: str) -> dict:
    m = (model or "").lower()
    for key, rate in DEFAULT_RATES.items():
        if key != "default" and key in m:
            return rate
    return DEFAULT_RATES["default"]


class UsageLedger:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or (Path.home() / ".ma" / "usage.sqlite"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS usage(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts INTEGER NOT NULL,
              project_id TEXT,
              model TEXT NOT NULL,
              prompt_tokens INTEGER NOT NULL,
              completion_tokens INTEGER NOT NULL,
              cost_usd REAL NOT NULL,
              meta TEXT
            )
            """
        )
        self.db.commit()

    def close(self):
        self.db.close()

    def record(
        self,
        *,
        model: str,
        prompt: str = "",
        content: str = "",
        project_id: str | None = None,
        meta: dict | None = None,
    ) -> dict:
        # Prefer explicit usage if present in meta.raw
        prompt_tokens = max(1, len(prompt) // 4)
        completion_tokens = max(1, len(content) // 4)
        raw = (meta or {}).get("raw") if meta else None
        if isinstance(raw, dict):
            usage = raw.get("usage") or {}
            if usage.get("prompt_tokens"):
                prompt_tokens = int(usage["prompt_tokens"])
            if usage.get("completion_tokens"):
                completion_tokens = int(usage["completion_tokens"])
        rate = _rate_for(model)
        cost = (prompt_tokens * rate["in"] + completion_tokens * rate["out"]) / 1_000_000
        row = {
            "ts": int(time.time()),
            "project_id": project_id,
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cost_usd": round(cost, 8),
            "meta": json.dumps(meta or {}),
        }
        self.db.execute(
            "INSERT INTO usage(ts,project_id,model,prompt_tokens,completion_tokens,cost_usd,meta) VALUES(?,?,?,?,?,?,?)",
            (
                row["ts"],
                row["project_id"],
                row["model"],
                row["prompt_tokens"],
                row["completion_tokens"],
                row["cost_usd"],
                row["meta"],
            ),
        )
        self.db.commit()
        return row

    def summary(self, project_id: str | None = None) -> dict:
        if project_id:
            rows = self.db.execute(
                "SELECT model, SUM(prompt_tokens), SUM(completion_tokens), SUM(cost_usd), COUNT(*) "
                "FROM usage WHERE project_id=? GROUP BY model",
                (project_id,),
            ).fetchall()
            total = self.db.execute(
                "SELECT SUM(cost_usd), SUM(prompt_tokens), SUM(completion_tokens), COUNT(*) FROM usage WHERE project_id=?",
                (project_id,),
            ).fetchone()
        else:
            rows = self.db.execute(
                "SELECT model, SUM(prompt_tokens), SUM(completion_tokens), SUM(cost_usd), COUNT(*) FROM usage GROUP BY model"
            ).fetchall()
            total = self.db.execute(
                "SELECT SUM(cost_usd), SUM(prompt_tokens), SUM(completion_tokens), COUNT(*) FROM usage"
            ).fetchone()
        return {
            "by_model": [
                {
                    "model": r[0],
                    "prompt_tokens": r[1] or 0,
                    "completion_tokens": r[2] or 0,
                    "cost_usd": round(r[3] or 0, 6),
                    "calls": r[4] or 0,
                }
                for r in rows
            ],
            "total_cost_usd": round((total[0] or 0), 6),
            "total_prompt_tokens": total[1] or 0,
            "total_completion_tokens": total[2] or 0,
            "total_calls": total[3] or 0,
            "project_id": project_id,
        }
