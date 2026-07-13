from __future__ import annotations

import json
import time
from pathlib import Path


class CancelledError(RuntimeError):
    pass


def events_path(project_id: str, base: Path | None = None) -> Path:
    root = base or (Path.home() / ".ma" / "events")
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{project_id}.jsonl"


def cancel_path(project_id: str, base: Path | None = None) -> Path:
    root = base or (Path.home() / ".ma" / "events")
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{project_id}.cancel"


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


def request_cancel(project_id: str) -> Path:
    path = cancel_path(project_id)
    path.write_text(str(int(time.time())), encoding="utf-8")
    emit(project_id, "cancel_requested")
    return path


def clear_cancel(project_id: str):
    path = cancel_path(project_id)
    if path.exists():
        path.unlink()


def check_cancel(project_id: str):
    if cancel_path(project_id).exists():
        emit(project_id, "cancelled")
        raise CancelledError(f"project {project_id} cancelled")


def tail_events(project_id: str, *, follow: bool = False, sleep_s: float = 0.5, max_idle: float = 30.0):
    """Yield event dicts. If follow, keep reading until idle timeout after EOF."""
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
