# multi-agent-orchestrator (`ma`)

Local multi-model software factory over [9Router](http://127.0.0.1:20128).

`ma` does **not** role-play one chat into many agents.  
It runs a durable pipeline where each stage calls a separate model, persists state in SQLite, isolates code changes in git worktrees, verifies with real commands, and only merges after hard gates pass.

---

## What it does

```text
preflight
  → design (Sol)
  → critique (Grok)
  → judgment (Sol)          # task DAG JSON
  → implement (DeepSeek/GLM) # per-task worktrees, parallel waves
  → verify                  # machine tests
  → audit (Sol)             # must start with APPROVE
  → report (Gemini)
  → optional merge/push     # optional human approval
```

### Highlights

- **Separate model requests per role** via local 9Router
- **15s × 3 timeout brake**, then role fallback / hard fail
- **Dead-model blacklist** inside a run (failed model not retried forever)
- **Task DAG** with `allowed_files`, `depends_on`, `verify_command`
- **Per-task git worktrees** + file locks (cross-process)
- **Parallel workers** (`--workers 0` auto-scales up to 4)
- **Secret scan** on patches before apply/merge
- **Budget caps** (`--max-calls`, `--max-tokens`)
- **Usage/cost ledger** (provider tokens preferred, estimate fallback)
- **Telegram notify** on failure
- **Event stream** + `watch` / hard `cancel` (PID kill)
- **Human approval gate** before merge/push
- **Job queue** for multi-worker machines (`enqueue` / `worker`)
- **Self-hosted GitHub Action** template

---

## Requirements

- Python **3.11+**
- Git
- Local **9Router** on `http://127.0.0.1:20128`
- Windows / Linux / macOS (hard cancel uses `taskkill` on Windows)

Optional:
- Telegram bot env vars for failure alerts
- `MA_QUEUE_TOKEN` for multi-worker queue auth
- Self-hosted GitHub runner labeled `ma`

---

## Install

```bash
git clone https://github.com/lightttttttttttttttttttt/multi-agent-orchestrator.git
cd multi-agent-orchestrator
python -m pip install -e .
ma doctor --no-probe
```

Package name: `quang-multi-agent`  
CLI entrypoint: `ma`

---

## Quick start

```bash
# health
ma doctor

# one-command ship
ma ship C:/path/to/repo "Add subtract(a, b) to calc.py" \
  --verify "python -m unittest -v" \
  --workers 0

# watch progress in another terminal
ma watch PROJECT_ID --follow

# cancel hard (kills ship process tree on Windows)
ma cancel PROJECT_ID

# soft cancel only (cooperative between stages)
ma cancel PROJECT_ID --soft
```

### Merge safely

```bash
# audit APPROVE is required; human approval also required with this flag
ma ship C:/path/to/repo "goal" --verify "pytest -q" --merge --require-approval

# grant human approval, then merge
ma approve PROJECT_ID
ma merge PROJECT_ID --require-approval

# push also requires approval
ma merge PROJECT_ID --push --require-approval
```

---

## CLI map

| Command | Purpose |
|---|---|
| `ma doctor` | Preflight 9Router / key / git / `~/.ma` |
| `ma ship` | Full pipeline |
| `ma init` / `run` / `status` / `show` | Project lifecycle |
| `ma implement` / `verify` / `audit` | Manual stage control |
| `ma merge` | Fast-forward merge after APPROVE |
| `ma watch` | Tail JSONL events |
| `ma cancel` | Soft/hard cancel |
| `ma approve` | Human merge/push gate |
| `ma report` | Export markdown/json report |
| `ma usage` | Cost/token ledger |
| `ma clean` | Remove project worktrees |
| `ma enqueue` / `worker` / `queue` / `job` | Multi-worker job queue |

```bash
ma --help
```

---

## Default model routing

| Stage | Primary | Fallback |
|---|---|---|
| design / judgment / audit | `Ntt_Codex10tr/gpt-5.6-sol` | `nttcodex/gpt-5.6-sol` |
| critique | `nttcodex/grok-4.5-high` | Sol |
| implementation | `nttcodex/deepseek-v4-pro` | `nttcodex/glm-5.2` |
| report | `gemini/gemini-3-flash-preview` | Gemini 2.5 → Sol |

Each model call: **timeout 15s, up to 3 attempts**, then next fallback.  
HTTP 200 with empty content is treated as failure.

Routing is configured in `ma/defaults.py`.

---

## Safety gates (not optional)

1. **Preflight** — 9Router up, API key, git
2. **Empty model content** rejected
3. **Unified diff only** for implement workers
4. **Secret scan** on patch/diff
5. **`allowed_files` scope** enforcement
6. **Machine verify** exit code must be `0`
7. **Sol audit** must begin with `APPROVE`
8. **Budget** can hard-stop a run
9. **Human approval** when `--require-approval` / `--push`
10. **No auto-merge by default** — merge is explicit

`ma` will not invent green tests or claim success without tool output.

---

## Task DAG format

Judgment stage should end with a JSON array like:

```json
[
  {
    "id": "T_CALC",
    "goal": "Add subtract(a, b) returning a - b",
    "allowed_files": ["calc.py"],
    "verify_command": "python -m unittest -v",
    "depends_on": []
  },
  {
    "id": "T_TEXT",
    "goal": "Add whisper(s) helper",
    "allowed_files": ["textutil.py"],
    "verify_command": "python -m unittest -v",
    "depends_on": []
  }
]
```

Rules:
- Independent tasks with non-overlapping files can run in parallel waves
- Overlapping `allowed_files` or dependencies force later waves
- Patch touching files outside `allowed_files` fails

---

## State & artifacts

| Path | Content |
|---|---|
| `~/.ma/state.sqlite` | Projects, stages, tasks, evidence |
| `~/.ma/events/<id>.jsonl` | Progress events |
| `~/.ma/events/<id>.pid` | Ship process PID |
| `~/.ma/events/<id>.cancel` | Cancel marker |
| `~/.ma/approvals/` | Human approval files |
| `~/.ma/queue.sqlite` | Worker job queue |
| `~/.ma/usage.sqlite` | Token/cost ledger |
| `~/.ma/rates.json` | Optional USD/1M rates override |
| `~/.ma/file_locks.sqlite` | Cross-process file locks |
| `<repo>/.ma/reports/<id>.md` | Ship report |
| sibling worktrees | `*-ma-<project>*` |

---

## Multi-worker queue

```bash
# optional shared auth
export MA_QUEUE_TOKEN=super-secret
# or write ~/.ma/queue.token

ma enqueue C:/path/to/repo "goal" --verify "pytest -q" --token "$MA_QUEUE_TOKEN"
ma worker --once --token "$MA_QUEUE_TOKEN"
ma queue
ma job JOB_ID
```

If `MA_QUEUE_TOKEN` / `~/.ma/queue.token` is set, enqueue/claim/complete require the correct token.  
If unset, local open mode is allowed.

---

## GitHub Action (self-hosted)

Workflow: [`.github/workflows/ma-ship.yml`](.github/workflows/ma-ship.yml)

Designed for a **self-hosted Windows runner** with labels:

```text
[self-hosted, Windows, ma]
```

so the runner can reach local 9Router (`localhost:20128`).

Supports:
- `workflow_dispatch` with goal / verify / merge
- PR title as goal

---

## Useful ship flags

```bash
ma ship REPO "goal" \
  --verify "pytest -q" \
  --workers 0 \
  --max-calls 40 \
  --max-tokens 200000 \
  --max-replans 1 \
  --project-id EXISTING_ID \
  --merge \
  --require-approval \
  --push
```

| Flag | Meaning |
|---|---|
| `--workers 0` | Auto workers by wave size (≤ 4) |
| `--max-calls` | Hard call budget |
| `--max-tokens` | Hard token budget |
| `--max-replans` | Sol replan attempts after task fail |
| `--project-id` | Resume existing project |
| `--merge` | Merge after APPROVE |
| `--require-approval` | Need `ma approve` first |
| `--push` | Push after merge (implies approval gate) |

---

## Cost accounting

```bash
ma usage
ma usage PROJECT_ID
```

- Prefers provider `usage.prompt_tokens` / `completion_tokens`
- Falls back to `chars/4` estimate
- Override rates in `~/.ma/rates.json`

Example rates file:

```json
{
  "default": {"in": 0.5, "out": 1.5},
  "sol": {"in": 5.0, "out": 15.0},
  "deepseek": {"in": 0.3, "out": 0.8},
  "glm": {"in": 0.4, "out": 1.0},
  "grok": {"in": 2.0, "out": 6.0},
  "gemini": {"in": 0.2, "out": 0.6}
}
```

Costs are **estimates** unless the provider returns real usage.

---

## Tests

```bash
python -m unittest discover -s tests -v
```

Current suite covers routing retries, gates, DAG waves, locks, secrets, usage, queue auth, approval, events/cancel, worktrees, and ops helpers.

---

## Design stance

- Strong models decide / audit
- Cheap models write bounded diffs
- Machines verify
- Humans approve merge/push when asked
- Empty success is forbidden
- Fail loud, notify, stop

This is a **local software factory**, not a chat toy.

---

## License / status

Personal tooling for multi-model delivery over a local 9Router gateway.  
Expect sharp edges around long planning prompts, provider rate limits, and self-hosted runner setup.

---

## Minimal mental model

```text
Sol plans + judges + audits
Grok red-teams
DeepSeek/GLM codes in isolated worktrees
pytest/unittest decides reality
ma ships only when gates pass
```
