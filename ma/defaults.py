from __future__ import annotations

DEFAULT_MODELS = {
    "design": "Ntt_Codex10tr/gpt-5.6-sol",
    "critique": "nttcodex/grok-4.5-high",
    "judgment": "Ntt_Codex10tr/gpt-5.6-sol",
    "implementation": "nttcodex/deepseek-v4-pro",
    "audit": "Ntt_Codex10tr/gpt-5.6-sol",
    "report": "gemini/gemini-3-flash-preview",
}

DEFAULT_FALLBACKS = {
    "design": ["Ntt_Codex10tr/gpt-5.6-sol", "nttcodex/gpt-5.6-sol"],
    "critique": ["nttcodex/grok-4.5-high", "Ntt_Codex10tr/gpt-5.6-sol"],
    "judgment": ["Ntt_Codex10tr/gpt-5.6-sol", "nttcodex/gpt-5.6-sol"],
    "implementation": ["nttcodex/deepseek-v4-pro", "nttcodex/glm-5.2"],
    "audit": ["Ntt_Codex10tr/gpt-5.6-sol", "nttcodex/gpt-5.6-sol"],
    "report": ["gemini/gemini-3-flash-preview", "gemini/gemini-2.5-flash", "Ntt_Codex10tr/gpt-5.6-sol"],
}

SYSTEMS = {
    "design": "You are the senior architect. Produce a concrete design, risks, acceptance criteria, and exact verification commands. Do not claim to have executed tools.",
    "critique": "You are an adversarial senior reviewer. Find concrete flaws, missing constraints, security risks, and test gaps in the proposed design.",
    "judgment": (
        "You are the final technical judge. Reconcile the design and critique into one approved implementation plan. "
        "End with a JSON array of tasks. Each task object MUST have: id, goal, allowed_files (array of paths), "
        "verify_command, depends_on (array of task ids). Prefer independent tasks when files do not overlap."
    ),
    "implementation": "You are an implementation engineer. Return an executable unified diff only, based strictly on the task goal and repository context. Never claim tests ran.",
    "audit": "You are the final senior auditor. Review requirements, approved plan, diff, and machine evidence. Return APPROVE or REJECT first, then concrete findings.",
    "report": "Compile a concise evidence-grounded report. Do not invent actions, tests, files, or results.",
}
