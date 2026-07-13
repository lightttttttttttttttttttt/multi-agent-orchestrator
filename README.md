# Quang Multi-Agent Orchestrator

Local multi-model software factory over 9Router.

## Commands

```bash
ma doctor                 # preflight + model probe
ma doctor --no-probe
ma ship C:/repo "goal" --verify "pytest -q" --workers 0   # 0=auto up to 4
ma ship ... --merge / --push
ma usage [PROJECT_ID]
ma clean PROJECT_ID [--delete-branches]
ma show PROJECT_ID
```

## Features

- Role routing + fallbacks
- 15s × 3 timeout
- Task DAG / allowed_files / waves
- Per-task worktrees + integration worktree
- Cross-process file locks
- Auto worker scaling by wave size
- Secret scan
- Usage/cost ledger
- Budget caps
- Replan on failure
- Machine verify + Sol APPROVE
- Optional merge/push
- Telegram fail notify
- doctor / clean ops

## Install

```bash
python -m pip install -e C:/Users/OS/multi-agent-orchestrator
```

## Tests

```bash
python -m unittest discover -s tests -v
```
