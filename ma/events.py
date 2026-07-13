from __future__ import annotations

import json
import os
import signal
import time
from pathlib import Path


class CancelledError(RuntimeError):
    pass


def _root(base: Path | None = None) -> Path:
    root = base or (Path.home() / ".ma" / "events")
    root.mkdir(parents=True, exist_ok=True)
    return root


def events_path(project_id: str, base: Path | None = None) -> Path:
    return _root(base) / f"{project_id}.jsonl"


def cancel_path(project_id: str, base: Path | None = None) -> Path:
    return _root(base) / f"{project_id}.cancel"


def pid_path(project_id: str, base: Path | None = None) -> Path:
    return _root(base) / f"{project_id}.pid"


def emit(project_id: str, event: str, **data) -> dict:
    payload = {
        "ts": int(time.time()),
        "project_id": project_id,
        "event": event,
        **data,
    }
    path = events_path(project_id)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return payload


def write_pid(project_id: str, pid: int | None = None) -> Path:
    path = pid_path(project_id)
    path.write_text(str(pid or os.getpid()), encoding="utf-8")
    return path


def clear_pid(project_id: str):
    path = pid_path(project_id)
    if path.exists():
        path.unlink()


def read_pid(project_id: str) -> int | None:
    path = pid_path(project_id)
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def request_cancel(project_id: str, *, hard: bool = True) -> dict:
    path = cancel_path(project_id)
    path.write_text(str(int(time.time())), encoding="utf-8")
    emit(project_id, "cancel_requested", hard=hard)
    killed = None
    if hard:
        pid = read_pid(project_id)
        if pid:
            try:
                if os.name == "nt":
                    # Terminate process tree on Windows
                    import subprocess

                    subprocess.run(
                        ["taskkill", "/PID", str(pid), "/T", "/F"],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                else:
                    os.kill(pid, signal.SIGTERM)
                killed = pid
                emit(project_id, "process_killed", pid=pid)
            except Exception as exc:
                emit(project_id, "process_kill_failed", pid=pid, error=str(exc))
    return {"cancel": str(path), "killed_pid": killed}


def clear_cancel(project_id: str):
    path = cancel_path(project_id)
    if path.exists():
        path.unlink()


def check_cancel(project_id: str):
    if cancel_path(project_id).exists():
        emit(project_id, "cancelled")
        raise CancelledError(f"project {project_id} cancelled")


def tail_events(project_id: str, *, follow: bool = False, sleep_s: float = 0.5, max_idle: float = 30.0):
    path = events_path(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    with path.open("r", encoding="utf-8") as f:
        idle = 0.0
        while True:
            line = f.readline()
            if line:
                idle = 0.0
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
                continue
            if not follow:
                break
            time.sleep(sleep_s)
            idle += sleep_s
            if idle >= max_idle:
                break
