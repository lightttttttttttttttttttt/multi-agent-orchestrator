from __future__ import annotations

import argparse
import json
from pathlib import Path

from .orchestrator import Orchestrator, load_9router_key
from .router import NineRouterClient, RouterError
from .store import TaskStore


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ma", description="Durable multi-model orchestrator over 9Router")
    p.add_argument("--db", default=str(Path.home() / ".ma" / "state.sqlite"))
    sub = p.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("repo")
    init.add_argument("goal")
    init.add_argument("--name", default="project")
    run = sub.add_parser("run")
    run.add_argument("project_id")
    run.add_argument("--until", choices=["design", "critique", "judgment", "audit", "report"])
    status = sub.add_parser("status")
    status.add_argument("project_id", nargs="?")
    show = sub.add_parser("show")
    show.add_argument("project_id")
    verify = sub.add_parser("verify")
    verify.add_argument("project_id")
    verify.add_argument("command_line")
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    store = TaskStore(args.db)
    try:
        if args.command == "init":
            repo = str(Path(args.repo).resolve())
            if not Path(repo).is_dir():
                raise SystemExit(f"repo does not exist: {repo}")
            project_id = store.create_project(args.name, repo, args.goal)
            print(project_id)
            return 0
        if args.command == "status":
            result = store.get_project(args.project_id) if args.project_id else store.list_projects()
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
        if args.command == "show":
            print(json.dumps({"project": store.get_project(args.project_id), "stages": store.list_stages(args.project_id)}, indent=2, ensure_ascii=False))
            return 0
        client = NineRouterClient("http://127.0.0.1:20128", load_9router_key(), timeout=15, attempts=3)
        orchestrator = Orchestrator(store, client)
        if args.command == "run":
            result = orchestrator.run(args.project_id, until=args.until)
            print(json.dumps(result.__dict__, indent=2))
            return 0
        if args.command == "verify":
            print(json.dumps(orchestrator.verify(args.project_id, args.command_line), indent=2, ensure_ascii=False))
            return 0
    except RouterError as exc:
        print(f"MODEL FAILURE: {exc}")
        return 2
    finally:
        store.close()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
