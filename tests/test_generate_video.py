from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "generate_video.py"
sys.path.insert(0, str(ROOT / "scripts"))
import generate_video as video


PNG_BYTES = b"\x89PNG\r\n\x1a\nmock-image"
MP4_BYTES = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64


class MockVideoAPI(BaseHTTPRequestHandler):
    submitted: list[dict] = []
    auth_headers: list[str] = []
    queries = 0
    create_status = 200
    query_status = 200
    content_status = 200
    task_status = "completed"

    def log_message(self, format: str, *args: object) -> None:
        return

    def respond(self, status: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path != "/v1/videos":
            self.send_error(404)
            return
        self.__class__.auth_headers.append(self.headers.get("Authorization", ""))
        body = self.rfile.read(int(self.headers["Content-Length"]))
        content_type = self.headers.get("Content-Type", "")
        if content_type.startswith("multipart/form-data"):
            multipart = BytesParser(policy=policy.default).parsebytes(
                f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("ascii") + body
            )
            payload = {}
            for part in multipart.iter_parts():
                name = part.get_param("name", header="content-disposition")
                payload[name] = part.get_payload(decode=True) if name == "image" else part.get_payload(decode=True).decode()
        else:
            payload = json.loads(body)
        self.__class__.submitted.append(payload)
        if self.__class__.create_status != 200:
            self.respond(self.__class__.create_status, b'{"error":{"message":"upstream busy"}}')
            return
        self.respond(200, b'{"id":"video-task-1","status":"queued"}')

    def do_GET(self) -> None:
        self.__class__.auth_headers.append(self.headers.get("Authorization", ""))
        if self.path == "/v1/videos/video-task-1":
            self.__class__.queries += 1
            if self.__class__.query_status != 200:
                self.respond(self.__class__.query_status, b'{"error":{"message":"try again"}}')
                return
            self.respond(200, json.dumps({"id": "video-task-1", "status": self.__class__.task_status}).encode())
        elif self.path == "/v1/videos/video-task-1/content":
            if self.__class__.content_status == 302:
                self.send_response(302)
                self.send_header("Location", "/should-not-visit")
                self.end_headers()
            elif self.__class__.content_status != 200:
                self.respond(self.__class__.content_status, b'{"error":{"message":"media unavailable"}}')
            else:
                self.respond(200, MP4_BYTES, "video/mp4")
        elif self.path == "/should-not-visit":
            self.respond(200, MP4_BYTES, "video/mp4")
        else:
            self.send_error(404)


class GenerateVideoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), MockVideoAPI)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.thread.join()
        cls.server.server_close()

    def setUp(self) -> None:
        MockVideoAPI.submitted = []
        MockVideoAPI.auth_headers = []
        MockVideoAPI.queries = 0
        MockVideoAPI.create_status = 200
        MockVideoAPI.query_status = 200
        MockVideoAPI.content_status = 200
        MockVideoAPI.task_status = "completed"

    def run_skill(self, *args: str, with_key: bool = True, config_path: Path | None = None):
        environment = os.environ.copy()
        environment["CODER_API_BASE_URL"] = f"http://127.0.0.1:{self.server.server_port}/v1"
        environment["CODER_API_CONFIG_PATH"] = str(config_path or ROOT / "tests" / "missing-credentials.json")
        if with_key:
            environment["CODER_API_KEY"] = "test-video-key"
        else:
            environment.pop("CODER_API_KEY", None)
        return subprocess.run([sys.executable, str(SCRIPT), *args], env=environment, cwd=ROOT, text=True, capture_output=True, check=False)

    def ready(self, model: str = "grok-imagine-video-1.5", image: str | None = None, prompt: str = "waves on a pier", **kwargs: str) -> str:
        args = ["--begin", "--prompt", prompt]
        if image:
            args += ["--image", image]
        begun = self.run_skill(*args)
        self.assertEqual(begun.returncode, 0, begun.stderr)
        path = json.loads(begun.stdout)["state"]
        configured = self.run_skill(
            "--select-configuration", "--state", path, "--model", model, "--seconds", kwargs.get("seconds", "5"),
            "--resolution", kwargs.get("resolution", "480p"), *( ["--aspect-ratio", kwargs["aspect_ratio"]] if "aspect_ratio" in kwargs else []),
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)
        self.assertEqual(json.loads(configured.stdout)["status"], "ready")
        return path

    def test_catalog_and_begin_require_explicit_settings(self) -> None:
        catalog = self.run_skill("--list-models")
        self.assertEqual(catalog.returncode, 0)
        self.assertEqual(json.loads(catalog.stdout)["models"]["grok-imagine-video"], ["480p", "720p"])
        self.assertEqual(json.loads(catalog.stdout)["models"]["seedance-2.0"], ["480p", "720p"])
        self.assertEqual(json.loads(catalog.stdout)["models"]["seedance-2.0-fast"], ["480p", "720p"])
        self.assertEqual(json.loads(catalog.stdout)["models"]["seedance-2.0-mini"], ["480p", "720p"])
        self.assertEqual(json.loads(catalog.stdout)["models"]["seedance-2.5"], ["480p", "720p", "1080p"])
        self.assertEqual(json.loads(catalog.stdout)["model_profiles"]["seedance-2.5"]["image_input"], "public_url")
        begun = self.run_skill("--begin", "--prompt", "light in a bottle")
        state = json.loads(begun.stdout)
        self.assertEqual(state["status"], "model_selection")
        self.assertEqual(stat.S_IMODE(Path(state["state"]).stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(Path(state["state"]).parent.stat().st_mode), 0o700)
        rejected = self.run_skill("--select-configuration", "--state", state["state"], "--model", "grok-imagine-video", "--seconds", "5", "--resolution", "1080p")
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("unsupported resolution", rejected.stderr)
        video.remove_state(Path(state["state"]))

    def test_zhiqi_models_use_duration_and_public_image_url(self) -> None:
        image_url = "https://images.example/reference.png"
        begun = self.run_skill("--begin", "--prompt", "Animate the reference image", "--image-url", image_url)
        self.assertEqual(begun.returncode, 0, begun.stderr)
        state_path = json.loads(begun.stdout)["state"]
        configured = self.run_skill(
            "--select-configuration", "--state", state_path, "--model", "seedance-2.5",
            "--seconds", "8", "--resolution", "1080p", "--aspect-ratio", "16:9", "--generate-audio",
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)
        submitted = self.run_skill("--submit", "--state", state_path)
        self.assertEqual(submitted.returncode, 0, submitted.stderr)
        self.assertEqual(MockVideoAPI.submitted[0], {
            "model": "seedance-2.5",
            "duration": 8,
            "resolution": "1080p",
            "input_reference": image_url,
            "prompt": "Animate the reference image",
            "aspect_ratio": "16:9",
            "generate_audio": True,
        })
        video.remove_state(Path(state_path))

    def test_zhiqi_resolution_and_local_image_rules_are_model_specific(self) -> None:
        begun = self.run_skill("--begin", "--prompt", "sunrise")
        state_path = json.loads(begun.stdout)["state"]
        rejected = self.run_skill(
            "--select-configuration", "--state", state_path, "--model", "seedance-2.0",
            "--seconds", "5", "--resolution", "1080p",
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("unsupported resolution", rejected.stderr)
        video.remove_state(Path(state_path))

        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "input.png"
            image.write_bytes(PNG_BYTES)
            begun = self.run_skill("--begin", "--prompt", "animate this", "--image", str(image))
            state_path = json.loads(begun.stdout)["state"]
            rejected = self.run_skill(
                "--select-configuration", "--state", state_path, "--model", "seedance-2.5",
                "--seconds", "5", "--resolution", "720p",
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("requires --image-url", rejected.stderr)
            video.remove_state(Path(state_path))

    def test_public_image_url_is_validated_before_workflow_creation(self) -> None:
        result = self.run_skill("--begin", "--prompt", "animate", "--image-url", "file:///tmp/image.png")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("public HTTP(S) URL", result.stderr)

    def test_create_poll_and_download_without_repeat_post(self) -> None:
        path = self.ready(resolution="1080p", aspect_ratio="9:16")
        submitted = self.run_skill("--submit", "--state", path)
        self.assertEqual(submitted.returncode, 0, submitted.stderr)
        self.assertEqual(json.loads(submitted.stdout)["task_id"], "video-task-1")
        self.assertEqual(MockVideoAPI.submitted, [{"model": "grok-imagine-video-1.5", "seconds": 5, "resolution": "1080p", "prompt": "waves on a pier", "aspect_ratio": "9:16"}])
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_skill("--poll", "--state", path, "--output-dir", directory, "--output", "test.mp4", "--max-wait", "0")
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            self.assertEqual(Path(data["file"]).read_bytes(), MP4_BYTES)
            self.assertEqual(data["model"], "grok-imagine-video-1.5")
        self.assertEqual(len(MockVideoAPI.submitted), 1)
        self.assertFalse(Path(path).exists())
        self.assertEqual(MockVideoAPI.auth_headers, ["Bearer test-video-key"] * 3)

    def test_pending_poll_can_resume_without_posting_again(self) -> None:
        path = self.ready(model="grok-imagine-video")
        self.assertEqual(self.run_skill("--submit", "--state", path).returncode, 0)
        MockVideoAPI.task_status = "queued"
        pending = self.run_skill("--poll", "--state", path, "--max-wait", "0")
        self.assertEqual(json.loads(pending.stdout)["status"], "in_progress")
        MockVideoAPI.query_status = 503
        failed_query = self.run_skill("--poll", "--state", path, "--max-wait", "0")
        self.assertEqual(failed_query.returncode, 1)
        self.assertEqual(json.loads(Path(path).read_text())["task_id"], "video-task-1")
        MockVideoAPI.query_status = 200
        MockVideoAPI.task_status = "completed"
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_skill("--poll", "--state", path, "--max-wait", "0", "--output-dir", directory)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(Path(json.loads(result.stdout)["file"]).exists())
        self.assertEqual(len(MockVideoAPI.submitted), 1)

    def test_transient_submit_failure_does_not_retry_a_billable_post(self) -> None:
        path = self.ready()
        MockVideoAPI.create_status = 503
        result = self.run_skill("--submit", "--state", path)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(Path(path).read_text())["status"], "submission_uncertain")
        self.assertEqual(self.run_skill("--submit", "--state", path).returncode, 1)
        self.assertEqual(len(MockVideoAPI.submitted), 1)
        attached = self.run_skill("--attach-task-id", "--state", path, "--task-id", "video-task-1")
        self.assertEqual(json.loads(attached.stdout)["status"], "submitted")
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_skill("--poll", "--state", path, "--output-dir", directory)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(MockVideoAPI.submitted), 1)

    def test_definite_client_error_keeps_configuration_without_automatic_retry(self) -> None:
        path = self.ready()
        MockVideoAPI.create_status = 400
        result = self.run_skill("--submit", "--state", path)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(Path(path).read_text())["status"], "ready")
        self.assertEqual(len(MockVideoAPI.submitted), 1)
        video.remove_state(Path(path))

    def test_image_to_video_uses_multipart_and_preserves_source_ratio(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "input.png"
            image.write_bytes(PNG_BYTES)
            path = self.ready(image=str(image), prompt="", resolution="720p")
            result = self.run_skill("--submit", "--state", path)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = MockVideoAPI.submitted[0]
            self.assertEqual(payload["image"], PNG_BYTES)
            self.assertEqual(payload["model"], "grok-imagine-video-1.5")
            self.assertEqual(payload["seconds"], "5")
            self.assertNotIn("aspect_ratio", payload)
            video.remove_state(Path(path))

    def test_invalid_or_oversized_reference_is_rejected_before_a_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bad = Path(directory) / "bad.png"
            bad.write_bytes(b"not an image")
            result = self.run_skill("--begin", "--image", str(bad))
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(MockVideoAPI.submitted)
            big = Path(directory) / "big.png"
            with big.open("wb") as file:
                file.write(PNG_BYTES)
                file.truncate(video.MAX_REFERENCE_BYTES + 1)
            result = self.run_skill("--begin", "--image", str(big))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("at most 20 MiB", result.stderr)

    def test_download_failure_keeps_task_and_never_reposts(self) -> None:
        path = self.ready()
        self.run_skill("--submit", "--state", path)
        MockVideoAPI.content_status = 503
        with tempfile.TemporaryDirectory() as directory:
            failed = self.run_skill("--poll", "--state", path, "--output-dir", directory)
            self.assertEqual(failed.returncode, 1)
            self.assertEqual(json.loads(Path(path).read_text())["status"], "completed")
            self.assertEqual(list(Path(directory).iterdir()), [])
            MockVideoAPI.content_status = 200
            retried = self.run_skill("--poll", "--state", path, "--output-dir", directory)
            self.assertEqual(retried.returncode, 0, retried.stderr)
            self.assertEqual(len(MockVideoAPI.submitted), 1)

    def test_redirected_video_content_is_not_followed_with_bearer_token(self) -> None:
        path = self.ready()
        self.run_skill("--submit", "--state", path)
        MockVideoAPI.content_status = 302
        with tempfile.TemporaryDirectory() as directory:
            failed = self.run_skill("--poll", "--state", path, "--output-dir", directory)
        self.assertEqual(failed.returncode, 1)
        self.assertIn("HTTP 302", failed.stderr)
        self.assertEqual(len(MockVideoAPI.auth_headers), 3)
        video.remove_state(Path(path))

    def test_missing_key_does_not_submit(self) -> None:
        begun = self.run_skill("--begin", "--prompt", "sunrise", with_key=False)
        state = json.loads(begun.stdout)
        self.assertEqual(state["status"], "key_storage_decision")
        self.assertIn("security_reminder", state)
        result = self.run_skill("--submit", "--state", state["state"], with_key=False)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(MockVideoAPI.submitted)
        video.remove_state(Path(state["state"]))

    def test_video_key_storage_uses_shared_private_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            credentials = Path(directory) / "config" / "credentials.json"
            begun = self.run_skill("--begin", "--prompt", "sunrise", with_key=False, config_path=credentials)
            state_path = json.loads(begun.stdout)["state"]
            saved = self.run_skill("--save-local-key", "--state", state_path, "--api-key", "chat-video-key", with_key=False, config_path=credentials)
            self.assertEqual(saved.returncode, 0, saved.stderr)
            self.assertEqual(json.loads(saved.stdout)["status"], "model_selection")
            self.assertIn("security_reminder", json.loads(saved.stdout))
            self.assertEqual(json.loads(credentials.read_text())["api_key"], "chat-video-key")
            self.assertEqual(stat.S_IMODE(credentials.stat().st_mode), 0o600)
            self.assertNotIn("chat-video-key", Path(state_path).read_text())
            video.remove_state(Path(state_path))

    def test_video_submit_rejects_free_form_model_overrides(self) -> None:
        path = self.ready()
        attempt = self.run_skill("--submit", "--state", path, "--model", "grok-imagine-video")
        self.assertEqual(attempt.returncode, 1)
        self.assertIn("only with --select-configuration", attempt.stderr)
        self.assertFalse(MockVideoAPI.submitted)
        video.remove_state(Path(path))

    def test_existing_output_is_not_removed_on_exclusive_create_collision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            occupied = Path(directory) / "existing.mp4"
            occupied.write_bytes(b"user's existing video")
            base_url = f"http://127.0.0.1:{self.server.server_port}/v1"
            with patch.object(video, "unique_output_path", return_value=occupied):
                with self.assertRaises(video.SkillError):
                    video.download_video(base_url + "/videos/video-task-1/content", "test-video-key", 10, Path(directory), "existing.mp4")
            self.assertEqual(occupied.read_bytes(), b"user's existing video")

    def test_failed_video_stays_failed_and_cannot_resubmit(self) -> None:
        path = self.ready()
        self.run_skill("--submit", "--state", path)
        MockVideoAPI.task_status = "failed"
        result = self.run_skill("--poll", "--state", path, "--max-wait", "0")
        self.assertEqual(json.loads(result.stdout)["status"], "failed")
        self.assertEqual(self.run_skill("--submit", "--state", path).returncode, 1)
        self.assertEqual(len(MockVideoAPI.submitted), 1)
        video.remove_state(Path(path))


if __name__ == "__main__":
    unittest.main()
