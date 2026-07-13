from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from .gates import require_approve, require_command_success, require_model_content
from .locks import FileLockManager
from .notify import notify_failure
from .router import NineRouterClient, RouterError
from .store import TaskStore
from .tasks import enforce_allowed_files, parse_task_dag, ready_waves
from .workspace import Workspace, extract_diff

DEFAULT_MODELS = {
    "design": "Ntt_Codex10tr/gpt-5.6-sol",
    "critique": "nttcodex/grok-4.5-high",
    "judgment": "Ntt_Codex10tr/gpt-5.6-sol",
    "implementation": "nttcodex/deepseek-v4-pro",
    "audit": "Ntt_Codex10tr/gpt-5.6-sol",
    "report": "gemini/gemini-3-flash-preview",
}

DEFAULT_FALLBACKS = {
    "design": ["Ntt_Codex10tr/gpt-5.6-sol", "nttcodex/gpt-5.6-sol"],
    "critique": ["nttcodex/grok-4.5-high", "Ntt_Codex10tr/gpt-5.6-sol"],
    "judgment": ["Ntt_Codex10tr/gpt-5.6-sol", "nttcodex/gpt-5.6-sol"],
    "implementation": ["nttcodex/deepseek-v4-pro", "nttcodex/glm-5.2"],
    "audit": ["Ntt_Codex10tr/gpt-5.6-sol", "nttcodex/gpt-5.6-sol"],
    "report": ["gemini/gemini-3-flash-preview", "gemini/gemini-2.5-flash", "Ntt_Codex10tr/gpt-5.6-sol"],
}

SYSTEMS = {
    "design": "You are the senior architect. Produce a concrete design, risks, acceptance criteria, and exact verification commands. Do not claim to have executed tools.",
    "critique": "You are an adversarial senior reviewer. Find concrete flaws, missing constraints, security risks, and test gaps in the proposed design.",
    "judgment": (
        "You are the final technical judge. Reconcile the design and critique into one approved implementation plan. "
        "End with a JSON array of tasks. Each task object MUST have: id, goal, allowed_files (array of paths), "
        "verify_command, depends_on (array of task ids). Prefer independent tasks when files do not overlap."
    ),
    "implementation": "You are an implementation engineer. Return an executable unified diff only, based strictly on the task goal and repository context. Never claim tests ran.",
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
    def __init__(
        self,
        store: TaskStore,
        client: NineRouterClient,
        models: dict | None = None,
        fallbacks: dict | None = None,
        max_workers: int = 2,
    ):
        self.store = store
        self.client = client
        self.models = DEFAULT_MODELS | (models or {})
        self.fallbacks = DEFAULT_FALLBACKS | (fallbacks or {})
        self.max_workers = max_workers
        self.file_locks = FileLockManager()

    def _call_role(self, role: str, prompt: str, system: str):
        chain = self.fallbacks.get(role) or [self.models[role]]
        seen, models = set(), []
        for m in chain:
            if m not in seen:
                seen.add(m)
                models.append(m)
        errors = []
        for model in models:
            try:
                result = self.client.call(model, prompt, system=system)
                return result, model, errors
            except RouterError as exc:
                errors.append(str(exc))
        raise RouterError(f"{role}: all models failed: " + " | ".join(errors))

    def run(self, project_id: str, *, until: str | None = None) -> RunResult:
        while (stage := self.store.next_stage(project_id)) is not None:
            if stage in {"implementation", "verification"}:
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
        result, model, fallback_errors = self._call_role(stage, prompt, SYSTEMS[stage])
        artifact = require_model_content(result.content)
        self.store.record_stage(project_id, stage, model, prompt, artifact)
        self.store.add_evidence(
            project_id,
            "model_call",
            {
                "stage": stage,
                "model": model,
                "latency_ms": result.latency_ms,
                "content_chars": len(artifact),
                "fallback_errors": fallback_errors,
            },
        )
        if stage == "judgment":
            try:
                tasks = parse_task_dag(artifact)
                self.store.replace_tasks(
                    project_id,
                    [
                        {
                            "id": t.id,
                            "goal": t.goal,
                            "allowed_files": t.allowed_files,
                            "verify_command": t.verify_command,
                            "depends_on": t.depends_on,
                            "status": "READY",
                        }
                        for t in tasks
                    ],
                )
                self.store.add_evidence(project_id, "task_dag", {"count": len(tasks), "ids": [t.id for t in tasks]})
            except Exception as exc:
                self.store.add_evidence(project_id, "task_dag_parse_error", {"error": str(exc)})

    def verify(self, project_id: str, command: str):
        project = self.store.get_project(project_id)
        # Prefer integration worktree if present, else first task worktree, else repo
        integration = Workspace(project["repo"], project_id, task_id="integration")
        cwd = project["repo"]
        if integration.path.exists():
            cwd = integration.path
        else:
            tasks = self.store.list_tasks(project_id)
            for t in tasks:
                ws = Workspace(project["repo"], project_id, task_id=t["id"])
                if ws.path.exists():
                    cwd = ws.path
                    break
            else:
                legacy = Workspace(project["repo"], project_id)
                if legacy.path.exists():
                    cwd = legacy.path
        proc = subprocess.run(command, cwd=cwd, shell=True, text=True, capture_output=True)
        evidence = {
            "command": command,
            "cwd": str(cwd),
            "exit_code": proc.returncode,
            "stdout": proc.stdout[-20000:],
            "stderr": proc.stderr[-20000:],
        }
        self.store.add_evidence(project_id, "command", evidence)
        require_command_success(evidence)
        self.store.record_stage(
            project_id,
            "verification",
            "machine",
            command,
            json.dumps(evidence, ensure_ascii=False),
        )
        return evidence

    def _implement_one_task(self, project: dict, project_id: str, task: dict, base: str = "HEAD") -> dict:
        task_id = task["id"]
        allowed = task["allowed_files"]
        self.file_locks.acquire(task_id, allowed)
        try:
            self.store.set_task_status(task_id, "IMPLEMENTING")
            workspace = Workspace(project["repo"], project_id, task_id=task_id)
            workspace.create(base=base)
            snapshot = workspace.snapshot()
            allowed_clause = f"ALLOWED FILES ONLY: {allowed}\n"
            prompt = (
                f"PROJECT GOAL:\n{project['goal']}\n\nTASK ID: {task_id}\nTASK GOAL:\n{task['goal']}\n\n"
                f"APPROVED PLAN:\n{self.store.get_artifact(project_id, 'judgment')}\n\n"
                f"REPOSITORY SNAPSHOT:\n{snapshot}\n\n{allowed_clause}"
                "Return only a unified git diff. Keep changes minimal."
            )
            chain = self.fallbacks.get("implementation") or [self.models["implementation"]]
            seen, models = set(), []
            for m in chain:
                if m not in seen:
                    seen.add(m)
                    models.append(m)
            last_errors: list[str] = []
            chosen = None
            for model in models:
                for attempt_prompt, tag in (
                    (prompt, "full"),
                    (
                        (
                            f"Return ONLY a unified git diff. Output must start with 'diff --git'.\n"
                            f"Task: {task['goal']}\n{allowed_clause}"
                            f"Repo snapshot:\n{snapshot}\n"
                        ),
                        "tight",
                    ),
                ):
                    try:
                        result = self.client.call(model, attempt_prompt, system=SYSTEMS["implementation"])
                        patch = extract_diff(require_model_content(result.content))
                        workspace.apply_patch(patch)
                        diff = workspace.diff()
                        if not diff.strip():
                            raise RuntimeError("worker patch produced no repository diff")
                        if allowed and allowed != ["."]:
                            enforce_allowed_files(diff, allowed)
                        chosen = (model, result, diff, tag, last_errors)
                        break
                    except Exception as exc:
                        last_errors.append(f"{model}/{tag}: {exc}")
                        workspace.reset_hard()
                if chosen:
                    break
            if not chosen:
                self.store.set_task_status(task_id, "FAILED")
                raise RouterError(f"task {task_id}: all models failed: " + " | ".join(last_errors))
            model, result, diff, tag, fallback_errors = chosen
            workspace.commit(f"ma: {task_id}")
            # per-task verify if present
            verify_cmd = task.get("verify_command")
            verify_evidence = None
            if verify_cmd:
                proc = subprocess.run(verify_cmd, cwd=workspace.path, shell=True, text=True, capture_output=True)
                verify_evidence = {
                    "command": verify_cmd,
                    "exit_code": proc.returncode,
                    "stdout": proc.stdout[-10000:],
                    "stderr": proc.stderr[-10000:],
                }
                if proc.returncode != 0:
                    self.store.set_task_status(task_id, "VERIFY_FAILED")
                    raise RuntimeError(f"task {task_id} verify failed: {verify_evidence}")
            self.store.set_task_status(task_id, "IMPLEMENTED")
            evidence = {
                "task_id": task_id,
                "model": model,
                "latency_ms": result.latency_ms,
                "worktree": str(workspace.path),
                "branch": workspace.branch,
                "diff_chars": len(diff),
                "allowed_files": allowed,
                "prompt_mode": tag,
                "fallback_errors": fallback_errors,
                "verify": verify_evidence,
            }
            self.store.add_evidence(project_id, "task_implementation", evidence)
            return evidence
        finally:
            self.file_locks.release(task_id, allowed)

    def implement(self, project_id: str, *, allowed_files: list[str] | None = None) -> dict:
        if self.store.next_stage(project_id) != "implementation":
            raise ValueError(f"implementation not ready; next stage is {self.store.next_stage(project_id)}")
        project = self.store.get_project(project_id)
        tasks = self.store.list_tasks(project_id)
        if not tasks:
            # fallback single synthetic task over whole repo goal
            tasks = [
                {
                    "id": "T1",
                    "goal": project["goal"],
                    "allowed_files": allowed_files or ["."],
                    "verify_command": "python -m unittest discover -s . -v",
                    "depends_on": [],
                    "status": "READY",
                }
            ]
            # if allowed is ["."] skip enforce later by expanding? better require real files
            if tasks[0]["allowed_files"] == ["."]:
                # keep enforce soft: allowed_files None path in enforce by listing tracked? force user/tasks
                pass
            self.store.replace_tasks(project_id, tasks)

        waves = ready_waves(tasks)
        all_results: list[dict] = []
        for wave_idx, wave in enumerate(waves):
            self.store.add_evidence(project_id, "wave_start", {"wave": wave_idx, "tasks": [t["id"] for t in wave]})
            if len(wave) == 1 or self.max_workers <= 1:
                for t in wave:
                    all_results.append(self._implement_one_task(project, project_id, t))
            else:
                with ThreadPoolExecutor(max_workers=min(self.max_workers, len(wave))) as pool:
                    futures = {pool.submit(self._implement_one_task, project, project_id, t): t["id"] for t in wave}
                    for fut in as_completed(futures):
                        tid = futures[fut]
                        try:
                            all_results.append(fut.result())
                        except Exception as exc:
                            raise RuntimeError(f"parallel task {tid} failed: {exc}") from exc
            self.store.add_evidence(project_id, "wave_done", {"wave": wave_idx, "results": [r["task_id"] for r in all_results if r.get("task_id") in {t['id'] for t in wave}]})

        # Integration worktree: merge all task branches in dependency order
        integration = Workspace(project["repo"], project_id, task_id="integration")
        if integration.path.exists():
            integration.remove()
        integration.create(base="HEAD")
        merged_branches = []
        for wave in waves:
            for t in wave:
                branch = f"ma/{project_id}/{t['id']}"
                integration.merge_branch(branch)
                merged_branches.append(branch)
        integration.commit(f"ma: integrate {project_id}")
        combined_diff = integration.diff()
        # also include committed history as empty working tree; use merge-base style summary
        if not combined_diff.strip():
            # after commit, working tree clean — store branch list as artifact
            combined_diff = "\n".join(f"merged {b}" for b in merged_branches) + "\n"

        artifact = json.dumps(
            {
                "integration_worktree": str(integration.path),
                "integration_branch": integration.branch,
                "task_results": all_results,
                "merged_branches": merged_branches,
            },
            ensure_ascii=False,
            indent=2,
        )
        self.store.record_stage(project_id, "implementation", "multi-task", "per-task workers", artifact)
        self.store.add_evidence(project_id, "implementation", {"tasks": len(all_results), "waves": len(waves), "integration": str(integration.path)})
        return {"tasks": all_results, "waves": len(waves), "integration": str(integration.path)}

    def audit(self, project_id: str):
        if self.store.next_stage(project_id) != "audit":
            raise ValueError(f"audit not ready; next stage is {self.store.next_stage(project_id)}")
        self.run_stage(project_id, "audit")
        artifact = self.store.get_artifact(project_id, "audit")
        return require_approve(artifact)

    def merge(self, project_id: str) -> dict:
        project = self.store.get_project(project_id)
        audit = self.store.get_artifact(project_id, "audit")
        require_approve(audit)
        integration = Workspace(project["repo"], project_id, task_id="integration")
        if integration.path.exists():
            result = integration.merge_into_repo(f"ma: {project_id} approved")
        else:
            workspace = Workspace(project["repo"], project_id)
            if not workspace.path.exists():
                raise RuntimeError("worktree missing; cannot merge")
            result = workspace.merge_into_repo(f"ma: {project_id} approved")
        self.store.add_evidence(project_id, "merge", result)
        return result

    def ship(
        self,
        project_id: str,
        *,
        verify_command: str | None = None,
        merge: bool = False,
    ) -> dict:
        try:
            while True:
                stage = self.store.next_stage(project_id)
                if stage is None:
                    break
                if stage in {"design", "critique", "judgment", "report"}:
                    self.run_stage(project_id, stage)
                    continue
                if stage == "implementation":
                    self.implement(project_id)
                    continue
                if stage == "verification":
                    cmd = verify_command
                    if not cmd:
                        tasks = self.store.list_tasks(project_id)
                        cmd = tasks[0]["verify_command"] if tasks else "python -m unittest discover -s . -v"
                    self.verify(project_id, cmd)
                    continue
                if stage == "audit":
                    self.audit(project_id)
                    continue
                break
            merge_info = self.merge(project_id) if merge else {"merged": False, "reason": "merge not requested"}
            project = self.store.get_project(project_id)
            return {
                "project_id": project_id,
                "status": project["status"],
                "next_stage": self.store.next_stage(project_id),
                "tasks": self.store.list_tasks(project_id),
                "merge": merge_info,
            }
        except Exception as exc:
            note = notify_failure(f"ma ship FAILED {project_id}: {type(exc).__name__}: {exc}")
            self.store.add_evidence(project_id, "failure", {"error": str(exc), "notify": note})
            raise

    def _context(self, project_id: str, stage: str) -> str:
        required = {
            "design": [],
            "critique": ["design"],
            "judgment": ["design", "critique"],
            "audit": ["judgment", "implementation", "verification"],
            "report": ["judgment", "implementation", "verification", "audit"],
        }.get(stage, ["judgment"])
        return "\n\n".join(f"## {name.upper()}\n{self.store.get_artifact(project_id, name)}" for name in required)
