from __future__ import annotations

import argparse
import json
from pathlib import Path

from .gates import GateError
from .locks import Budget, BudgetExceeded
from .notify import notify_failure
from .orchestrator import Orchestrator, load_9router_key
from .router import NineRouterClient, RouterError
from .secrets import SecretScanError
from .store import TaskStore
from .tasks import TaskSpecError
from .usage import UsageLedger
from .workspace import WorkspaceError


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

    implement = sub.add_parser("implement")
    implement.add_argument("project_id")

    audit = sub.add_parser("audit")
    audit.add_argument("project_id")

    merge = sub.add_parser("merge")
    merge.add_argument("project_id")
    merge.add_argument("--push", action="store_true")
    merge.add_argument("--remote", default="origin")

    ship = sub.add_parser("ship")
    ship.add_argument("repo")
    ship.add_argument("goal")
    ship.add_argument("--name", default="ship")
    ship.add_argument("--verify", default=None)
    ship.add_argument("--merge", action="store_true")
    ship.add_argument("--push", action="store_true", help="push after merge (implies --merge)")
    ship.add_argument("--remote", default="origin")
    ship.add_argument("--project-id", default=None)
    ship.add_argument("--max-calls", type=int, default=None, help="budget: max model calls")
    ship.add_argument("--max-tokens", type=int, default=None, help="budget: rough max tokens (chars/4)")
    ship.add_argument("--max-replans", type=int, default=1)
    ship.add_argument("--workers", type=int, default=2)

    usage = sub.add_parser("usage")
    usage.add_argument("project_id", nargs="?")
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
            print(
                json.dumps(
                    {
                        "project": store.get_project(args.project_id),
                        "stages": store.list_stages(args.project_id),
                        "tasks": store.list_tasks(args.project_id),
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return 0
        if args.command == "usage":
            ledger = UsageLedger()
            try:
                print(json.dumps(ledger.summary(args.project_id), indent=2, ensure_ascii=False))
            finally:
                ledger.close()
            return 0

        budget = None
        max_replans = 1
        workers = 2
        if args.command == "ship":
            budget = Budget(max_calls=args.max_calls, max_tokens=args.max_tokens)
            max_replans = args.max_replans
            workers = args.workers

        client = NineRouterClient(
            "http://127.0.0.1:20128",
            load_9router_key(),
            timeout=15,
            attempts=3,
            budget=budget,
        )
        orchestrator = Orchestrator(
            store,
            client,
            max_workers=workers,
            budget=budget,
            max_replans=max_replans,
        )

        if args.command == "run":
            result = orchestrator.run(args.project_id, until=args.until)
            print(json.dumps(result.__dict__, indent=2))
            return 0
        if args.command == "verify":
            print(json.dumps(orchestrator.verify(args.project_id, args.command_line), indent=2, ensure_ascii=False))
            return 0
        if args.command == "implement":
            print(json.dumps(orchestrator.implement(args.project_id), indent=2, ensure_ascii=False))
            return 0
        if args.command == "audit":
            print(orchestrator.audit(args.project_id))
            return 0
        if args.command == "merge":
            print(
                json.dumps(
                    orchestrator.merge(args.project_id, push=args.push, remote=args.remote),
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return 0
        if args.command == "ship":
            repo = str(Path(args.repo).resolve())
            if not Path(repo).is_dir():
                raise SystemExit(f"repo does not exist: {repo}")
            pid = args.project_id or store.create_project(args.name, repo, args.goal)
            do_merge = args.merge or args.push
            result = orchestrator.ship(
                pid,
                verify_command=args.verify,
                merge=do_merge,
                push=args.push,
                remote=args.remote,
            )
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
    except (RouterError, GateError, WorkspaceError, TaskSpecError, BudgetExceeded, SecretScanError) as exc:
        note = notify_failure(f"ma {args.command} FAILED: {type(exc).__name__}: {exc}")
        print(f"MODEL FAILURE: {exc}")
        print(json.dumps({"notify": note}, ensure_ascii=False))
        return 2
    finally:
        store.close()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
