from __future__ import annotations

import time
from pathlib import Path


class ApprovalError(RuntimeError):
    pass


def _root(base: Path | None = None) -> Path:
    root = base or (Path.home() / ".ma" / "approvals")
    root.mkdir(parents=True, exist_ok=True)
    return root


def approval_path(project_id: str, base: Path | None = None) -> Path:
    return _root(base) / f"{project_id}.approve"


def request_human_approval(project_id: str, reason: str = "merge/push requires human approval") -> Path:
    path = approval_path(project_id)
    # do not create approval file; only emit marker note for operator
    note = _root() / f"{project_id}.pending"
    note.write_text(f"{int(time.time())}\n{reason}\n", encoding="utf-8")
    return note


def grant_approval(project_id: str, note: str = "approved") -> Path:
    path = approval_path(project_id)
    path.write_text(f"{int(time.time())}\n{note}\n", encoding="utf-8")
    pending = _root() / f"{project_id}.pending"
    if pending.exists():
        pending.unlink()
    return path


def revoke_approval(project_id: str):
    path = approval_path(project_id)
    if path.exists():
        path.unlink()


def require_human_approval(project_id: str):
    path = approval_path(project_id)
    if not path.exists():
        request_human_approval(project_id)
        raise ApprovalError(
            f"human approval required for {project_id}. "
            f"Run: ma approve {project_id}"
        )
