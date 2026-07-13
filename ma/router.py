from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


class RouterError(RuntimeError):
    pass


class EmptyResponseError(RouterError):
    pass


@dataclass(frozen=True)
class ModelResult:
    model: str
    content: str
    latency_ms: int
    raw: dict
    prompt_chars: int = 0


class NineRouterClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 15, attempts: int = 3, budget=None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.attempts = attempts
        self.budget = budget

    def call(self, model: str, prompt: str, *, system: str = "", api: str = "chat") -> ModelResult:
        if api == "responses":
            endpoint = "/v1/responses"
            payload = {"model": model, "input": prompt, "stream": False}
            if system:
                payload["instructions"] = system
        else:
            endpoint = "/v1/chat/completions"
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            payload = {"model": model, "messages": messages, "stream": False}
        request = urllib.request.Request(
            self.base_url + endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"},
        )
        started = time.monotonic()
        last_error = None
        for attempt in range(1, self.attempts + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw_bytes = response.read()
                break
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:1000]
                raise RouterError(f"{model}: HTTP {exc.code}: {detail}") from exc
            except Exception as exc:
                last_error = exc
                if attempt == self.attempts:
                    raise RouterError(
                        f"{model}: failed after {self.attempts} attempts "
                        f"({self.timeout}s each): {type(exc).__name__}: {exc}"
                    ) from exc
        else:
            raise RouterError(f"{model}: failed after {self.attempts} attempts: {last_error}")
        try:
            raw = json.loads(raw_bytes.decode("utf-8", "replace"))
        except json.JSONDecodeError as exc:
            raise RouterError(f"{model}: non-JSON response: {raw_bytes[:300]!r}") from exc
        content = self._extract(raw, api)
        if not content.strip():
            raise EmptyResponseError(f"{model}: HTTP success but empty model output")
        if self.budget is not None:
            self.budget.charge(prompt=system + "\n" + prompt, content=content, model=model)
        return ModelResult(
            model,
            content.strip(),
            int((time.monotonic() - started) * 1000),
            raw,
            prompt_chars=len(system) + len(prompt),
        )

    @staticmethod
    def _extract(raw: dict, api: str) -> str:
        if api == "chat":
            try:
                value = raw["choices"][0]["message"]["content"]
                return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            except (KeyError, IndexError, TypeError):
                return ""
        if isinstance(raw.get("output_text"), str):
            return raw["output_text"]
        parts = []
        for item in raw.get("output", []):
            if item.get("type") != "message":
                continue
            for block in item.get("content", []):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
