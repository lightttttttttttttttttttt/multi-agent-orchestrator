# Quang Multi-Agent Orchestrator (MVP)

A durable local controller that calls separate models through 9Router, persists stage artifacts in SQLite, rejects empty HTTP-200 model responses, and records machine verification evidence.

## Model policy

- Design/judgment/audit: `Ntt_Codex10tr/gpt-5.6-sol`
- Independent critique: `nttcodex/grok-4.5-high`
- Implementation target: `nttcodex/deepseek-v4-pro`
- Report: `gemini/gemini-3-flash-preview`

The controller times out each model request after 15 seconds, retries the same request up to 3 total attempts, then exits immediately with `MODEL FAILURE` and exit code 2. HTTP errors are not retried. HTTP 200 with empty content is a hard failure.

## Install

```bash
cd C:/Users/OS/multi-agent-orchestrator
python -m pip install -e .
```

## Use

```bash
ma init C:/path/to/git/repo "Build refresh-token rotation" --name auth
ma run PROJECT_ID --until judgment
ma status PROJECT_ID
ma show PROJECT_ID
```

The MVP deliberately stops before `implementation`. This prevents a model-produced patch from being applied without an explicit mutation/worktree gate. Machine verification can be recorded after implementation is present:

```bash
ma verify PROJECT_ID "python -m unittest discover -s tests -v"
```

## Current stage machine

```text
INTAKE -> DESIGN -> CRITIQUE -> JUDGMENT -> IMPLEMENTATION
       -> VERIFICATION -> AUDIT -> REPORT/DONE
```

SQLite defaults to `C:/Users/OS/.ma/state.sqlite`, so work can be resumed after the Hermes session or terminal process exits.

## Tests

```bash
python -m unittest discover -s tests -v
```

## Honest MVP boundary

This version proves durable model switching, retry/failure behavior, stage ordering, empty-output rejection, and verification evidence. It does not yet apply model patches, create git worktrees, merge branches, or execute workers in parallel. Those mutations remain approval-gated instead of pretending to be safe or complete.
