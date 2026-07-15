from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "generate_image.py"
PNG_BYTES = b"\x89PNG\r\n\x1a\nmock-image"


class MockCoderAPIHandler(BaseHTTPRequestHandler):
    requests: list[dict] = []
    base_url = ""
    response_mode = "b64"

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers["Content-Length"])
        payload = json.loads(self.rfile.read(length))
        self.__class__.requests.append(payload)
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

    def run_skill(self, *args: str, with_key: bool = True) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["CODER_API_BASE_URL"] = MockCoderAPIHandler.base_url + "/v1"
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

    def test_gpt_image_writes_base64_response(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_skill(
                "--model",
                "gpt-image-2",
                "--size",
                "1536x1024",
                "--prompt",
                "a neon city",
                "--output-dir",
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
            result = self.run_skill(
                "--model",
                "gemini-3.1-flash-image-1k",
                "--aspect-ratio",
                "16:9",
                "--prompt",
                "a mountain lake",
                "--output-dir",
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
            result = self.run_skill(
                "--prompt",
                "a square icon",
                "--output-dir",
                directory,
                "--output",
                "icon",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)
            self.assertTrue(output["file"].endswith("icon.png"))
            self.assertEqual(Path(output["file"]).read_bytes(), PNG_BYTES)

    def test_missing_key_fails_before_a_request(self) -> None:
        result = self.run_skill("--prompt", "a cat", with_key=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("CODER_API_KEY is not set", result.stderr)
        self.assertEqual(MockCoderAPIHandler.requests, [])


if __name__ == "__main__":
    unittest.main()
