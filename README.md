# Quang Multi-Agent Orchestrator

Durable local controller that calls separate models through 9Router, persists stage artifacts in SQLite, rejects empty HTTP-200 model responses, isolates work in git worktrees, and records machine verification evidence.

## Model policy

| Role | Primary | Fallback |
|---|---|---|
| Design / Judgment / Audit | `Ntt_Codex10tr/gpt-5.6-sol` | `nttcodex/gpt-5.6-sol` |
| Critique | `nttcodex/grok-4.5-high` | Sol |
| Implementation | `nttcodex/deepseek-v4-pro` | `nttcodex/glm-5.2` |
| Report | Gemini Flash | Gemini 2.5 Flash → Sol |

Each model request times out after **15s**, retries up to **3 attempts**, then tries the next fallback model. HTTP 200 with empty content is a hard failure.

## Install

```bash
cd C:/Users/OS/multi-agent-orchestrator
python -m pip install -e .
```

## One command

```bash
ma ship C:/path/to/repo "Implement feature X" --verify "python -m unittest -v"
```

Optional auto-merge after Sol `APPROVE`:

```bash
ma ship C:/path/to/repo "Implement feature X" --verify "python -m unittest -v" --merge
```

Resume existing project:

```bash
ma ship C:/path/to/repo "goal" --project-id ABC123 --verify "pytest -q"
```

## Manual gates

```bash
ma init <repo> "<goal>" --name <name>
ma run PROJECT_ID --until judgment
ma implement PROJECT_ID
ma verify PROJECT_ID "python -m unittest -v"
ma audit PROJECT_ID
ma merge PROJECT_ID   # only after APPROVE
ma show PROJECT_ID
```

## What each stage does

1. **design** — Sol architects
2. **critique** — Grok red-teams
3. **judgment** — Sol final plan + optional task DAG JSON (`id`, `goal`, `allowed_files`, `verify_command`, `depends_on`)
4. **implementation** — DeepSeek/GLM returns unified diff into isolated worktree; out-of-scope files rejected
5. **verification** — machine runs test command; exit code must be 0
6. **audit** — Sol must return `APPROVE` first
7. **report** — evidence-grounded summary
8. **merge** — optional ff-only merge into current branch; never push remote

## Failure notification

On ship/model/gate failure, `ma` sends Telegram via Hermes env (`TELEGRAM_BOT_TOKEN` + home channel) and exits code `2`.

## State

SQLite default: `C:/Users/OS/.ma/state.sqlite`

Stage machine:

```text
INTAKE → DESIGN → CRITIQUE → JUDGMENT
→ IMPLEMENTATION → VERIFICATION → AUDIT → REPORT/DONE
```

## Tests

```bash
python -m unittest discover -s tests -v
```

## Honest boundary

Ships one vertical slice well: durable routing, fallback, worktree, allowed-files gate, machine verify, strong-model audit, optional merge, Telegram fail notify.

Still not a full software factory:
- no multi-worker parallel execution
- no file locks across concurrent projects
- task DAG is sequential single-implement for now (not one worktree per task)
- no remote push
- planning quality depends on live strong models under 15s×3
