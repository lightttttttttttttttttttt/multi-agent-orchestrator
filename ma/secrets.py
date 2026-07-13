from __future__ import annotations

import re


class SecretScanError(RuntimeError):
    pass


# Patterns focused on high-confidence secrets in added diff lines / text.
_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github_pat", re.compile(r"ghp_[A-Za-z0-9]{20,}")),
    ("github_fine_grained", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("openai_key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("google_api_key", re.compile(r"AIza[0-9A-Za-z\-_]{20,}")),
    ("generic_bearer", re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][^'\"]{12,}['\"]")),
]

_BLOCKED_PATH_HINTS = (
    ".env",
    "id_rsa",
    "id_ed25519",
    "credentials.json",
    "service-account",
    ".pem",
    ".p12",
    ".key",
)


def scan_text_for_secrets(text: str) -> list[dict]:
    hits = []
    for name, pattern in _SECRET_PATTERNS:
        for match in pattern.finditer(text or ""):
            snippet = match.group(0)
            redacted = snippet[:4] + "…" + snippet[-4:] if len(snippet) > 12 else "***"
            hits.append({"type": name, "snippet": redacted})
    return hits


def scan_diff_for_secrets(diff: str) -> list[dict]:
    """Scan only added lines and path headers from a unified diff."""
    hits: list[dict] = []
    for line in (diff or "").splitlines():
        if line.startswith("+++ ") or line.startswith("diff --git "):
            path = line.split()[-1]
            if path.startswith("b/"):
                path = path[2:]
            lower = path.lower()
            if any(h in lower for h in _BLOCKED_PATH_HINTS):
                hits.append({"type": "blocked_path", "snippet": path})
        if line.startswith("+") and not line.startswith("+++"):
            hits.extend(scan_text_for_secrets(line[1:]))
    # de-dupe
    uniq = []
    seen = set()
    for h in hits:
        key = (h["type"], h["snippet"])
        if key not in seen:
            seen.add(key)
            uniq.append(h)
    return uniq


def require_no_secrets(diff: str) -> str:
    hits = scan_diff_for_secrets(diff)
    if hits:
        summary = ", ".join(f"{h['type']}:{h['snippet']}" for h in hits[:8])
        raise SecretScanError(f"secret scan failed: {summary}")
    return diff
