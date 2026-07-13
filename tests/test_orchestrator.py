import json
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))

from ma.router import EmptyResponseError, NineRouterClient, RouterError
from ma.store import TaskStore
from ma.gates import GateError, require_model_content, require_command_success, require_approve
from ma.workspace import Workspace, extract_diff
from ma.tasks import parse_task_dag, enforce_allowed_files, TaskSpecError, ready_waves
from ma.orchestrator import Orchestrator
from ma.locks import FileLockManager, FileLockError, Budget, BudgetExceeded
import subprocess


class FakeResponse:
    status = 200
    headers = {"content-type": "application/json"}

    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body.encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class RouterTests(unittest.TestCase):
    def test_extracts_chat_content(self):
        body = json.dumps({"choices": [{"message": {"content": "PLAN"}}]})
        with patch("urllib.request.urlopen", return_value=FakeResponse(body)):
            result = NineRouterClient("http://x", "secret").call("model", "prompt")
        self.assertEqual(result.content, "PLAN")

    def test_rejects_http_200_with_empty_content(self):
        body = json.dumps({"choices": [{"message": {"content": ""}}]})
        with patch("urllib.request.urlopen", return_value=FakeResponse(body)):
            with self.assertRaises(EmptyResponseError):
                NineRouterClient("http://x", "secret").call("dead", "prompt")

    def test_extracts_responses_output(self):
        body = json.dumps(
            {"output": [{"type": "message", "content": [{"type": "output_text", "text": "AUDIT"}]}]}
        )
        with patch("urllib.request.urlopen", return_value=FakeResponse(body)):
            result = NineRouterClient("http://x", "secret").call("model", "prompt", api="responses")
        self.assertEqual(result.content, "AUDIT")

    def test_retries_timeout_three_times_then_reports_failure(self):
        with patch("urllib.request.urlopen", side_effect=socket.timeout("late")) as call:
            with self.assertRaisesRegex(Exception, "failed after 3 attempts"):
                NineRouterClient("http://x", "secret", timeout=15, attempts=3).call("slow", "prompt")
        self.assertEqual(call.call_count, 3)

    def test_retry_can_recover_on_third_attempt(self):
        body = json.dumps({"choices": [{"message": {"content": "RECOVERED"}}]})
        with patch(
            "urllib.request.urlopen",
            side_effect=[socket.timeout(), socket.timeout(), FakeResponse(body)],
        ) as call:
            result = NineRouterClient("http://x", "secret", timeout=15, attempts=3).call("slow", "prompt")
        self.assertEqual(result.content, "RECOVERED")
        self.assertEqual(call.call_count, 3)


class StoreTests(unittest.TestCase):
    def test_persists_project_and_resumes_next_stage(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "state.sqlite"
            s = TaskStore(db)
            pid = s.create_project("demo", str(Path(d)), "build thing")
            s.record_stage(pid, "design", "sol", "plan", "artifact")
            s.close()
            s2 = TaskStore(db)
            self.assertEqual(s2.get_project(pid)["status"], "DESIGN")
            self.assertEqual(s2.next_stage(pid), "critique")
            s2.close()

    def test_invalid_transition_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            s = TaskStore(Path(d) / "state.sqlite")
            pid = s.create_project("demo", d, "goal")
            with self.assertRaises(ValueError):
                s.record_stage(pid, "implementation", "worker", "x", "y")
            s.close()

    def test_task_dag_persistence(self):
        with tempfile.TemporaryDirectory() as d:
            s = TaskStore(Path(d) / "state.sqlite")
            pid = s.create_project("demo", d, "goal")
            s.replace_tasks(
                pid,
                [
                    {
                        "id": "T1",
                        "goal": "add subtract",
                        "allowed_files": ["calc.py"],
                        "verify_command": "python -m unittest -v",
                        "depends_on": [],
                    }
                ],
            )
            tasks = s.list_tasks(pid)
            self.assertEqual(tasks[0]["id"], "T1")
            self.assertEqual(tasks[0]["allowed_files"], ["calc.py"])
            s.close()


class WorkspaceTests(unittest.TestCase):
    def test_extract_diff_from_fenced_model_output(self):
        output = "Here it is:\n```diff\ndiff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n```"
        self.assertTrue(extract_diff(output).startswith("diff --git"))

    def test_worktree_applies_patch_and_collects_diff(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@local"], cwd=repo, check=True)
            (repo / "a.py").write_text("old\n")
            subprocess.run(["git", "add", "a.py"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
            ws = Workspace(repo, "p123")
            path = ws.create()
            ws.apply_patch(
                "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n"
            )
            self.assertIn("+new", ws.diff())
            ws.remove()
            self.assertFalse(path.exists())


class TaskTests(unittest.TestCase):
    def test_parse_task_dag(self):
        text = """
APPROVED PLAN
```json
[
  {
    "id": "T1",
    "goal": "add subtract",
    "allowed_files": ["calc.py"],
    "verify_command": "python -m unittest -v",
    "depends_on": []
  }
]
```
"""
        tasks = parse_task_dag(text)
        self.assertEqual(tasks[0].id, "T1")
        self.assertEqual(tasks[0].allowed_files, ["calc.py"])

    def test_enforce_allowed_files_rejects_out_of_scope(self):
        diff = "diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@\n+x\ndiff --git a/secret.env b/secret.env\n--- a/secret.env\n+++ b/secret.env\n@@\n+y\n"
        with self.assertRaises(TaskSpecError):
            enforce_allowed_files(diff, ["calc.py"])

    def test_ready_waves_respects_deps_and_file_locks(self):
        tasks = [
            {"id": "A", "allowed_files": ["a.py"], "depends_on": []},
            {"id": "B", "allowed_files": ["b.py"], "depends_on": []},
            {"id": "C", "allowed_files": ["c.py"], "depends_on": ["A", "B"]},
            {"id": "D", "allowed_files": ["a.py"], "depends_on": []},  # conflicts with A
        ]
        waves = ready_waves(tasks)
        # first wave can run A and B (no file overlap); D conflicts with A so later
        first_ids = {t["id"] for t in waves[0]}
        self.assertIn("A", first_ids)
        self.assertIn("B", first_ids)
        self.assertNotIn("C", first_ids)
        # C only after A and B done
        flat = [t["id"] for w in waves for t in w]
        self.assertLess(flat.index("A"), flat.index("C"))
        self.assertLess(flat.index("B"), flat.index("C"))


class LockTests(unittest.TestCase):
    def test_file_lock_conflict(self):
        with tempfile.TemporaryDirectory() as d:
            lm = FileLockManager(Path(d) / "locks.sqlite")
            lm.acquire("T1", ["a.py", "b.py"])
            with self.assertRaises(FileLockError):
                lm.acquire("T2", ["b.py"])
            lm.release("T1")
            lm.acquire("T2", ["b.py"])
            lm.release("T2")
            lm.close()

    def test_cross_process_lock_via_shared_db(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "locks.sqlite"
            a = FileLockManager(db)
            b = FileLockManager(db)
            a.acquire("T1", ["shared.py"], project_id="p1")
            with self.assertRaises(FileLockError):
                b.acquire("T2", ["shared.py"], project_id="p2")
            a.release("T1")
            b.acquire("T2", ["shared.py"], project_id="p2")
            b.release("T2")
            a.close()
            b.close()


class BudgetTests(unittest.TestCase):
    def test_budget_blocks_extra_calls(self):
        b = Budget(max_calls=1, max_tokens=10000)
        b.charge(prompt="hi", content="ok", model="m")
        with self.assertRaises(BudgetExceeded):
            b.charge(prompt="again", content="x", model="m")

    def test_budget_blocks_tokens(self):
        b = Budget(max_tokens=5)
        with self.assertRaises(BudgetExceeded):
            b.charge(prompt="x" * 100, content="y" * 100, model="m")


class SecretTests(unittest.TestCase):
    def test_secret_scan_blocks_openai_key_in_diff(self):
        from ma.secrets import require_no_secrets, SecretScanError

        fake = "sk-" + ("a" * 32)
        bad = f"diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@\n+key = '{fake}'\n"
        with self.assertRaises(SecretScanError):
            require_no_secrets(bad)

    def test_secret_scan_allows_clean_diff(self):
        from ma.secrets import require_no_secrets

        good = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@\n+def ok():\n+    return 1\n"
        self.assertIn("def ok", require_no_secrets(good))


class UsageTests(unittest.TestCase):
    def test_usage_ledger_records_and_summarizes(self):
        from ma.usage import UsageLedger

        with tempfile.TemporaryDirectory() as d:
            led = UsageLedger(Path(d) / "usage.sqlite")
            led.record(model="nttcodex/deepseek-v4-pro", prompt="hi" * 20, content="ok" * 10, project_id="p1")
            led.record(model="Ntt_Codex10tr/gpt-5.6-sol", prompt="plan" * 30, content="done" * 20, project_id="p1")
            s = led.summary("p1")
            self.assertEqual(s["total_calls"], 2)
            self.assertGreater(s["total_cost_usd"], 0)
            led.close()


class OpsTests(unittest.TestCase):
    def test_workers_for_wave_autoscale(self):
        from ma.ops import workers_for_wave

        self.assertEqual(workers_for_wave(1, 0), 1)
        self.assertEqual(workers_for_wave(3, 0), 3)
        self.assertEqual(workers_for_wave(10, 0), 4)
        self.assertEqual(workers_for_wave(10, 2), 2)

    def test_clean_removes_worktree(self):
        from ma.ops import clean_project

        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "t@l"], cwd=repo, check=True)
            (repo / "a.py").write_text("x\n")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "i"], cwd=repo, check=True)
            store = TaskStore(Path(d) / "state.sqlite")
            pid = store.create_project("c", str(repo), "g")
            ws = Workspace(repo, pid, task_id="T1")
            ws.create()
            self.assertTrue(ws.path.exists())
            out = clean_project(store, pid)
            self.assertFalse(ws.path.exists())
            self.assertTrue(any("T1" in p for p in out["removed_worktrees"]))
            store.close()


class FallbackTests(unittest.TestCase):
    def test_role_fallback_uses_second_model(self):
        class Client:
            def __init__(self):
                self.calls = []

            def call(self, model, prompt, system=""):
                self.calls.append(model)
                if model == "primary":
                    raise RouterError("primary dead")
                return type("R", (), {"content": "OK", "latency_ms": 1, "raw": {}})()

        with tempfile.TemporaryDirectory() as d:
            store = TaskStore(Path(d) / "state.sqlite")
            pid = store.create_project("demo", d, "goal")
            client = Client()
            orch = Orchestrator(
                store,
                client,
                models={"design": "primary"},
                fallbacks={"design": ["primary", "backup"]},
            )
            orch.run_stage(pid, "design")
            self.assertEqual(client.calls, ["primary", "backup"])
            self.assertEqual(store.get_artifact(pid, "design"), "OK")
            self.assertIn("primary", orch._dead_models)
            # second call should skip blacklisted primary
            client.calls.clear()
            store.record_stage = store.record_stage  # keep
            # force next stage critique using same client fallbacks
            orch.models["critique"] = "primary"
            orch.fallbacks["critique"] = ["primary", "backup"]
            orch.run_stage(pid, "critique")
            self.assertEqual(client.calls, ["backup"])
            store.close()

    def test_export_report(self):
        from ma.report import export_markdown_report

        with tempfile.TemporaryDirectory() as d:
            store = TaskStore(Path(d) / "state.sqlite")
            pid = store.create_project("demo", d, "goal")
            store.record_stage(pid, "design", "m", "p", "design artifact")
            path = export_markdown_report(store, pid, {"total_calls": 1, "total_cost_usd": 0.01, "by_model": []}, out_dir=Path(d) / "reports")
            self.assertTrue(path.exists())
            text = path.read_text(encoding="utf-8")
            self.assertIn(pid, text)
            self.assertIn("design artifact", text)
            store.close()


class GateTests(unittest.TestCase):
    def test_model_content_gate_rejects_whitespace(self):
        with self.assertRaises(GateError):
            require_model_content("  \n")

    def test_command_gate_requires_zero_exit(self):
        with self.assertRaises(GateError):
            require_command_success({"exit_code": 1})

    def test_approve_gate(self):
        self.assertTrue(require_approve("APPROVE\nok").startswith("APPROVE"))
        with self.assertRaises(GateError):
            require_approve("REJECT no")


if __name__ == "__main__":
    unittest.main()
