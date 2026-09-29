#!/usr/bin/env python3
"""Create and retrieve one Grok video through Coder API's standard video endpoint."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import secrets
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from generate_image import (
    SkillError,
    api_base_url,
    api_error_message,
    configure_api_key,
    inferred_mime_type,
    local_config_path,
    read_api_key,
    read_local_api_key,
    security_reminder,
    unique_output_path,
    validate_edit_input,
    write_private_json,
)


VIDEO_MODELS = {
    "grok-imagine-video": ["480p", "720p"],
    "grok-imagine-video-1.5": ["480p", "720p", "1080p"],
}
ASPECT_RATIOS = ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"]
VIDEO_STATE_VERSION = 1
MAX_REFERENCE_BYTES = 20 * 1024 * 1024
MAX_VIDEO_BYTES = 512 * 1024 * 1024
MAX_JSON_BYTES = 1024 * 1024
MAX_TIMEOUT = 1000
TASK_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~-]{0,255}\Z")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request: urllib.request.Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    operations = parser.add_mutually_exclusive_group(required=True)
    operations.add_argument("--list-models", action="store_true")
    operations.add_argument("--begin", action="store_true")
    operations.add_argument("--save-local-key", action="store_true")
    operations.add_argument("--select-configuration", action="store_true")
    operations.add_argument("--submit", action="store_true")
    operations.add_argument("--attach-task-id", action="store_true")
    operations.add_argument("--poll", action="store_true")
    parser.add_argument("--state", help="video workflow file returned by --begin")
    parser.add_argument("--prompt", help="video prompt (optional when an image is provided)")
    parser.add_argument("--image", help="local PNG, JPEG, or WebP reference image, at most 20 MiB")
    parser.add_argument("--model", choices=sorted(VIDEO_MODELS))
    parser.add_argument("--seconds", type=int, help="video duration in seconds (1-15)")
    parser.add_argument("--resolution", help="480p, 720p, or 1080p where supported")
    parser.add_argument("--aspect-ratio", help="optional aspect ratio; source image ratio is kept when omitted")
    audio = parser.add_mutually_exclusive_group()
    audio.add_argument("--generate-audio", dest="generate_audio", action="store_true")
    audio.add_argument("--no-generate-audio", dest="generate_audio", action="store_false")
    parser.set_defaults(generate_audio=None)
    parser.add_argument("--task-id", help="existing public video task ID for manual reconciliation")
    parser.add_argument("--api-key", help="save a key supplied by the user into the shared private config")
    parser.add_argument("--output-dir", default=".")
    parser.add_argument("--output", help="optional output filename (must end with .mp4)")
    parser.add_argument("--timeout", type=int, default=60, help="per-request timeout in seconds (1-1000)")
    parser.add_argument("--max-wait", type=int, default=120, help="maximum seconds to wait per --poll call")
    parser.add_argument("--poll-interval", type=int, default=5, help="seconds between task status queries")
    return parser.parse_args()


def validate_reference(path: str) -> Path:
    image = validate_edit_input(Path(path))
    if image.stat().st_size > MAX_REFERENCE_BYTES:
        raise SkillError("video reference image must be at most 20 MiB")
    return image


def read_state(path: Path) -> dict[str, Any]:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SkillError(f"video workflow state is missing or invalid: {error}") from error
    if not isinstance(state, dict) or state.get("version") != VIDEO_STATE_VERSION or state.get("media") != "video":
        raise SkillError("video workflow state is invalid or from an unsupported version")
    if not isinstance(state.get("status"), str) or not isinstance(state.get("prompt"), str):
        raise SkillError("video workflow state is invalid")
    return state


def begin(args: argparse.Namespace) -> tuple[Path, dict[str, Any]]:
    if not (args.prompt or "").strip() and not args.image:
        raise SkillError("--prompt or --image is required for a video")
    image = validate_reference(args.image) if args.image else None
    key_available = bool(os.environ.get("CODER_API_KEY", "").strip() or read_local_api_key(local_config_path()))
    directory = Path(tempfile.mkdtemp(prefix="coder-api-video-"))
    os.chmod(directory, 0o700)
    path = directory / "workflow.json"
    state: dict[str, Any] = {
        "version": VIDEO_STATE_VERSION,
        "media": "video",
        "prompt": (args.prompt or "").strip(),
        "status": "model_selection" if key_available else "key_storage_decision",
        "local_key_saved": False,
    }
    if image:
        state["image_path"] = str(image)
    write_private_json(path, state)
    return path, state


def state_result(path: Path, state: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"state": str(path), "status": state["status"], "media": "video"}
    status = state["status"]
    if status == "key_storage_decision":
        result.update({"action": "configure_shared_coder_api_key", "security_reminder": security_reminder()})
    elif status == "model_selection":
        result.update({"action": "ask_user_to_choose_video_model_duration_and_resolution", "models": VIDEO_MODELS, "seconds": {"min": 1, "max": 15}, "aspect_ratios": ASPECT_RATIOS})
    elif status == "ready":
        result.update({"action": "ready_to_submit", "model": state["model"], "settings": state["settings"]})
    elif status in {"submitting", "submission_uncertain"}:
        result.update({"action": "manual_reconcile_submission", "warning": "The upstream may have accepted this paid request. Do not submit it again; find its public task ID and use --attach-task-id."})
    elif status in {"submitted", "in_progress"}:
        result.update({"action": "poll_existing_task", "task_id": state["task_id"]})
    elif status == "completed":
        result.update({"action": "download_existing_task", "task_id": state["task_id"]})
    elif status in {"failed", "canceled"}:
        result.update({"action": "inspect_failed_task", "task_id": state["task_id"], "error": state.get("error", "")})
    else:
        raise SkillError("video workflow has an unsupported status")
    if state.get("local_key_saved"):
        result["security_reminder"] = security_reminder()
    return result


def select_configuration(state: dict[str, Any], args: argparse.Namespace) -> None:
    if state["status"] != "model_selection":
        raise SkillError("video model selection is not the next workflow step")
    if args.model not in VIDEO_MODELS:
        raise SkillError("choose an explicit video model from --list-models")
    if args.seconds is None or not 1 <= args.seconds <= 15:
        raise SkillError("--seconds must be an integer between 1 and 15")
    if args.resolution not in VIDEO_MODELS[args.model]:
        raise SkillError(f"unsupported resolution for {args.model}; choose {', '.join(VIDEO_MODELS[args.model])}")
    if args.aspect_ratio and args.aspect_ratio not in ASPECT_RATIOS:
        raise SkillError(f"unsupported aspect ratio; choose {', '.join(ASPECT_RATIOS)}")
    settings: dict[str, Any] = {"seconds": args.seconds, "resolution": args.resolution}
    if args.aspect_ratio:
        settings["aspect_ratio"] = args.aspect_ratio
    if args.generate_audio is not None:
        settings["generate_audio"] = args.generate_audio
    state.update({"model": args.model, "settings": settings, "status": "ready"})


def request_payload(state: dict[str, Any]) -> dict[str, Any]:
    model, settings = state.get("model"), state.get("settings")
    if model not in VIDEO_MODELS or not isinstance(settings, dict):
        raise SkillError("video workflow configuration is invalid")
    seconds, resolution = settings.get("seconds"), settings.get("resolution")
    if not isinstance(seconds, int) or isinstance(seconds, bool) or not 1 <= seconds <= 15 or resolution not in VIDEO_MODELS[model]:
        raise SkillError("video workflow configuration is invalid")
    payload = {"model": model, "seconds": seconds, "resolution": resolution}
    if state["prompt"]:
        payload["prompt"] = state["prompt"]
    ratio = settings.get("aspect_ratio")
    if ratio:
        if ratio not in ASPECT_RATIOS:
            raise SkillError("video workflow aspect ratio is invalid")
        payload["aspect_ratio"] = ratio
    if "generate_audio" in settings:
        if not isinstance(settings["generate_audio"], bool):
            raise SkillError("video workflow audio option is invalid")
        payload["generate_audio"] = settings["generate_audio"]
    return payload


def multipart_payload(payload: dict[str, Any], image_path: Path) -> tuple[bytes, str]:
    image = validate_reference(str(image_path))
    image_bytes = image.read_bytes()
    if len(image_bytes) > MAX_REFERENCE_BYTES:
        raise SkillError("video reference image must be at most 20 MiB")
    boundary = f"----CoderAPIVideo{secrets.token_hex(16)}"
    body = bytearray()
    for key, value in payload.items():
        body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n'.encode("ascii"))
        body.extend((str(value).lower() if isinstance(value, bool) else str(value)).encode("utf-8"))
        body.extend(b"\r\n")
    mime = inferred_mime_type(image_bytes[:12], "")
    extension = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}[mime]
    body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="reference.{extension}"\r\nContent-Type: {mime}\r\n\r\n'.encode("ascii"))
    body.extend(image_bytes)
    body.extend(f"\r\n--{boundary}--\r\n".encode("ascii"))
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def http_json(url: str, api_key: str, timeout: int, payload: bytes | None = None, content_type: str = "application/json") -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST" if payload is not None else "GET",
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json", "Content-Type": content_type} if payload is not None else {"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
    )
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            body = response.read(MAX_JSON_BYTES + 1)
    except urllib.error.HTTPError as error:
        message = api_error_message(error.read(8192)).replace(api_key, "[redacted]")
        raise SkillError(f"video API returned HTTP {error.code}: {message}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise SkillError(f"video API request failed: {str(error).replace(api_key, '[redacted]')}") from error
    if len(body) > MAX_JSON_BYTES:
        raise SkillError("video task response exceeds the 1 MiB safety limit")
    try:
        data = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SkillError("video task response is not valid JSON") from error
    if not isinstance(data, dict):
        raise SkillError("video task response must be a JSON object")
    return data


def valid_task_id(value: Any) -> str:
    if not isinstance(value, str) or not TASK_ID_PATTERN.fullmatch(value):
        raise SkillError("video response did not contain a valid public task ID")
    return value


def state_url(state: dict[str, Any]) -> str:
    base_url = state.get("base_url")
    if not isinstance(base_url, str) or base_url != api_base_url():
        raise SkillError("CODER_API_BASE_URL differs from the URL used for this video task")
    return base_url


def submit(path: Path, state: dict[str, Any], timeout: int) -> dict[str, Any]:
    if state["status"] != "ready":
        raise SkillError(f"video workflow is not ready to submit; current status is {state['status']}")
    payload = request_payload(state)
    if state.get("image_path"):
        body, content_type = multipart_payload(payload, Path(state["image_path"]))
    else:
        body, content_type = json.dumps(payload).encode("utf-8"), "application/json"
    base_url = api_base_url()
    api_key = read_api_key()
    state.update({"status": "submitting", "base_url": base_url})
    write_private_json(path, state)
    try:
        response = http_json(f"{base_url}/videos", api_key, timeout, body, content_type)
    except SkillError as error:
        # Only a definite client-side HTTP rejection permits a fresh submission.
        cause = error.__cause__
        if isinstance(cause, urllib.error.HTTPError) and cause.code in {400, 401, 403, 404, 422}:
            state["status"] = "ready"
        else:
            state["status"] = "submission_uncertain"
        write_private_json(path, state)
        raise
    try:
        state["task_id"] = valid_task_id(response.get("id"))
    except SkillError:
        state["status"] = "submission_uncertain"
        write_private_json(path, state)
        raise
    state["status"] = "submitted"
    write_private_json(path, state)
    return state_result(path, state)


def attach_task(path: Path, state: dict[str, Any], task_id: str | None) -> dict[str, Any]:
    if state["status"] not in {"submitting", "submission_uncertain"}:
        raise SkillError("manual task ID reconciliation is only available after an uncertain submission")
    state["task_id"] = valid_task_id(task_id)
    state["status"] = "submitted"
    write_private_json(path, state)
    return state_result(path, state)


def download_video(url: str, api_key: str, timeout: int, output_dir: Path, output: str | None) -> Path:
    if output and Path(output).suffix.lower() not in {"", ".mp4"}:
        raise SkillError("--output must be an .mp4 filename")
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}", "Accept": "video/mp4"})
    output_path: Path | None = None
    created = False
    complete = False
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            size = response.headers.get("Content-Length")
            if size and size.isdecimal() and int(size) > MAX_VIDEO_BYTES:
                raise SkillError("video exceeds the 512 MiB download limit")
            first = response.read(64 * 1024)
            if len(first) < 12 or first[4:8] != b"ftyp":
                raise SkillError("video content was not an MP4 file")
            name = output or f"generated-video-{dt.datetime.now(tz=dt.UTC):%Y%m%d-%H%M%S}"
            output_path = unique_output_path(output_dir, name, ".mp4")
            with output_path.open("xb") as file:
                created = True
                file.write(first)
                total = len(first)
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_VIDEO_BYTES:
                        raise SkillError("video exceeds the 512 MiB download limit")
                    file.write(chunk)
            complete = True
    except urllib.error.HTTPError as error:
        message = api_error_message(error.read(8192)).replace(api_key, "[redacted]")
        raise SkillError(f"video download returned HTTP {error.code}: {message}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise SkillError(f"video download failed: {str(error).replace(api_key, '[redacted]')}") from error
    finally:
        if created and not complete and output_path is not None:
            output_path.unlink(missing_ok=True)
    if output_path is None:
        raise SkillError("video download returned no content")
    return output_path.resolve()


def remove_state(path: Path) -> None:
    path.unlink(missing_ok=True)
    if path.parent.parent == Path(tempfile.gettempdir()).resolve() and path.parent.name.startswith("coder-api-video-"):
        path.parent.rmdir()


def poll(path: Path, state: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    if state["status"] not in {"submitted", "in_progress", "completed"}:
        raise SkillError(f"video task cannot be polled in status {state['status']}")
    task_id = valid_task_id(state.get("task_id"))
    base_url = state_url(state)
    api_key = read_api_key()
    deadline = time.monotonic() + args.max_wait
    while state["status"] != "completed":
        response = http_json(f"{base_url}/videos/{urllib.parse.quote(task_id, safe='')}", api_key, args.timeout)
        if response.get("id") != task_id:
            raise SkillError("video status response returned a different task ID")
        status = response.get("status")
        if status == "completed":
            state["status"] = "completed"
        elif status in {"failed", "canceled"}:
            state["status"] = status
            error = response.get("error")
            message = error.get("message", "") if isinstance(error, dict) else ""
            state["error"] = message[:500].replace(api_key, "[redacted]") if isinstance(message, str) else ""
        elif status in {"queued", "in_progress"}:
            state["status"] = "in_progress"
        else:
            raise SkillError("video task returned an unsupported status")
        write_private_json(path, state)
        if state["status"] in {"failed", "canceled"}:
            return state_result(path, state)
        if state["status"] == "completed" or time.monotonic() >= deadline:
            break
        time.sleep(min(args.poll_interval, max(0, deadline - time.monotonic())))
    if state["status"] != "completed":
        return state_result(path, state)
    output_path = download_video(f"{base_url}/videos/{urllib.parse.quote(task_id, safe='')}/content", api_key, args.timeout, Path(args.output_dir), args.output)
    result: dict[str, Any] = {"file": str(output_path), "model": state["model"], "mime_type": "video/mp4", "media": "video", "task_id": task_id}
    if state.get("local_key_saved"):
        result["security_reminder"] = security_reminder()
    remove_state(path)
    return result


def main() -> int:
    args = parse_args()
    try:
        if not 1 <= args.timeout <= MAX_TIMEOUT or args.max_wait < 0 or args.poll_interval < 1:
            raise SkillError("--timeout must be 1-1000; --max-wait >= 0; --poll-interval >= 1")
        if not args.begin and (args.prompt is not None or args.image is not None):
            raise SkillError("--prompt and --image are accepted only with --begin")
        if not args.select_configuration and any(value is not None for value in (args.model, args.seconds, args.resolution, args.aspect_ratio, args.generate_audio)):
            raise SkillError("video model and settings are accepted only with --select-configuration")
        if not args.attach_task_id and args.task_id is not None:
            raise SkillError("--task-id is accepted only with --attach-task-id")
        if not args.save_local_key and args.api_key is not None:
            raise SkillError("--api-key is accepted only with --save-local-key")
        if args.list_models:
            result: dict[str, Any] = {"media": "video", "models": VIDEO_MODELS, "seconds": {"min": 1, "max": 15}, "aspect_ratios": ASPECT_RATIOS}
        elif args.begin:
            if args.state:
                raise SkillError("--begin cannot use --state")
            path, state = begin(args)
            result = state_result(path, state)
        else:
            if not args.state:
                raise SkillError("--state is required")
            path = Path(args.state).expanduser().resolve()
            state = read_state(path)
            if args.save_local_key:
                if state["status"] != "key_storage_decision" or not args.api_key:
                    raise SkillError("--save-local-key requires a key_storage_decision state and --api-key")
                configure_api_key(local_config_path(), args.api_key, emit_reminder=False)
                state.update({"status": "model_selection", "local_key_saved": True})
                write_private_json(path, state)
                result = state_result(path, state)
            elif args.select_configuration:
                select_configuration(state, args)
                write_private_json(path, state)
                result = state_result(path, state)
            elif args.submit:
                result = submit(path, state, args.timeout)
            elif args.attach_task_id:
                result = attach_task(path, state, args.task_id)
            else:
                if args.max_wait > 3600:
                    raise SkillError("--max-wait must not exceed 3600 seconds")
                result = poll(path, state, args)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except SkillError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
