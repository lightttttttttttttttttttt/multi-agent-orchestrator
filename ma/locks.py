from __future__ import annotations

import threading


class FileLockError(RuntimeError):
    pass


class FileLockManager:
    """Process-local file locks so parallel workers cannot claim the same path."""

    def __init__(self):
        self._lock = threading.Lock()
        self._owned: dict[str, str] = {}  # file -> task_id

    def acquire(self, task_id: str, files: list[str]):
        with self._lock:
            conflicts = [f for f in files if f in self._owned and self._owned[f] != task_id]
            if conflicts:
                holders = {f: self._owned[f] for f in conflicts}
                raise FileLockError(f"file lock conflict for {task_id}: {holders}")
            for f in files:
                self._owned[f] = task_id

    def release(self, task_id: str, files: list[str] | None = None):
        with self._lock:
            if files is None:
                files = [f for f, owner in self._owned.items() if owner == task_id]
            for f in files:
                if self._owned.get(f) == task_id:
                    del self._owned[f]
