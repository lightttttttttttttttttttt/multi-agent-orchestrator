# Quang Multi-Agent Factory

```bash
ma doctor
ma ship C:/repo "goal" --verify "pytest -q" --workers 0
ma report PROJECT_ID
ma usage [PROJECT_ID]
ma clean PROJECT_ID
```

Ship now:
- preflights 9Router/git/key
- blacklists dead models for the run
- writes `.ma/reports/<id>.md` + `.json`
