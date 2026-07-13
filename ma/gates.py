class GateError(RuntimeError):
    pass


def require_model_content(content: str) -> str:
    if not content or not content.strip():
        raise GateError("model returned no usable content")
    return content.strip()


def require_command_success(evidence: dict) -> dict:
    if evidence.get("exit_code") != 0:
        raise GateError(f"command failed with exit code {evidence.get('exit_code')}")
    return evidence
