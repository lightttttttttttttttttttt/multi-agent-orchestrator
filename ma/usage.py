from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path


# Rough USD / 1M tokens. Override via ~/.ma/rates.json
DEFAULT_RATES = {
    "default": {"in": 0.5, "out": 1.5},
    "sol": {"in": 5.0, "out": 15.0},
    "gpt-5": {"in": 5.0, "out": 15.0},
    "deepseek": {"in": 0.3, "out": 0.8},
    "glm": {"in": 0.4, "out": 1.0},
    "grok": {"in": 2.0, "out": 6.0},
    "gemini": {"in": 0.2, "out": 0.6},
}


def load_rates() -> dict:
    path = Path(os.environ.get("MA_RATES_FILE") or (Path.home() / ".ma" / "rates.json"))
    rates = dict(DEFAULT_RATES)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(v, dict) and "in" in v and "out" in v:
                        rates[str(k).lower()] = {"in": float(v["in"]), "out": float(v["out"])}
        except Exception:
            pass
    return rates


def _rate_for(model: str, rates: dict | None = None) -> dict:
    rates = rates or load_rates()
    m = (model or "").lower()
    # longest key match wins for specificity
    best = None
    for key, rate in rates.items():
        if key == "default":
            continue
        if key in m and (best is None or len(key) > len(best[0])):
            best = (key, rate)
    return best[1] if best else rates.get("default", DEFAULT_RATES["default"])


def extract_usage_tokens(raw: dict | None, prompt: str = "", content: str = "") -> tuple[int, int, str]:
    """Return (prompt_tokens, completion_tokens, source). Prefer provider usage fields."""
    prompt_tokens = max(1, len(prompt) // 4)
    completion_tokens = max(1, len(content) // 4)
    source = "estimate_chars/4"
    if not isinstance(raw, dict):
        return prompt_tokens, completion_tokens, source
    usage = raw.get("usage") or {}
    # OpenAI-style
    if usage.get("prompt_tokens") or usage.get("completion_tokens"):
        if usage.get("prompt_tokens"):
            prompt_tokens = int(usage["prompt_tokens"])
        if usage.get("completion_tokens"):
            completion_tokens = int(usage["completion_tokens"])
        return prompt_tokens, completion_tokens, "provider.usage"
    # alternate names
    if usage.get("input_tokens") or usage.get("output_tokens"):
        if usage.get("input_tokens"):
            prompt_tokens = int(usage["input_tokens"])
        if usage.get("output_tokens"):
            completion_tokens = int(usage["output_tokens"])
        return prompt_tokens, completion_tokens, "provider.usage_io"
    # nested details
    details = usage.get("prompt_tokens_details") or {}
    if usage.get("total_tokens") and not usage.get("prompt_tokens"):
        # weak signal only
        total = int(usage["total_tokens"])
        prompt_tokens = max(1, total // 2)
        completion_tokens = max(1, total - prompt_tokens)
        return prompt_tokens, completion_tokens, "provider.total_split"
    _ = details
    return prompt_tokens, completion_tokens, source


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
        self.rates = load_rates()

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
        raw = (meta or {}).get("raw") if meta else None
        prompt_tokens, completion_tokens, source = extract_usage_tokens(raw if isinstance(raw, dict) else None, prompt, content)
        rate = _rate_for(model, self.rates)
        cost = (prompt_tokens * rate["in"] + completion_tokens * rate["out"]) / 1_000_000
        meta_out = dict(meta or {})
        meta_out["token_source"] = source
        meta_out["rate"] = rate
        row = {
            "ts": int(time.time()),
            "project_id": project_id,
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cost_usd": round(cost, 8),
            "meta": json.dumps(meta_out),
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
            "rates_file": str(Path(os.environ.get("MA_RATES_FILE") or (Path.home() / ".ma" / "rates.json"))),
            "note": "cost is estimated unless provider usage tokens present",
        }
