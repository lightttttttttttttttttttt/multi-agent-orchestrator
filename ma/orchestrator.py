from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from .defaults import DEFAULT_FALLBACKS, DEFAULT_MODELS, SYSTEMS
from .gates import require_approve, require_command_success, require_model_content
from .locks import Budget, BudgetExceeded, FileLockManager
from .notify import notify_failure
from .ops import doctor, workers_for_wave
from .report import export_markdown_report
from .router import NineRouterClient, RouterError
from .secrets import SecretScanError, require_no_secrets
from .store import TaskStore
from .tasks import enforce_allowed_files, parse_task_dag, ready_waves
from .usage import UsageLedger
from .workspace import Workspace, extract_diff


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
        budget: Budget | None = None,
        max_replans: int = 1,
        usage: UsageLedger | None = None,
    ):
        self.store = store
        self.client = client
        self.models = DEFAULT_MODELS | (models or {})
        self.fallbacks = DEFAULT_FALLBACKS | (fallbacks or {})
        self.max_workers = max_workers
        self.budget = budget
        if budget is not None and getattr(client, "budget", None) is None:
            client.budget = budget
        self.max_replans = max_replans
        self.usage = usage or UsageLedger()
        self.file_locks = FileLockManager()
        self._replan_count = 0
        self._project_for_usage: str | None = None
        self._dead_models: set[str] = set()

    def _call_role(self, role: str, prompt: str, system: str, *, project_id: str | None = None):
        chain = self.fallbacks.get(role) or [self.models[role]]
        seen, models = set(), []
        for m in chain:
            if m in self._dead_models:
                continue
            if m not in seen:
                seen.add(m)
                models.append(m)
        if not models:
            raise RouterError(f"{role}: no live models left (blacklist={sorted(self._dead_models)})")
        errors = []
        for model in models:
            try:
                result = self.client.call(model, prompt, system=system)
                if self.usage is not None:
                    self.usage.record(
                        model=model,
                        prompt=system + "\n" + prompt,
                        content=result.content,
                        project_id=project_id or self._project_for_usage,
                        meta={"role": role, "raw": result.raw, "latency_ms": result.latency_ms},
                    )
                return result, model, errors
            except RouterError as exc:
                errors.append(str(exc))
                # blacklist model for rest of this orchestrator instance
                self._dead_models.add(model)
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
        result, model, fallback_errors = self._call_role(stage, prompt, SYSTEMS[stage], project_id=project_id)
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
        self.file_locks.acquire(task_id, allowed, project_id=project_id)
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
                if m in self._dead_models:
                    continue
                if m not in seen:
                    seen.add(m)
                    models.append(m)
            if not models:
                raise RouterError(f"task {task_id}: no live implement models left")
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
                        if self.usage is not None:
                            self.usage.record(
                                model=model,
                                prompt=SYSTEMS["implementation"] + "\n" + attempt_prompt,
                                content=result.content,
                                project_id=project_id,
                                meta={"role": "implementation", "task_id": task_id, "raw": result.raw},
                            )
                        patch = extract_diff(require_model_content(result.content))
                        require_no_secrets(patch)
                        workspace.apply_patch(patch)
                        diff = workspace.diff()
                        if not diff.strip():
                            raise RuntimeError("worker patch produced no repository diff")
                        require_no_secrets(diff)
                        if allowed and allowed != ["."]:
                            enforce_allowed_files(diff, allowed)
                        chosen = (model, result, diff, tag, last_errors)
                        break
                    except RouterError as exc:
                        last_errors.append(f"{model}/{tag}: {exc}")
                        self._dead_models.add(model)
                        workspace.reset_hard()
                        break  # don't tight-retry same dead model
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
        pending = list(tasks)
        wave_idx = 0
        while pending:
            waves = ready_waves(pending)
            wave = waves[0]
            self.store.add_evidence(project_id, "wave_start", {"wave": wave_idx, "tasks": [t["id"] for t in wave]})
            wave_results = []
            failures = []
            n_workers = workers_for_wave(len(wave), self.max_workers)
            self.store.add_evidence(project_id, "wave_workers", {"wave": wave_idx, "workers": n_workers, "wave_size": len(wave)})
            if len(wave) == 1 or n_workers <= 1:
                for t in wave:
                    try:
                        wave_results.append(self._implement_one_task(project, project_id, t))
                    except Exception as exc:
                        failures.append((t, exc))
            else:
                with ThreadPoolExecutor(max_workers=n_workers) as pool:
                    futures = {pool.submit(self._implement_one_task, project, project_id, t): t for t in wave}
                    for fut in as_completed(futures):
                        t = futures[fut]
                        try:
                            wave_results.append(fut.result())
                        except Exception as exc:
                            failures.append((t, exc))
            all_results.extend(wave_results)
            self.store.add_evidence(
                project_id,
                "wave_done",
                {"wave": wave_idx, "ok": [r["task_id"] for r in wave_results], "failed": [t["id"] for t, _ in failures]},
            )
            if failures:
                if self._replan_count >= self.max_replans:
                    raise RuntimeError(
                        f"task failures and replan budget exhausted: "
                        + "; ".join(f"{t['id']}: {e}" for t, e in failures)
                    )
                self._replan_count += 1
                fail_report = "\n".join(f"- {t['id']}: {e}" for t, e in failures)
                self.store.add_evidence(project_id, "replan_start", {"wave": wave_idx, "failures": fail_report})
                replan_prompt = (
                    f"PROJECT GOAL:\n{project['goal']}\n\n"
                    f"CURRENT PLAN:\n{self.store.get_artifact(project_id, 'judgment')}\n\n"
                    f"FAILED TASKS:\n{fail_report}\n\n"
                    "Replan ONLY the failed work as a JSON task array with id/goal/allowed_files/verify_command/depends_on. "
                    "Do not redo successful tasks. Keep allowed_files minimal."
                )
                result, model, errors = self._call_role("judgment", replan_prompt, SYSTEMS["judgment"])
                new_tasks = parse_task_dag(require_model_content(result.content))
                # keep successful tasks; replace remaining with replan + not-yet-run
                done_ids = {r["task_id"] for r in all_results}
                failed_ids = {t["id"] for t, _ in failures}
                leftovers = [t for t in pending if t["id"] not in done_ids and t["id"] not in failed_ids and t["id"] not in {x["id"] for x in wave}]
                # wave tasks that failed are dropped in favor of replan
                pending = leftovers + [
                    {
                        "id": t.id,
                        "goal": t.goal,
                        "allowed_files": t.allowed_files,
                        "verify_command": t.verify_command,
                        "depends_on": t.depends_on,
                        "status": "READY",
                    }
                    for t in new_tasks
                ]
                # persist updated remaining task set + successful statuses
                persisted = []
                for r in all_results:
                    # reconstruct minimal success entries from store
                    pass
                existing = self.store.list_tasks(project_id)
                kept = [t for t in existing if t["id"] in done_ids]
                for t in kept:
                    t["status"] = "IMPLEMENTED"
                self.store.replace_tasks(project_id, kept + pending)
                self.store.add_evidence(
                    project_id,
                    "replan",
                    {"model": model, "new_tasks": [t["id"] for t in new_tasks], "fallback_errors": errors},
                )
                wave_idx += 1
                continue
            # remove completed wave from pending
            done_wave = {t["id"] for t in wave}
            pending = [t for t in pending if t["id"] not in done_wave]
            wave_idx += 1

        # Integration worktree: merge all successful task branches
        integration = Workspace(project["repo"], project_id, task_id="integration")
        if integration.path.exists():
            integration.remove()
        integration.create(base="HEAD")
        merged_branches = []
        for r in all_results:
            branch = r.get("branch") or f"ma/{project_id}/{r['task_id']}"
            integration.merge_branch(branch)
            merged_branches.append(branch)
        integration.commit(f"ma: integrate {project_id}")
        combined_diff = integration.diff()
        if not combined_diff.strip():
            combined_diff = "\n".join(f"merged {b}" for b in merged_branches) + "\n"

        artifact = json.dumps(
            {
                "integration_worktree": str(integration.path),
                "integration_branch": integration.branch,
                "task_results": all_results,
                "merged_branches": merged_branches,
                "replans": self._replan_count,
                "budget": self.budget.snapshot() if self.budget else None,
            },
            ensure_ascii=False,
            indent=2,
        )
        self.store.record_stage(project_id, "implementation", "multi-task", "per-task workers", artifact)
        self.store.add_evidence(
            project_id,
            "implementation",
            {"tasks": len(all_results), "waves": wave_idx, "integration": str(integration.path), "replans": self._replan_count},
        )
        return {"tasks": all_results, "waves": wave_idx, "integration": str(integration.path), "replans": self._replan_count}

    def audit(self, project_id: str):
        if self.store.next_stage(project_id) != "audit":
            raise ValueError(f"audit not ready; next stage is {self.store.next_stage(project_id)}")
        self.run_stage(project_id, "audit")
        artifact = self.store.get_artifact(project_id, "audit")
        return require_approve(artifact)

    def merge(self, project_id: str, *, push: bool = False, remote: str = "origin") -> dict:
        project = self.store.get_project(project_id)
        audit = self.store.get_artifact(project_id, "audit")
        require_approve(audit)
        integration = Workspace(project["repo"], project_id, task_id="integration")
        if integration.path.exists():
            result = integration.merge_into_repo(f"ma: {project_id} approved")
            ws = integration
        else:
            workspace = Workspace(project["repo"], project_id)
            if not workspace.path.exists():
                raise RuntimeError("worktree missing; cannot merge")
            result = workspace.merge_into_repo(f"ma: {project_id} approved")
            ws = workspace
        if push:
            result["push"] = ws.push(remote=remote)
        self.store.add_evidence(project_id, "merge", result)
        return result

    def ship(
        self,
        project_id: str,
        *,
        verify_command: str | None = None,
        merge: bool = False,
        push: bool = False,
        remote: str = "origin",
        preflight: bool = True,
    ) -> dict:
        self._project_for_usage = project_id
        try:
            if preflight:
                pre = doctor(probe_models=False)
                core_fail = [c for c in pre["checks"] if c["check"] in {"9router_up", "9router_key", "git"} and not c["ok"]]
                self.store.add_evidence(project_id, "preflight", pre)
                if core_fail:
                    raise RuntimeError("preflight failed: " + "; ".join(f"{c['check']}:{c['detail']}" for c in core_fail))
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
            merge_info = (
                self.merge(project_id, push=push, remote=remote)
                if merge
                else {"merged": False, "reason": "merge not requested"}
            )
            project = self.store.get_project(project_id)
            usage = self.usage.summary(project_id) if self.usage else None
            report_path = export_markdown_report(self.store, project_id, usage)
            return {
                "project_id": project_id,
                "status": project["status"],
                "next_stage": self.store.next_stage(project_id),
                "tasks": self.store.list_tasks(project_id),
                "merge": merge_info,
                "budget": self.budget.snapshot() if self.budget else None,
                "replans": self._replan_count,
                "usage": usage,
                "dead_models": sorted(self._dead_models),
                "report": str(report_path),
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
