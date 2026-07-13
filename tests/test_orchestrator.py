import json
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))

from ma.router import EmptyResponseError, NineRouterClient
from ma.store import TaskStore
from ma.gates import GateError, require_model_content, require_command_success


class FakeResponse:
    status = 200
    headers = {"content-type": "application/json"}
    def __init__(self, body): self.body = body
    def read(self): return self.body.encode()
    def __enter__(self): return self
    def __exit__(self, *args): return False


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
        body = json.dumps({"output": [{"type": "message", "content": [{"type": "output_text", "text": "AUDIT"}]}]})
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
        with patch("urllib.request.urlopen", side_effect=[socket.timeout(), socket.timeout(), FakeResponse(body)]) as call:
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


class GateTests(unittest.TestCase):
    def test_model_content_gate_rejects_whitespace(self):
        with self.assertRaises(GateError): require_model_content("  \n")

    def test_command_gate_requires_zero_exit(self):
        with self.assertRaises(GateError): require_command_success({"exit_code": 1})


if __name__ == "__main__":
    unittest.main()
