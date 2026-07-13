from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path

from .defaults import DEFAULT_FALLBACKS, DEFAULT_MODELS
from .router import NineRouterClient
from .store import TaskStore
from .workspace import Workspace


def _ok(name: str, detail: str = "") -> dict:
    return {"check": name, "ok": True, "detail": detail}


def _bad(name: str, detail: str) -> dict:
    return {"check": name, "ok": False, "detail": detail}


def doctor(*, probe_models: bool = True, timeout: int = 15) -> dict:
    checks = []
    try:
        with urllib.request.urlopen("http://127.0.0.1:20128/v1/models", timeout=5) as r:
            checks.append(_ok("9router_up", f"HTTP {r.status}"))
    except Exception as exc:
        checks.append(_bad("9router_up", f"{type(exc).__name__}: {exc}"))

    try:
        from .orchestrator import load_9router_key

        key = load_9router_key()
        checks.append(_ok("9router_key", f"loaded (...{key[-4:]})"))
    except Exception as exc:
        key = None
        checks.append(_bad("9router_key", str(exc)))

    git = shutil.which("git")
    checks.append(_ok("git", git) if git else _bad("git", "not found in PATH"))

    ma_home = Path.home() / ".ma"
    ma_home.mkdir(parents=True, exist_ok=True)
    checks.append(_ok("ma_home", str(ma_home)))
    for name in ("state.sqlite", "file_locks.sqlite", "usage.sqlite"):
        p = ma_home / name
        checks.append(_ok(f"path_{name}", f"exists={p.exists()} size={p.stat().st_size if p.exists() else 0}"))

    env_path = Path(os.path.expandvars(r"%LOCALAPPDATA%\hermes\.env"))
    has_tg = False
    if env_path.exists():
        text = env_path.read_text(encoding="utf-8", errors="replace")
        has_tg = "TELEGRAM_BOT_TOKEN" in text and (
            "TELEGRAM_HOME_CHANNEL" in text or "TELEGRAM_ALLOWED_USERS" in text
        )
    checks.append(_ok("telegram_env", "present") if has_tg else _bad("telegram_env", "missing token/home channel"))

    model_results = []
    if probe_models and key:
        client = NineRouterClient("http://127.0.0.1:20128", key, timeout=timeout, attempts=1)
        models = []
        for role, primary in DEFAULT_MODELS.items():
            if primary not in models:
                models.append(primary)
            for fb in DEFAULT_FALLBACKS.get(role, []):
                if fb not in models:
                    models.append(fb)
        for model in models[:8]:
            try:
                r = client.call(model, "Reply exactly PING")
                model_results.append(
                    {"model": model, "ok": True, "latency_ms": r.latency_ms, "content": r.content[:40]}
                )
            except Exception as exc:
                model_results.append({"model": model, "ok": False, "error": str(exc)[:200]})
        checks.append(
            _ok("model_probe", f"{sum(1 for m in model_results if m['ok'])}/{len(model_results)} alive")
            if any(m["ok"] for m in model_results)
            else _bad("model_probe", "all probed models failed")
        )

    core = {"9router_up", "9router_key", "git", "ma_home"}
    ok = all(c["ok"] for c in checks if c["check"] in core)
    return {"ok": ok, "checks": checks, "models": model_results}


def clean_project(store: TaskStore, project_id: str, *, delete_branches: bool = False) -> dict:
    project = store.get_project(project_id)
    repo = Path(project["repo"]).resolve()
    removed = []
    errors = []
    prefix = f"{repo.name}-ma-{project_id}"
    for path in sorted(repo.parent.glob(f"{prefix}*")):
        if not path.is_dir():
            continue
        try:
            suffix = path.name[len(repo.name) + 4 :]  # strip "<repo>-ma-"
            if suffix == project_id:
                ws = Workspace(repo, project_id)
            elif suffix.startswith(project_id + "-"):
                ws = Workspace(repo, project_id, task_id=suffix[len(project_id) + 1 :])
            else:
                ws = Workspace(repo, project_id, task_id=suffix)
            if ws.path.exists():
                ws.remove()
                removed.append(str(ws.path))
            if delete_branches:
                subprocess.run(
                    ["git", "branch", "-D", ws.branch],
                    cwd=repo,
                    capture_output=True,
                    text=True,
                    check=False,
                )
        except Exception as exc:
            errors.append(f"{path}: {exc}")
    subprocess.run(["git", "worktree", "prune"], cwd=repo, capture_output=True)
    return {
        "project_id": project_id,
        "removed_worktrees": removed,
        "errors": errors,
        "delete_branches": delete_branches,
    }


def workers_for_wave(wave_size: int, max_workers: int) -> int:
    if max_workers <= 0:
        max_workers = 4
    return max(1, min(max_workers, wave_size))
