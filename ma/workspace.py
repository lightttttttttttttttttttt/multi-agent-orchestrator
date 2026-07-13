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
    def __init__(self, repo: str | Path, project_id: str):
        self.repo = Path(repo).resolve()
        self.project_id = project_id
        self.path = self.repo.parent / f"{self.repo.name}-ma-{project_id}"
        self.branch = f"ma/{project_id}"

    def _run(self, args: list[str], *, cwd: Path | None = None, input_text: str | None = None) -> subprocess.CompletedProcess:
        result = subprocess.run(args, cwd=cwd or self.repo, input=input_text, text=True, capture_output=True)
        if result.returncode:
            raise WorkspaceError(f"{' '.join(args)} failed: {(result.stderr or result.stdout).strip()}")
        return result

    def create(self) -> Path:
        if not (self.repo / ".git").exists():
            raise WorkspaceError(f"not a git repository: {self.repo}")
        if self.path.exists():
            return self.path
        branch_exists = subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{self.branch}"],
            cwd=self.repo,
        ).returncode == 0
        args = ["git", "worktree", "add"]
        args += [str(self.path), self.branch] if branch_exists else ["-b", self.branch, str(self.path), "HEAD"]
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
        self._run(["git", "add", "-A"], cwd=self.path)
        # if nothing staged, git commit fails; that's OK to surface
        self._run(["git", "commit", "-m", message], cwd=self.path)

    def current_branch(self) -> str:
        return self._run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=self.repo).stdout.strip()

    def merge_into_repo(self, message: str = "ma: merge approved worktree") -> dict:
        """Commit worktree branch, then fast-forward merge into the repo's current branch."""
        target = self.current_branch()
        if target == self.branch:
            # already on feature branch in main workdir; just commit worktree tip if needed
            self.commit(message)
            return {"merged_into": target, "branch": self.branch, "mode": "already-on-branch"}
        self.commit(message)
        # merge feature branch into current branch of main repo
        self._run(["git", "merge", "--ff-only", self.branch], cwd=self.repo)
        return {"merged_into": target, "branch": self.branch, "mode": "ff-only"}

    def remove(self):
        if self.path.exists():
            self._run(["git", "worktree", "remove", "--force", str(self.path)])
