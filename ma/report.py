from __future__ import annotations

import json
from pathlib import Path


def export_markdown_report(store, project_id: str, usage_summary: dict | None = None, out_dir: str | Path | None = None) -> Path:
    project = store.get_project(project_id)
    stages = store.list_stages(project_id)
    tasks = store.list_tasks(project_id)
    out = Path(out_dir or (Path(project["repo"]) / ".ma" / "reports"))
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{project_id}.md"

    lines = [
        f"# ma report — {project_id}",
        "",
        f"- **name**: {project['name']}",
        f"- **status**: {project['status']}",
        f"- **repo**: `{project['repo']}`",
        f"- **goal**: {project['goal']}",
        "",
        "## Stages",
        "",
    ]
    for s in stages:
        art = (s.get("artifact") or "").strip().replace("\r\n", "\n")
        preview = art[:500] + ("…" if len(art) > 500 else "")
        lines += [
            f"### {s['stage']} (`{s.get('model','')}`)",
            "",
            "```",
            preview,
            "```",
            "",
        ]

    lines += ["## Tasks", ""]
    if not tasks:
        lines.append("_no tasks_")
    else:
        for t in tasks:
            lines.append(
                f"- `{t['id']}` [{t.get('status')}] allowed={t.get('allowed_files')} deps={t.get('depends_on')} — {t.get('goal')}"
            )
    lines.append("")

    if usage_summary:
        lines += [
            "## Usage",
            "",
            f"- calls: {usage_summary.get('total_calls')}",
            f"- prompt tokens: {usage_summary.get('total_prompt_tokens')}",
            f"- completion tokens: {usage_summary.get('total_completion_tokens')}",
            f"- est. cost USD: {usage_summary.get('total_cost_usd')}",
            "",
        ]
        for row in usage_summary.get("by_model") or []:
            lines.append(
                f"  - `{row['model']}`: calls={row['calls']} cost={row['cost_usd']}"
            )
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    # also dump raw json sidecar
    sidecar = out / f"{project_id}.json"
    sidecar.write_text(
        json.dumps(
            {
                "project": project,
                "stages": stages,
                "tasks": tasks,
                "usage": usage_summary,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path
