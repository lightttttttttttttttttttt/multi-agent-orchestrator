from __future__ import annotations

import re
import subprocess
from pathlib import Path


class WorkspaceError(RuntimeError):
    pass


def extract_diff(text: str) -> str:
    fenced = re.search(r"```(?:diff|patch)?\s*\n(.*?)```", text, re.S)
    candidate = fenced.group(1) if fenced else text
    start = candidate.find("diff --git ")
    if start < 0:
        raise WorkspaceError("worker returned no unified git diff")
    return candidate[start:].strip() + "\n"


class Workspace:
    def __init__(self, repo: str | Path, project_id: str, task_id: str | None = None):
        self.repo = Path(repo).resolve()
        self.project_id = project_id
        self.task_id = task_id
        suffix = f"{project_id}-{task_id}" if task_id else project_id
        self.path = self.repo.parent / f"{self.repo.name}-ma-{suffix}"
        self.branch = f"ma/{project_id}/{task_id}" if task_id else f"ma/{project_id}"

    def _run(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess:
        result = subprocess.run(args, cwd=cwd or self.repo, input=input_text, text=True, capture_output=True)
        if result.returncode:
            raise WorkspaceError(f"{' '.join(args)} failed: {(result.stderr or result.stdout).strip()}")
        return result

    def create(self, *, base: str = "HEAD") -> Path:
        if not (self.repo / ".git").exists():
            raise WorkspaceError(f"not a git repository: {self.repo}")
        if self.path.exists():
            return self.path
        branch_exists = (
            subprocess.run(
                ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{self.branch}"],
                cwd=self.repo,
            ).returncode
            == 0
        )
        args = ["git", "worktree", "add"]
        if branch_exists:
            args += [str(self.path), self.branch]
        else:
            args += ["-b", self.branch, str(self.path), base]
        self._run(args)
        return self.path

    def snapshot(self, max_chars: int = 24000) -> str:
        files = self._run(["git", "ls-files"], cwd=self.path).stdout.splitlines()
        chunks, size = [], 0
        for name in files:
            path = self.path / name
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            block = f"\n### FILE: {name}\n{text}\n"
            if size + len(block) > max_chars:
                break
            chunks.append(block)
            size += len(block)
        return "".join(chunks)

    def apply_patch(self, patch: str):
        self._run(["git", "apply", "--check", "-"], cwd=self.path, input_text=patch)
        self._run(["git", "apply", "-"], cwd=self.path, input_text=patch)

    def diff(self) -> str:
        return self._run(["git", "diff", "--binary"], cwd=self.path).stdout

    def commit(self, message: str):
        status = self._run(["git", "status", "--porcelain"], cwd=self.path).stdout.strip()
        if not status:
            return
        self._run(["git", "add", "-A"], cwd=self.path)
        self._run(["git", "commit", "-m", message], cwd=self.path)

    def reset_hard(self):
        if self.path.exists():
            subprocess.run(["git", "reset", "--hard", "HEAD"], cwd=self.path, capture_output=True)
            subprocess.run(["git", "clean", "-fd"], cwd=self.path, capture_output=True)

    def current_branch(self) -> str:
        return self._run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=self.repo).stdout.strip()

    def merge_branch(self, branch: str, *, cwd: Path | None = None):
        self._run(["git", "merge", "--no-ff", "--no-edit", "-m", f"ma: merge {branch}", branch], cwd=cwd or self.path)

    def merge_into_repo(self, message: str = "ma: merge approved worktree") -> dict:
        target = self.current_branch()
        self.commit(message)
        if target == self.branch:
            return {"merged_into": target, "branch": self.branch, "mode": "already-on-branch"}
        # Prefer no-ff so multi-task history is preserved; fall back to ff-only for single commits.
        try:
            self._run(
                ["git", "merge", "--no-ff", "--no-edit", "-m", message, self.branch],
                cwd=self.repo,
            )
            mode = "no-ff"
        except WorkspaceError:
            self._run(["git", "merge", "--ff-only", self.branch], cwd=self.repo)
            mode = "ff-only"
        return {"merged_into": target, "branch": self.branch, "mode": mode}

    def remove(self):
        if self.path.exists():
            self._run(["git", "worktree", "remove", "--force", str(self.path)])
