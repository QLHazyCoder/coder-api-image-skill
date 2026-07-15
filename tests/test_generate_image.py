from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "generate_image.py"
PNG_BYTES = b"\x89PNG\r\n\x1a\nmock-image"
sys.path.insert(0, str(ROOT / "scripts"))
import generate_image as generator


class MockCoderAPIHandler(BaseHTTPRequestHandler):
    requests: list[dict] = []
    base_url = ""
    response_mode = "b64"
    failures_remaining = 0
    failure_status = 503

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers["Content-Length"])
        payload = json.loads(self.rfile.read(length))
        self.__class__.requests.append(payload)
        if self.__class__.failures_remaining:
            self.__class__.failures_remaining -= 1
            body = json.dumps({"error": {"message": "temporary upstream failure"}}).encode("utf-8")
            self.send_response(self.__class__.failure_status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        image_data = {"mime_type": "image/png", "revised_prompt": "mock revised prompt"}
        if self.__class__.response_mode == "url":
            image_data["url"] = f"{self.__class__.base_url}/generated.png"
        else:
            image_data["b64_json"] = base64.b64encode(PNG_BYTES).decode("ascii")
        body = json.dumps({"created": 1, "data": [image_data]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path != "/generated.png":
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(PNG_BYTES)))
        self.end_headers()
        self.wfile.write(PNG_BYTES)


class GenerateImageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), MockCoderAPIHandler)
        MockCoderAPIHandler.base_url = f"http://127.0.0.1:{cls.server.server_port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.thread.join()
        cls.server.server_close()

    def setUp(self) -> None:
        MockCoderAPIHandler.requests = []
        MockCoderAPIHandler.response_mode = "b64"
        MockCoderAPIHandler.failures_remaining = 0

    def run_skill(self, *args: str, with_key: bool = True) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["CODER_API_BASE_URL"] = MockCoderAPIHandler.base_url + "/v1"
        env["CODER_API_CONFIG_PATH"] = str(ROOT / "tests" / "missing-credentials.json")
        if with_key:
            env["CODER_API_KEY"] = "test-key"
        else:
            env.pop("CODER_API_KEY", None)
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=ROOT,
            env=env,
            check=False,
            text=True,
            capture_output=True,
        )

    def prepare_ready_workflow(
        self,
        prompt: str,
        model: str,
        layout_option: str,
    ) -> str:
        begin = self.run_skill("--begin", "--prompt", prompt)
        self.assertEqual(begin.returncode, 0, begin.stderr)
        state_path = json.loads(begin.stdout)["state"]

        select_model = self.run_skill("--select-model", "--state", state_path, "--model", model)
        self.assertEqual(select_model.returncode, 0, select_model.stderr)

        layout_argument = "--size" if model == "gpt-image-2" else "--aspect-ratio"
        select_layout = self.run_skill("--select-layout", "--state", state_path, layout_argument, layout_option)
        self.assertEqual(select_layout.returncode, 0, select_layout.stderr)
        return state_path

    def run_workflow(
        self,
        prompt: str,
        model: str,
        layout_option: str,
        output_dir: str,
        output: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        state_path = self.prepare_ready_workflow(prompt, model, layout_option)
        command = ["--generate", "--state", state_path, "--output-dir", output_dir]
        if output:
            command.extend(["--output", output])
        return self.run_skill(*command)

    def test_gpt_image_writes_base64_response(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_workflow(
                "a neon city",
                "gpt-image-2",
                "1536x1024",
                directory,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(MockCoderAPIHandler.requests[0]["model"], "gpt-image-2")
            self.assertEqual(MockCoderAPIHandler.requests[0]["size"], "1536x1024")
            self.assertEqual(MockCoderAPIHandler.requests[0]["response_format"], "b64_json")
            output = json.loads(result.stdout)
            self.assertEqual(Path(output["file"]).read_bytes(), PNG_BYTES)
            self.assertEqual(output["mime_type"], "image/png")

    def test_gemini_model_name_and_resolution_suffix_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_workflow(
                "a mountain lake",
                "gemini-3.1-flash-image-1k",
                "16:9",
                directory,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = MockCoderAPIHandler.requests[0]
            self.assertEqual(payload["model"], "gemini-3.1-flash-image-1k")
            self.assertEqual(payload["aspect_ratio"], "16:9")
            self.assertNotIn("resolution", payload)
            self.assertNotIn("size", payload)

    def test_url_response_is_downloaded(self) -> None:
        MockCoderAPIHandler.response_mode = "url"
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_workflow(
                "a square icon",
                "gpt-image-2",
                "1024x1024",
                directory,
                "icon",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)
            self.assertTrue(output["file"].endswith("icon.png"))
            self.assertEqual(Path(output["file"]).read_bytes(), PNG_BYTES)

    def test_missing_key_stops_at_key_storage_decision_before_a_request(self) -> None:
        result = self.run_skill("--begin", "--prompt", "a cat", with_key=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        workflow = json.loads(result.stdout)
        self.assertEqual(workflow["status"], "key_storage_decision")
        self.assertIn("security_reminder", workflow)
        generate = self.run_skill("--generate", "--state", workflow["state"], with_key=False)
        self.assertEqual(generate.returncode, 1)
        self.assertIn("workflow is not ready", generate.stderr)
        generator.remove_workflow_state(Path(workflow["state"]))
        self.assertEqual(MockCoderAPIHandler.requests, [])

    def test_workflow_state_uses_a_private_directory_without_changing_temp_root(self) -> None:
        temporary_root = Path(tempfile.gettempdir())
        original_mode = stat.S_IMODE(temporary_root.stat().st_mode)
        state_path = generator.create_workflow_state("a cat")
        try:
            self.assertEqual(stat.S_IMODE(state_path.parent.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(temporary_root.stat().st_mode), original_mode)
        finally:
            generator.remove_workflow_state(state_path)

    def test_direct_prompt_cannot_use_an_implicit_model_or_layout(self) -> None:
        result = self.run_skill("--prompt", "a cat")
        self.assertEqual(result.returncode, 1)
        self.assertIn("choose exactly one operation", result.stderr)
        self.assertEqual(MockCoderAPIHandler.requests, [])

    def test_transient_failures_retry_up_to_three_attempts(self) -> None:
        MockCoderAPIHandler.failures_remaining = 2
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_workflow("a neon city", "gpt-image-2", "1024x1024", directory)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(MockCoderAPIHandler.requests), 3)

    def test_three_failures_require_user_confirmation_before_another_round(self) -> None:
        MockCoderAPIHandler.failures_remaining = 3
        state_path = self.prepare_ready_workflow("a neon city", "gpt-image-2", "1024x1024")
        exhausted = self.run_skill("--generate", "--state", state_path)
        self.assertEqual(exhausted.returncode, 0, exhausted.stderr)
        workflow = json.loads(exhausted.stdout)
        self.assertEqual(workflow["status"], "retry_exhausted")
        self.assertEqual(workflow["attempts"], 3)
        self.assertEqual(workflow["action"], "ask_user_to_continue_retrying")
        self.assertEqual(len(MockCoderAPIHandler.requests), 3)

        with tempfile.TemporaryDirectory() as directory:
            continued = self.run_skill("--continue-retry", "--state", state_path)
            self.assertEqual(continued.returncode, 0, continued.stderr)
            self.assertEqual(json.loads(continued.stdout)["status"], "ready")
            success = self.run_skill("--generate", "--state", state_path, "--output-dir", directory)
        self.assertEqual(success.returncode, 0, success.stderr)
        self.assertEqual(len(MockCoderAPIHandler.requests), 4)
        self.assertFalse(Path(state_path).exists())

    def test_timeout_cannot_exceed_two_minutes(self) -> None:
        result = self.run_skill("--begin", "--prompt", "a cat", "--timeout", "121")
        self.assertEqual(result.returncode, 1)
        self.assertIn("between 1 and 120 seconds", result.stderr)

    def test_configure_stores_key_with_private_permissions_without_network_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "private" / "credentials.json"
            output = io.StringIO()
            with patch("generate_image.getpass.getpass", return_value="configured-test-key"), contextlib.redirect_stdout(output):
                generator.configure_api_key(config_path)
            self.assertEqual(generator.read_local_api_key(config_path), "configured-test-key")
            self.assertEqual(stat.S_IMODE(config_path.stat().st_mode), 0o600)
            self.assertIn("enable model limits", output.getvalue())

    def test_environment_key_takes_precedence_over_local_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "credentials.json"
            generator.write_local_api_key(config_path, "saved-key")
            with patch.dict(
                os.environ,
                {"CODER_API_KEY": "environment-key", "CODER_API_CONFIG_PATH": str(config_path)},
                clear=False,
            ):
                self.assertEqual(generator.read_api_key(), "environment-key")

    def test_skill_requires_key_save_offer_and_model_choice(self) -> None:
        instructions = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("ask whether they want to save it locally before generating", instructions)
        self.assertIn("`default` is a user choice, never an agent assumption", instructions)
        self.assertIn("Do not invoke the system `imagegen` skill", instructions)
        self.assertIn("If the JSON result contains `security_reminder`", instructions)
        self.assertIn("do not run the next command until the user has answered", instructions)

    def test_successful_workflow_returns_security_reminder_after_local_key_save(self) -> None:
        descriptor, state_name = tempfile.mkstemp(prefix="coder-api-image-test-", suffix=".json")
        os.close(descriptor)
        state_path = Path(state_name)
        try:
            generator.write_private_json(
                state_path,
                {
                    "version": generator.WORKFLOW_STATE_VERSION,
                    "prompt": "a square icon",
                    "status": "ready",
                    "model": "gpt-image-2",
                    "layout": {"size": "1024x1024"},
                    "local_key_saved": True,
                },
            )
            with tempfile.TemporaryDirectory() as directory:
                result = self.run_skill("--generate", "--state", str(state_path), "--output-dir", directory)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("security_reminder", json.loads(result.stdout))
            self.assertFalse(state_path.exists())
        finally:
            state_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
