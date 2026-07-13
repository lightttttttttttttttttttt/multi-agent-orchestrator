# Quang Multi-Agent Orchestrator

Durable local controller over 9Router: multi-model roles, SQLite state, per-task git worktrees, DAG waves, file locks, parallel workers, machine verification, Sol audit, optional merge, Telegram fail notify.

## Model policy

| Role | Primary | Fallback |
|---|---|---|
| Design / Judgment / Audit | `Ntt_Codex10tr/gpt-5.6-sol` | `nttcodex/gpt-5.6-sol` |
| Critique | `nttcodex/grok-4.5-high` | Sol |
| Implementation | `nttcodex/deepseek-v4-pro` | `nttcodex/glm-5.2` |
| Report | Gemini Flash | Gemini 2.5 → Sol |

Timeout **15s × 3** per model, then next fallback. Empty HTTP 200 = fail.

## Install

```bash
cd C:/Users/OS/multi-agent-orchestrator
python -m pip install -e .
```

## One command

```bash
ma ship C:/path/to/repo "Implement feature X" --verify "python -m unittest -v"
ma ship ... --merge              # merge after Sol APPROVE
ma ship ... --project-id ABC     # resume
```

## Manual gates

```bash
ma init / ma run / ma implement / ma verify / ma audit / ma merge / ma show
```

## Pipeline

1. design → Sol  
2. critique → Grok  
3. judgment → Sol + task DAG JSON  
4. implementation → **per-task worktrees**, DAG waves, file locks, parallel independent tasks  
5. verification → machine tests on integration worktree  
6. audit → Sol must start with `APPROVE`  
7. report  
8. merge (optional, no remote push)

### Task DAG JSON (from judgment)

```json
[{
  "id": "T1",
  "goal": "...",
  "allowed_files": ["src/a.py"],
  "verify_command": "pytest -q",
  "depends_on": []
}]
```

- Tasks with no deps and no shared `allowed_files` run in the **same wave in parallel** (default `max_workers=2`).
- Overlapping files are serialized.
- Each task gets `ma/<project>/<task>` worktree + branch.
- Integration worktree merges task branches for final verify/audit/merge.

## Failure notify

Telegram via Hermes env. Exit code `2` on failure.

## State

`C:/Users/OS/.ma/state.sqlite`

## Tests

```bash
python -m unittest discover -s tests -v
```

## Boundary

Has: routing, fallback, DAG, file lock, parallel waves, per-task worktrees, verify, audit, optional merge, Telegram.

Not yet: remote push, multi-project global file lock across processes, dynamic replan mid-ship, budget/token caps.
