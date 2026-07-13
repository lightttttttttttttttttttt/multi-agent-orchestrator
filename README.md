# Quang Multi-Agent Orchestrator

Durable local multi-model software factory over 9Router.

## Features

- Role routing: Sol / Grok / DeepSeek / Gemini + fallbacks
- 15s × 3 timeout brake, then next model
- SQLite durable project/task state
- Task DAG with `allowed_files` + dependencies
- Per-task git worktrees/branches
- DAG waves + **cross-process file locks** (`~/.ma/file_locks.sqlite`)
- Parallel independent tasks (`--workers`)
- Integration worktree for final verify/audit/merge
- Machine verification gate
- Sol `APPROVE` audit gate
- Mid-ship **replan** on task failure (`--max-replans`)
- **Budget caps**: `--max-calls`, `--max-tokens`
- Optional merge + optional remote push
- Telegram failure notify

## Install

```bash
cd C:/Users/OS/multi-agent-orchestrator
python -m pip install -e .
```

## Use

```bash
# one command
ma ship C:/repo "goal" --verify "pytest -q"

# with budgets + parallel workers
ma ship C:/repo "goal" --verify "pytest -q" --workers 2 --max-calls 40 --max-tokens 200000 --max-replans 1

# merge after APPROVE
ma ship C:/repo "goal" --verify "pytest -q" --merge

# merge + push
ma ship C:/repo "goal" --verify "pytest -q" --push --remote origin

# resume
ma ship C:/repo "goal" --project-id ABC --verify "pytest -q"
```

Manual:

```bash
ma init / ma run / ma implement / ma verify / ma audit / ma merge [--push] / ma show
```

## Task DAG (judgment output)

```json
[{
  "id": "T1",
  "goal": "...",
  "allowed_files": ["src/a.py"],
  "verify_command": "pytest -q",
  "depends_on": []
}]
```

Independent non-overlapping tasks run in parallel. Failures can replan once (default).

## Safety

- No push unless `--push`
- No merge unless `--merge`/`--push` and Sol returns APPROVE
- Empty model content fails
- Patch outside `allowed_files` fails
- Budget exceeded exits 2 + Telegram notify

## Tests

```bash
python -m unittest discover -s tests -v
```

## Boundary

Still not: multi-machine orchestration, automatic secret scanning beyond allowed_files, dynamic worker pool autoscaling, cost accounting against real provider invoices.
