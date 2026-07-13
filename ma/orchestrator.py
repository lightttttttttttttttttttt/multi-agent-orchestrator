from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .gates import require_command_success, require_model_content
from .router import NineRouterClient
from .store import TaskStore

DEFAULT_MODELS = {
    "design": "Ntt_Codex10tr/gpt-5.6-sol",
    "critique": "nttcodex/grok-4.5-high",
    "judgment": "Ntt_Codex10tr/gpt-5.6-sol",
    "implementation": "nttcodex/deepseek-v4-pro",
    "audit": "Ntt_Codex10tr/gpt-5.6-sol",
    "report": "gemini/gemini-3-flash-preview",
}

SYSTEMS = {
    "design": "You are the senior architect. Produce a concrete design, risks, acceptance criteria, and exact verification commands. Do not claim to have executed tools.",
    "critique": "You are an adversarial senior reviewer. Find concrete flaws, missing constraints, security risks, and test gaps in the proposed design.",
    "judgment": "You are the final technical judge. Reconcile the design and critique into one approved implementation plan with bounded tasks.",
    "implementation": "You are an implementation engineer. Return an executable unified diff only, based strictly on the approved plan and repository context. Never claim tests ran.",
    "audit": "You are the final senior auditor. Review requirements, approved plan, diff, and machine evidence. Return APPROVE or REJECT first, then concrete findings.",
    "report": "Compile a concise evidence-grounded report. Do not invent actions, tests, files, or results.",
}


@dataclass
class RunResult:
    project_id: str
    status: str
    next_stage: str | None


def load_9router_key() -> str:
    db_path = Path(os.path.expandvars(r"%APPDATA%\9router\db\data.sqlite"))
    db = sqlite3.connect(db_path)
    try:
        row = db.execute("SELECT key FROM apiKeys WHERE isActive IN (1,'1') LIMIT 1").fetchone()
    finally:
        db.close()
    if not row:
        raise RuntimeError("no active 9Router ingress API key")
    return row[0]


class Orchestrator:
    def __init__(self, store: TaskStore, client: NineRouterClient, models: dict | None = None):
        self.store = store
        self.client = client
        self.models = DEFAULT_MODELS | (models or {})

    def run(self, project_id: str, *, until: str | None = None) -> RunResult:
        while (stage := self.store.next_stage(project_id)) is not None:
            if stage in {"implementation", "verification"}:
                # Mutation is deliberately approval-gated in the MVP.
                break
            self.run_stage(project_id, stage)
            if stage == until:
                break
        project = self.store.get_project(project_id)
        return RunResult(project_id, project["status"], self.store.next_stage(project_id))

    def run_stage(self, project_id: str, stage: str):
        project = self.store.get_project(project_id)
        context = self._context(project_id, stage)
        prompt = f"PROJECT GOAL:\n{project['goal']}\n\nREPOSITORY:\n{project['repo']}\n\nCONTEXT:\n{context}"
        model = self.models[stage]
        result = self.client.call(model, prompt, system=SYSTEMS[stage])
        artifact = require_model_content(result.content)
        self.store.record_stage(project_id, stage, model, prompt, artifact)
        self.store.add_evidence(project_id, "model_call", {"stage": stage, "model": model, "latency_ms": result.latency_ms, "content_chars": len(artifact)})

    def verify(self, project_id: str, command: str):
        project = self.store.get_project(project_id)
        proc = subprocess.run(command, cwd=project["repo"], shell=True, text=True, capture_output=True)
        evidence = {"command": command, "exit_code": proc.returncode, "stdout": proc.stdout[-20000:], "stderr": proc.stderr[-20000:]}
        self.store.add_evidence(project_id, "command", evidence)
        require_command_success(evidence)
        self.store.record_stage(project_id, "verification", "machine", command, json.dumps(evidence, ensure_ascii=False))
        return evidence

    def _context(self, project_id: str, stage: str) -> str:
        required = {
            "design": [],
            "critique": ["design"],
            "judgment": ["design", "critique"],
            "audit": ["judgment", "implementation", "verification"],
            "report": ["judgment", "implementation", "verification", "audit"],
        }.get(stage, ["judgment"])
        return "\n\n".join(f"## {name.upper()}\n{self.store.get_artifact(project_id, name)}" for name in required)
