from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def _load_env() -> dict[str, str]:
    env = dict(os.environ)
    path = Path(os.path.expandvars(r"%LOCALAPPDATA%\hermes\.env"))
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return env


def notify_failure(message: str) -> dict:
    env = _load_env()
    token = env.get("TELEGRAM_BOT_TOKEN")
    chat_id = env.get("TELEGRAM_HOME_CHANNEL") or env.get("TELEGRAM_ALLOWED_USERS", "").split(",")[0]
    if not token or not chat_id:
        return {"sent": False, "reason": "telegram credentials missing"}
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({"chat_id": chat_id, "text": message[:3500]}).encode()
    req = urllib.request.Request(url, data=payload, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
        return {"sent": bool(body.get("ok")), "provider": "telegram", "raw": body}
    except urllib.error.HTTPError as exc:
        return {"sent": False, "reason": f"HTTP {exc.code}: {exc.read()[:300]!r}"}
    except Exception as exc:
        return {"sent": False, "reason": f"{type(exc).__name__}: {exc}"}
