# ma factory

```bash
ma doctor
ma ship C:/repo "goal" --verify "pytest -q" --workers 0
ma watch PROJECT_ID --follow
ma cancel PROJECT_ID          # hard kill PID if running
ma cancel PROJECT_ID --soft   # cooperative only
ma report PROJECT_ID
ma usage PROJECT_ID
ma clean PROJECT_ID

# remote/local worker queue
ma enqueue C:/repo "goal" --verify "pytest -q"
ma worker --once
ma queue
ma job JOB_ID
```

Artifacts:
- events/pid/cancel: `~/.ma/events/`
- queue: `~/.ma/queue.sqlite`
- usage: `~/.ma/usage.sqlite`
- GH Action: `.github/workflows/ma-ship.yml`
