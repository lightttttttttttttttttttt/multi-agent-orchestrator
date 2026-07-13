from __future__ import annotations

import argparse
import json
import os
import socket
import time
import traceback
from pathlib import Path

from .approve import ApprovalError, grant_approval, revoke_approval
from .events import CancelledError, request_cancel, tail_events
from .gates import GateError
from .locks import Budget, BudgetExceeded
from .notify import notify_failure
from .ops import clean_project, doctor
from .orchestrator import Orchestrator, load_9router_key
from .queue import JobQueue, QueueError
from .report import export_markdown_report
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
    merge.add_argument("--require-approval", action="store_true")

    ship = sub.add_parser("ship")
    ship.add_argument("repo")
    ship.add_argument("goal")
    ship.add_argument("--name", default="ship")
    ship.add_argument("--verify", default=None)
    ship.add_argument("--merge", action="store_true")
    ship.add_argument("--push", action="store_true")
    ship.add_argument("--remote", default="origin")
    ship.add_argument("--project-id", default=None)
    ship.add_argument("--max-calls", type=int, default=None)
    ship.add_argument("--max-tokens", type=int, default=None)
    ship.add_argument("--max-replans", type=int, default=1)
    ship.add_argument("--workers", type=int, default=0)
    ship.add_argument("--require-approval", action="store_true", help="require ma approve before merge/push")

    usage = sub.add_parser("usage")
    usage.add_argument("project_id", nargs="?")

    doc = sub.add_parser("doctor")
    doc.add_argument("--no-probe", action="store_true")
    doc.add_argument("--timeout", type=int, default=15)

    clean = sub.add_parser("clean")
    clean.add_argument("project_id")
    clean.add_argument("--delete-branches", action="store_true")

    report = sub.add_parser("report")
    report.add_argument("project_id")

    watch = sub.add_parser("watch")
    watch.add_argument("project_id")
    watch.add_argument("--follow", action="store_true")
    watch.add_argument("--idle", type=float, default=30.0)

    cancel = sub.add_parser("cancel")
    cancel.add_argument("project_id")
    cancel.add_argument("--soft", action="store_true")

    approve = sub.add_parser("approve")
    approve.add_argument("project_id")
    approve.add_argument("--note", default="approved")
    approve.add_argument("--revoke", action="store_true")

    enqueue = sub.add_parser("enqueue")
    enqueue.add_argument("repo")
    enqueue.add_argument("goal")
    enqueue.add_argument("--verify", default=None)
    enqueue.add_argument("--token", default=None)

    qlist = sub.add_parser("queue")
    qlist.add_argument("--limit", type=int, default=20)

    qget = sub.add_parser("job")
    qget.add_argument("job_id")

    worker = sub.add_parser("worker")
    worker.add_argument("--id", default=None)
    worker.add_argument("--once", action="store_true")
    worker.add_argument("--poll", type=float, default=2.0)
    worker.add_argument("--token", default=None)
    return p


def _token(args_token: str | None) -> str | None:
    return args_token or os.environ.get("MA_QUEUE_TOKEN")


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    store = TaskStore(args.db)
    try:
        if args.command == "init":
            repo = str(Path(args.repo).resolve())
            if not Path(repo).is_dir():
                raise SystemExit(f"repo does not exist: {repo}")
            print(store.create_project(args.name, repo, args.goal))
            return 0
        if args.command == "status":
            print(json.dumps(store.get_project(args.project_id) if args.project_id else store.list_projects(), indent=2, ensure_ascii=False))
            return 0
        if args.command == "show":
            print(json.dumps({"project": store.get_project(args.project_id), "stages": store.list_stages(args.project_id), "tasks": store.list_tasks(args.project_id)}, indent=2, ensure_ascii=False))
            return 0
        if args.command == "usage":
            ledger = UsageLedger()
            try:
                print(json.dumps(ledger.summary(args.project_id), indent=2, ensure_ascii=False))
            finally:
                ledger.close()
            return 0
        if args.command == "doctor":
            report = doctor(probe_models=not args.no_probe, timeout=args.timeout)
            print(json.dumps(report, indent=2, ensure_ascii=False))
            return 0 if report["ok"] else 2
        if args.command == "clean":
            print(json.dumps(clean_project(store, args.project_id, delete_branches=args.delete_branches), indent=2, ensure_ascii=False))
            return 0
        if args.command == "report":
            ledger = UsageLedger()
            try:
                usage = ledger.summary(args.project_id)
            finally:
                ledger.close()
            print(json.dumps({"report": str(export_markdown_report(store, args.project_id, usage))}, indent=2))
            return 0
        if args.command == "watch":
            for ev in tail_events(args.project_id, follow=args.follow, max_idle=args.idle):
                print(json.dumps(ev, ensure_ascii=False), flush=True)
            return 0
        if args.command == "cancel":
            print(json.dumps(request_cancel(args.project_id, hard=not args.soft), indent=2))
            return 0
        if args.command == "approve":
            if args.revoke:
                revoke_approval(args.project_id)
                print(json.dumps({"revoked": args.project_id}))
            else:
                path = grant_approval(args.project_id, args.note)
                print(json.dumps({"approved": args.project_id, "path": str(path)}))
            return 0
        if args.command == "enqueue":
            q = JobQueue()
            try:
                job_id = q.enqueue(repo=str(Path(args.repo).resolve()), goal=args.goal, verify_command=args.verify, token=_token(args.token))
                print(json.dumps({"job_id": job_id}, indent=2))
            finally:
                q.close()
            return 0
        if args.command == "queue":
            q = JobQueue()
            try:
                print(json.dumps(q.list(args.limit), indent=2, ensure_ascii=False))
            finally:
                q.close()
            return 0
        if args.command == "job":
            q = JobQueue()
            try:
                print(json.dumps(q.get(args.job_id), indent=2, ensure_ascii=False))
            finally:
                q.close()
            return 0
        if args.command == "worker":
            worker_id = args.id or f"{socket.gethostname()}-{int(time.time())}"
            token = _token(args.token)
            q = JobQueue()
            try:
                while True:
                    job = q.claim(worker_id, token=token)
                    if not job:
                        if args.once:
                            print(json.dumps({"worker": worker_id, "claimed": None}))
                            return 0
                        time.sleep(args.poll)
                        continue
                    print(json.dumps({"worker": worker_id, "claimed": job["id"], "repo": job["repo"]}, flush=True))
                    try:
                        client = NineRouterClient("http://127.0.0.1:20128", load_9router_key(), timeout=15, attempts=3)
                        orch = Orchestrator(store, client, max_workers=0)
                        pid = store.create_project(f"job-{job['id']}", job["repo"], job["goal"])
                        result = orch.ship(pid, verify_command=job.get("verify_command"), merge=False)
                        q.complete(job["id"], result, token=token)
                        print(json.dumps({"worker": worker_id, "job": job["id"], "status": "DONE"}, flush=True))
                    except Exception as exc:
                        q.fail(job["id"], f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-1000:]}", token=token)
                        print(json.dumps({"worker": worker_id, "job": job["id"], "status": "FAILED", "error": str(exc)}, flush=True))
                    if args.once:
                        return 0
            finally:
                q.close()

        budget = None
        max_replans = 1
        workers = 0
        if args.command == "ship":
            budget = Budget(max_calls=args.max_calls, max_tokens=args.max_tokens)
            max_replans = args.max_replans
            workers = args.workers

        client = NineRouterClient("http://127.0.0.1:20128", load_9router_key(), timeout=15, attempts=3, budget=budget)
        orchestrator = Orchestrator(store, client, max_workers=workers if args.command == "ship" else 2, budget=budget, max_replans=max_replans)

        if args.command == "run":
            print(json.dumps(orchestrator.run(args.project_id, until=args.until).__dict__, indent=2))
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
            print(json.dumps(orchestrator.merge(args.project_id, push=args.push, remote=args.remote, require_approval=args.require_approval or args.push), indent=2, ensure_ascii=False))
            return 0
        if args.command == "ship":
            repo = str(Path(args.repo).resolve())
            if not Path(repo).is_dir():
                raise SystemExit(f"repo does not exist: {repo}")
            pid = args.project_id or store.create_project(args.name, repo, args.goal)
            result = orchestrator.ship(
                pid,
                verify_command=args.verify,
                merge=args.merge or args.push,
                push=args.push,
                remote=args.remote,
                require_approval=args.require_approval or args.push,
            )
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
    except (RouterError, GateError, WorkspaceError, TaskSpecError, BudgetExceeded, SecretScanError, CancelledError, QueueError, ApprovalError) as exc:
        note = notify_failure(f"ma {args.command} FAILED: {type(exc).__name__}: {exc}")
        print(f"MODEL FAILURE: {exc}")
        print(json.dumps({"notify": note}, ensure_ascii=False))
        return 2
    finally:
        store.close()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
