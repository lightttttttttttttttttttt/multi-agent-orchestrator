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
from ma.tasks import parse_task_dag, enforce_allowed_files, TaskSpecError
from ma.orchestrator import Orchestrator


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
