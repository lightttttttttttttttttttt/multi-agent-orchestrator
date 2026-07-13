from __future__ import annotations

import json
import re
from dataclasses import dataclass


class TaskSpecError(ValueError):
    pass


@dataclass(frozen=True)
class TaskSpec:
    id: str
    goal: str
    allowed_files: list[str]
    verify_command: str
    depends_on: list[str]


def parse_task_dag(text: str) -> list[TaskSpec]:
    """Extract a JSON task array from model output. Supports fenced JSON."""
    fenced = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else None
    if not candidate:
        start = text.find("[")
        end = text.rfind("]")
        if start < 0 or end <= start:
            raise TaskSpecError("no task JSON array found")
        candidate = text[start : end + 1]
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise TaskSpecError(f"invalid task JSON: {exc}") from exc
    if not isinstance(data, list) or not data:
        raise TaskSpecError("task DAG must be a non-empty array")
    tasks = []
    seen = set()
    for item in data:
        if not isinstance(item, dict):
            raise TaskSpecError("each task must be an object")
        tid = str(item.get("id", "")).strip()
        goal = str(item.get("goal", "")).strip()
        allowed = item.get("allowed_files") or []
        verify = str(item.get("verify_command", "")).strip()
        depends = item.get("depends_on") or []
        if not tid or not goal or not verify or not isinstance(allowed, list) or not allowed:
            raise TaskSpecError(f"task missing required fields: {item}")
        if tid in seen:
            raise TaskSpecError(f"duplicate task id: {tid}")
        seen.add(tid)
        tasks.append(TaskSpec(tid, goal, [str(x) for x in allowed], verify, [str(x) for x in depends]))
    ids = {t.id for t in tasks}
    for t in tasks:
        for dep in t.depends_on:
            if dep not in ids:
                raise TaskSpecError(f"task {t.id} depends on missing {dep}")
    visiting, done = set(), set()

    def visit(tid: str):
        if tid in done:
            return
        if tid in visiting:
            raise TaskSpecError(f"cycle detected at {tid}")
        visiting.add(tid)
        task = next(x for x in tasks if x.id == tid)
        for dep in task.depends_on:
            visit(dep)
        visiting.remove(tid)
        done.add(tid)

    for t in tasks:
        visit(t.id)
    return tasks


def changed_files_from_diff(diff: str) -> list[str]:
    files = []
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                path = parts[3][2:] if parts[3].startswith("b/") else parts[3]
                files.append(path)
    return files


def enforce_allowed_files(diff: str, allowed: list[str]) -> list[str]:
    changed = changed_files_from_diff(diff)
    if not changed:
        raise TaskSpecError("diff touches no files")
    allowed_set = set(allowed)
    illegal = [f for f in changed if f not in allowed_set]
    if illegal:
        raise TaskSpecError(f"patch touches files outside allowed set: {illegal}")
    return changed


def files_overlap(a: list[str], b: list[str]) -> bool:
    return bool(set(a) & set(b))


def ready_waves(tasks: list[dict]) -> list[list[dict]]:
    """
    Partition tasks into execution waves.
    A task is ready when all depends_on are done in previous waves.
    Within a wave, tasks must not share allowed_files (file lock).
    """
    remaining = {t["id"]: dict(t) for t in tasks}
    done: set[str] = set()
    waves: list[list[dict]] = []
    while remaining:
        candidates = [
            t
            for t in remaining.values()
            if all(dep in done for dep in t.get("depends_on", []))
        ]
        if not candidates:
            raise TaskSpecError(f"deadlock in task DAG; remaining={list(remaining)}")
        # stable order by id
        candidates.sort(key=lambda x: x["id"])
        wave: list[dict] = []
        locked: set[str] = set()
        deferred = []
        for t in candidates:
            files = set(t.get("allowed_files") or [])
            if files & locked:
                deferred.append(t)
                continue
            wave.append(t)
            locked |= files
        if not wave:
            # all candidates conflict; run first alone
            wave = [candidates[0]]
        for t in wave:
            remaining.pop(t["id"])
            done.add(t["id"])
        waves.append(wave)
    return waves
