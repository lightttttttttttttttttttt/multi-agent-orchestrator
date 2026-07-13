# ma factory

```bash
ma doctor
ma ship C:/repo "goal" --verify "pytest -q" --workers 0
ma ship ... --merge --require-approval
ma approve PROJECT_ID
ma cancel PROJECT_ID
ma watch PROJECT_ID --follow
ma report / usage / clean

# queue workers (optional auth)
# set MA_QUEUE_TOKEN or ~/.ma/queue.token
ma enqueue C:/repo "goal" --verify "pytest -q" --token $MA_QUEUE_TOKEN
ma worker --once --token $MA_QUEUE_TOKEN
ma queue
```

Cost rates override: `~/.ma/rates.json`  
Approvals: `~/.ma/approvals/`  
Queue: `~/.ma/queue.sqlite`  
Events/PID: `~/.ma/events/`  
GH Action: self-hosted labels `[self-hosted, Windows, ma]`
