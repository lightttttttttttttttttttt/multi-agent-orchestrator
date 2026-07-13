# Quang Multi-Agent Orchestrator

Local multi-model software factory over 9Router.

## Capabilities

- Role routing + fallbacks (Sol / Grok / DeepSeek / Gemini)
- 15s × 3 timeout brake
- SQLite durable state
- Task DAG, allowed_files, dependency waves
- Per-task worktrees + integration worktree
- Cross-process file locks
- Parallel workers
- Secret scan on patches/diffs
- Usage/cost ledger (`ma usage`)
- Budget caps (`--max-calls`, `--max-tokens`)
- Replan on task failure
- Machine verify + Sol APPROVE
- Optional merge / push
- Telegram fail notify

## Install

```bash
cd C:/Users/OS/multi-agent-orchestrator
python -m pip install -e .
```

## Use

```bash
ma ship C:/repo "goal" --verify "pytest -q" --workers 2 --max-calls 40 --max-tokens 200000
ma ship ... --merge
ma ship ... --push
ma usage
ma usage PROJECT_ID
ma show PROJECT_ID
```

## Safety

- Secrets in added diff lines / blocked paths → fail
- Out-of-scope files → fail
- Tests fail → stop
- No APPROVE → no merge
- No push unless `--push`

## Tests

```bash
python -m unittest discover -s tests -v
```

## Boundary

Not: multi-machine workers, real provider invoice reconciliation, autoscaling pools.
