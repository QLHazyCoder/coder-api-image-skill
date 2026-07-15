#!/usr/bin/env python3
"""Generate one image through Coder API without exposing API credentials."""

from __future__ import annotations

import argparse
import base64
import binascii
import datetime as dt
import getpass
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = "https://api.qlhazycoder.tech/v1"
DEFAULT_MODEL = "gpt-image-2"
MAX_IMAGE_BYTES = 50 * 1024 * 1024
CONFIG_ENV_VAR = "CODER_API_CONFIG_PATH"
WORKFLOW_STATE_VERSION = 1
MAX_REQUEST_TIMEOUT_SECONDS = 120
MAX_GENERATION_ATTEMPTS = 3
RETRY_DELAYS_SECONDS = (1, 2)
GPT_IMAGE_2_SIZES = [
    "auto",
    "1024x1024",
    "1024x1536",
    "1536x1024",
    "1024x1792",
    "1792x1024",
    "2048x2048",
    "2560x1440",
    "1440x2560",
    "3840x2160",
    "2160x3840",
]
GPT_IMAGE_2_SIZE_LABELS = {
    "auto": "auto (model selected)",
    "1024x1024": "1024x1024 (1K)",
    "1024x1536": "1024x1536 (about 1.5K)",
    "1536x1024": "1536x1024 (about 1.5K)",
    "1024x1792": "1024x1792 (about 1.8K)",
    "1792x1024": "1792x1024 (about 1.8K)",
    "2048x2048": "2048x2048 (2K)",
    "2560x1440": "2560x1440 (about 2.5K)",
    "1440x2560": "1440x2560 (about 2.5K)",
    "3840x2160": "3840x2160 (4K)",
    "2160x3840": "2160x3840 (4K)",
}

MODEL_CATALOG: dict[str, dict[str, Any]] = {
    "gpt-image-2": {
        "label": "GPT Image 2",
        "parameter": "size",
        "options": GPT_IMAGE_2_SIZES,
        "default": "1024x1024",
        "option_labels": GPT_IMAGE_2_SIZE_LABELS,
    },
    "gemini-3.1-flash-image-1k": {
        "label": "Gemini 3.1 Flash Image 1K",
        "parameter": "aspect_ratio",
        "options": ["1:1", "16:9", "9:16"],
        "default": "1:1",
        "resolution_locked": "1K",
    },
    "gemini-3.1-flash-image-2k": {
        "label": "Gemini 3.1 Flash Image 2K",
        "parameter": "aspect_ratio",
        "options": ["1:1", "16:9", "9:16"],
        "default": "1:1",
        "resolution_locked": "2K",
    },
    "gemini-3.1-flash-image-4k": {
        "label": "Gemini 3.1 Flash Image 4K",
        "parameter": "aspect_ratio",
        "options": ["1:1", "16:9", "9:16"],
        "default": "1:1",
        "resolution_locked": "4K",
    },
}

MIME_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


class SkillError(Exception):
    """An error that is safe to return to the agent or user."""


class GenerationError(SkillError):
    """A generation request failure with retry guidance."""

    def __init__(self, message: str, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class RetryExhausted(SkillError):
    """All allowed generation attempts failed or retrying would be pointless."""

    def __init__(self, attempts: int, last_error: SkillError) -> None:
        super().__init__(str(last_error))
        self.attempts = attempts
        self.last_error = str(last_error)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list-models", action="store_true", help="print the built-in model catalog")
    parser.add_argument("--configure", action="store_true", help="save an API key in the local private config")
    parser.add_argument(
        "--confirm-local-storage",
        action="store_true",
        help="required acknowledgement before --configure writes a local key",
    )
    parser.add_argument("--remove-key", action="store_true", help="delete the locally saved API key")
    parser.add_argument("--show-config-path", action="store_true", help="print the local private config path")
    parser.add_argument("--begin", action="store_true", help="start an image-generation workflow")
    parser.add_argument("--save-local-key", action="store_true", help="save a key for a workflow after user confirmation")
    parser.add_argument("--select-model", action="store_true", help="record a model choice for a workflow")
    parser.add_argument("--select-layout", action="store_true", help="record a layout choice for a workflow")
    parser.add_argument("--generate", action="store_true", help="generate from a ready workflow")
    parser.add_argument("--continue-retry", action="store_true", help="start another retry round after user confirmation")
    parser.add_argument("--state", help="workflow state file returned by --begin")
    parser.add_argument("--prompt", help="image prompt")
    parser.add_argument("--model", choices=sorted(MODEL_CATALOG), help="explicit model choice")
    parser.add_argument("--size", help="GPT Image 2 size")
    parser.add_argument("--aspect-ratio", help="Gemini aspect ratio")
    parser.add_argument("--output-dir", default=".", help="directory for the generated file")
    parser.add_argument("--output", help="optional output filename")
    parser.add_argument(
        "--timeout",
        type=int,
        default=MAX_REQUEST_TIMEOUT_SECONDS,
        help=f"per-attempt request timeout in seconds (maximum {MAX_REQUEST_TIMEOUT_SECONDS})",
    )
    return parser.parse_args()


def model_catalog_for_output() -> list[dict[str, Any]]:
    return [
        {
            "model": model,
            "label": config["label"],
            "parameter": config["parameter"],
            "options": config["options"],
            "display_options": option_display_values(config),
            "default": config["default"],
            "resolution_locked": config.get("resolution_locked"),
        }
        for model, config in MODEL_CATALOG.items()
    ]


def option_display_values(config: dict[str, Any]) -> list[dict[str, str]]:
    labels = config.get("option_labels", {})
    return [{"value": option, "label": labels.get(option, option)} for option in config["options"]]


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    if not args.prompt or not args.prompt.strip():
        raise SkillError("--prompt is required")
    if not args.model:
        raise SkillError("--model is required; select a model through the workflow first")
    if args.timeout <= 0 or args.timeout > MAX_REQUEST_TIMEOUT_SECONDS:
        raise SkillError(f"--timeout must be between 1 and {MAX_REQUEST_TIMEOUT_SECONDS} seconds")

    model = args.model
    config = MODEL_CATALOG[model]
    payload: dict[str, Any] = {
        "model": model,
        "prompt": args.prompt.strip(),
        "n": 1,
        "response_format": "b64_json",
    }

    if config["parameter"] == "size":
        if args.aspect_ratio:
            raise SkillError(f"{model} uses --size, not --aspect-ratio")
        size = args.size
        if not size:
            raise SkillError(f"--size is required for {model}; select a layout through the workflow first")
        if size not in config["options"]:
            raise SkillError(f"unsupported size {size!r} for {model}")
        payload["size"] = size
        payload["quality"] = "auto"
        payload["output_format"] = "png"
        return payload

    if args.size:
        raise SkillError(f"{model} uses --aspect-ratio, not --size")
    aspect_ratio = args.aspect_ratio
    if not aspect_ratio:
        raise SkillError(f"--aspect-ratio is required for {model}; select a layout through the workflow first")
    if aspect_ratio not in config["options"]:
        raise SkillError(f"unsupported aspect ratio {aspect_ratio!r} for {model}")
    payload["aspect_ratio"] = aspect_ratio
    return payload


def api_base_url() -> str:
    base_url = os.environ.get("CODER_API_BASE_URL", DEFAULT_BASE_URL).strip().rstrip("/")
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise SkillError("CODER_API_BASE_URL must be an absolute http(s) URL")
    if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise SkillError("CODER_API_BASE_URL must use HTTPS outside local testing")
    return base_url


def local_config_path() -> Path:
    configured_path = os.environ.get(CONFIG_ENV_VAR, "").strip()
    if configured_path:
        return Path(configured_path).expanduser()
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_home / "coder-api-image" / "credentials.json"


def write_local_api_key(config_path: Path, api_key: str) -> None:
    if not api_key:
        raise SkillError("API key cannot be empty")
    config_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(config_path.parent, 0o700)
    temporary_path = config_path.with_name(f".{config_path.name}.{os.getpid()}.tmp")
    try:
        descriptor = os.open(temporary_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as config_file:
            json.dump({"api_key": api_key}, config_file)
            config_file.write("\n")
        os.replace(temporary_path, config_path)
        os.chmod(config_path, 0o600)
    finally:
        temporary_path.unlink(missing_ok=True)


def read_local_api_key(config_path: Path) -> str:
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ""
    except (OSError, json.JSONDecodeError) as error:
        raise SkillError(f"local API key config is invalid; run --configure ({error})") from error
    api_key = config.get("api_key") if isinstance(config, dict) else ""
    if not isinstance(api_key, str):
        raise SkillError("local API key config is invalid; run --configure")
    return api_key.strip()


def security_reminder() -> str:
    return (
        "Security reminder: enable model limits for this key and allow only the models you intend to use. "
        "Optionally enable an IP allowlist when this Codex machine has a stable public egress IP. "
        "A changing home or mobile IP can otherwise lock the skill out."
    )


def configure_api_key(config_path: Path, emit_reminder: bool = True) -> None:
    api_key = getpass.getpass("Coder API key (stored locally with mode 0600): ").strip()
    write_local_api_key(config_path, api_key)
    if emit_reminder:
        print(f"Saved local API key to {config_path}")
        print(security_reminder())


def remove_local_api_key(config_path: Path) -> None:
    try:
        config_path.unlink()
    except FileNotFoundError:
        print(f"No local API key was stored at {config_path}")
        return
    print(f"Removed local API key from {config_path}")


def write_private_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        descriptor = os.open(temporary_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as state_file:
            json.dump(payload, state_file, ensure_ascii=False)
            state_file.write("\n")
        os.replace(temporary_path, path)
        os.chmod(path, 0o600)
    finally:
        temporary_path.unlink(missing_ok=True)


def create_workflow_state(prompt: str) -> Path:
    if not prompt.strip():
        raise SkillError("--prompt is required with --begin")
    environment_key = os.environ.get("CODER_API_KEY", "").strip()
    key_available = bool(environment_key or read_local_api_key(local_config_path()))
    state_directory = Path(tempfile.mkdtemp(prefix="coder-api-image-"))
    os.chmod(state_directory, 0o700)
    state_path = state_directory / "workflow.json"
    state = {
        "version": WORKFLOW_STATE_VERSION,
        "prompt": prompt.strip(),
        "status": "model_selection" if key_available else "key_storage_decision",
        "local_key_saved": False,
    }
    write_private_json(state_path, state)
    return state_path


def remove_workflow_state(state_path: Path) -> None:
    state_path.unlink(missing_ok=True)
    temporary_root = Path(tempfile.gettempdir()).resolve()
    if state_path.parent.parent == temporary_root and state_path.parent.name.startswith("coder-api-image-"):
        state_path.parent.rmdir()


def read_workflow_state(state_path: Path) -> dict[str, Any]:
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise SkillError("workflow state does not exist; start again with --begin") from error
    except (OSError, json.JSONDecodeError) as error:
        raise SkillError(f"workflow state is invalid: {error}") from error
    if not isinstance(state, dict) or state.get("version") != WORKFLOW_STATE_VERSION:
        raise SkillError("workflow state is invalid or from an unsupported version")
    if not isinstance(state.get("prompt"), str) or not isinstance(state.get("status"), str):
        raise SkillError("workflow state is invalid")
    return state


def require_state_path(args: argparse.Namespace) -> Path:
    if not args.state:
        raise SkillError("--state is required for this workflow operation")
    return Path(args.state).expanduser().resolve()


def workflow_result(state_path: Path, state: dict[str, Any]) -> dict[str, Any]:
    status = state["status"]
    result: dict[str, Any] = {"state": str(state_path), "status": status}
    if status == "key_storage_decision":
        result.update(
            {
                "action": "ask_user_to_confirm_local_key_storage",
                "security_reminder": security_reminder(),
            }
        )
    elif status == "model_selection":
        result.update(
            {
                "action": "ask_user_to_choose_model",
                "default_model": DEFAULT_MODEL,
                "models": model_catalog_for_output(),
            }
        )
    elif status == "layout_selection":
        model = state["model"]
        config = MODEL_CATALOG[model]
        result.update(
            {
                "action": "ask_user_to_choose_layout",
                "model": model,
                "parameter": config["parameter"],
                "options": config["options"],
                "display_options": option_display_values(config),
                "default": config["default"],
            }
        )
    elif status == "ready":
        result.update({"action": "ready_to_generate", "model": state["model"], "layout": state["layout"]})
    elif status == "retry_exhausted":
        result.update(
            {
                "action": "ask_user_to_continue_retrying",
                "attempts": state["last_failure"]["attempts"],
                "last_error": state["last_failure"]["last_error"],
            }
        )
    else:
        raise SkillError("workflow state has an unsupported status")
    return result


def save_local_key_for_workflow(args: argparse.Namespace) -> dict[str, Any]:
    state_path = require_state_path(args)
    state = read_workflow_state(state_path)
    if state["status"] != "key_storage_decision":
        raise SkillError("local key storage is not the next workflow step")
    configure_api_key(local_config_path(), emit_reminder=False)
    state["local_key_saved"] = True
    state["status"] = "model_selection"
    write_private_json(state_path, state)
    return workflow_result(state_path, state)


def select_workflow_model(args: argparse.Namespace) -> dict[str, Any]:
    state_path = require_state_path(args)
    state = read_workflow_state(state_path)
    if state["status"] != "model_selection":
        raise SkillError("model selection is not the next workflow step")
    if not args.model:
        raise SkillError("--model is required with --select-model")
    state["model"] = args.model
    state["status"] = "layout_selection"
    write_private_json(state_path, state)
    return workflow_result(state_path, state)


def select_workflow_layout(args: argparse.Namespace) -> dict[str, Any]:
    state_path = require_state_path(args)
    state = read_workflow_state(state_path)
    if state["status"] != "layout_selection":
        raise SkillError("layout selection is not the next workflow step")
    model = state.get("model")
    if model not in MODEL_CATALOG:
        raise SkillError("workflow state has an invalid model")
    config = MODEL_CATALOG[model]
    if config["parameter"] == "size":
        if args.aspect_ratio:
            raise SkillError(f"{model} uses --size, not --aspect-ratio")
        if args.size not in config["options"]:
            raise SkillError(f"unsupported size {args.size!r} for {model}")
        state["layout"] = {"size": args.size}
    else:
        if args.size:
            raise SkillError(f"{model} uses --aspect-ratio, not --size")
        if args.aspect_ratio not in config["options"]:
            raise SkillError(f"unsupported aspect ratio {args.aspect_ratio!r} for {model}")
        state["layout"] = {"aspect_ratio": args.aspect_ratio}
    state["status"] = "ready"
    write_private_json(state_path, state)
    return workflow_result(state_path, state)


def continue_workflow_retry(args: argparse.Namespace) -> dict[str, Any]:
    state_path = require_state_path(args)
    state = read_workflow_state(state_path)
    if state["status"] != "retry_exhausted":
        raise SkillError("retry continuation is not the next workflow step")
    state.pop("last_failure", None)
    state["status"] = "ready"
    state["retry_round"] = int(state.get("retry_round", 0)) + 1
    write_private_json(state_path, state)
    return workflow_result(state_path, state)


def read_api_key() -> str:
    api_key = os.environ.get("CODER_API_KEY", "").strip()
    if api_key:
        return api_key
    api_key = read_local_api_key(local_config_path())
    if api_key:
        return api_key
    raise SkillError("no API key found; run --configure or set CODER_API_KEY")


def api_error_message(body: bytes) -> str:
    try:
        decoded = json.loads(body.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return body.decode("utf-8", errors="replace").strip()[:500] or "upstream returned no error body"
    if isinstance(decoded, dict):
        error = decoded.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"][:500]
        if isinstance(decoded.get("message"), str):
            return decoded["message"][:500]
    return "upstream returned an unrecognized error response"


def post_generation(payload: dict[str, Any], api_key: str, base_url: str, timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{base_url}/images/generations",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(MAX_IMAGE_BYTES + 1)
    except urllib.error.HTTPError as error:
        retryable = error.code == 408 or error.code == 409 or error.code == 429 or error.code >= 500
        raise GenerationError(
            f"generation failed with HTTP {error.code}: {api_error_message(error.read(8192))}",
            retryable=retryable,
        ) from error
    except urllib.error.URLError as error:
        raise GenerationError(f"generation request failed: {error.reason}", retryable=True) from error
    except TimeoutError as error:
        raise GenerationError("generation request timed out", retryable=True) from error

    if len(body) > MAX_IMAGE_BYTES:
        raise GenerationError("generation response exceeds the 50 MiB safety limit", retryable=False)
    try:
        decoded = json.loads(body)
    except json.JSONDecodeError as error:
        raise GenerationError("generation response was not valid JSON", retryable=True) from error
    if not isinstance(decoded, dict):
        raise GenerationError("generation response was not an object", retryable=True)
    return decoded


def is_local_url(url: str) -> bool:
    host = urllib.parse.urlparse(url).hostname
    return host in {"127.0.0.1", "localhost", "::1"}


def download_image(url: str, timeout: int, allow_insecure: bool) -> tuple[bytes, str]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"https", "http"}:
        raise SkillError("image URL must use http(s)")
    if parsed.scheme != "https" and not (allow_insecure and is_local_url(url)):
        raise SkillError("image URL must use HTTPS outside local testing")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            image_bytes = response.read(MAX_IMAGE_BYTES + 1)
            mime_type = response.headers.get_content_type()
    except urllib.error.URLError as error:
        raise SkillError(f"failed to download generated image: {error.reason}") from error
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise SkillError("downloaded image exceeds the 50 MiB safety limit")
    return image_bytes, mime_type


def inferred_mime_type(image_bytes: bytes, declared_mime_type: str) -> str:
    if declared_mime_type in MIME_EXTENSIONS:
        return declared_mime_type
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def unique_output_path(output_dir: Path, output: str | None, extension: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    if output:
        requested = Path(output)
        if requested.name != output:
            raise SkillError("--output must be a filename, not a path")
        filename = requested.name if requested.suffix else f"{requested.name}{extension}"
    else:
        filename = f"generated-image-{dt.datetime.now(tz=dt.UTC):%Y%m%d-%H%M%S}{extension}"
    candidate = output_dir / filename
    index = 1
    while candidate.exists():
        candidate = output_dir / f"{Path(filename).stem}-{index}{Path(filename).suffix}"
        index += 1
    return candidate


def save_first_image(response: dict[str, Any], output_dir: Path, output: str | None, timeout: int, base_url: str) -> tuple[Path, str, str]:
    data = response.get("data")
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        raise SkillError("generation response did not contain image data")
    image_data = data[0]
    declared_mime_type = image_data.get("mime_type", "")
    revised_prompt = image_data.get("revised_prompt", "")
    if not isinstance(declared_mime_type, str):
        declared_mime_type = ""
    if not isinstance(revised_prompt, str):
        revised_prompt = ""

    b64_json = image_data.get("b64_json")
    if isinstance(b64_json, str) and b64_json:
        try:
            image_bytes = base64.b64decode(b64_json, validate=True)
        except (binascii.Error, ValueError) as error:
            raise SkillError("generation returned invalid Base64 image data") from error
        if len(image_bytes) > MAX_IMAGE_BYTES:
            raise SkillError("decoded image exceeds the 50 MiB safety limit")
    else:
        image_url = image_data.get("url")
        if not isinstance(image_url, str) or not image_url:
            raise SkillError("generation response did not contain b64_json or url")
        image_bytes, downloaded_mime_type = download_image(
            image_url,
            timeout,
            urllib.parse.urlparse(base_url).scheme == "http",
        )
        if not declared_mime_type:
            declared_mime_type = downloaded_mime_type

    mime_type = inferred_mime_type(image_bytes, declared_mime_type)
    extension = MIME_EXTENSIONS.get(mime_type, ".bin")
    output_path = unique_output_path(output_dir, output, extension)
    output_path.write_bytes(image_bytes)
    return output_path.resolve(), mime_type, revised_prompt


def generate_with_retries(
    payload: dict[str, Any],
    api_key: str,
    base_url: str,
    timeout: int,
    output_dir: Path,
    output: str | None,
) -> tuple[Path, str, str]:
    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        try:
            response = post_generation(payload, api_key, base_url, timeout)
        except GenerationError as error:
            if not error.retryable or attempt == MAX_GENERATION_ATTEMPTS:
                raise RetryExhausted(attempt, error) from error
            time.sleep(RETRY_DELAYS_SECONDS[attempt - 1])
            continue

        try:
            return save_first_image(response, output_dir, output, timeout, base_url)
        except SkillError as error:
            # The upstream may have completed; never submit a duplicate request for a local save/download failure.
            raise RetryExhausted(attempt, error) from error

    raise AssertionError("generation retry loop terminated unexpectedly")


def main() -> int:
    args = parse_args()
    try:
        if args.timeout <= 0 or args.timeout > MAX_REQUEST_TIMEOUT_SECONDS:
            raise SkillError(f"--timeout must be between 1 and {MAX_REQUEST_TIMEOUT_SECONDS} seconds")
        operations = [
            args.list_models,
            args.configure,
            args.remove_key,
            args.show_config_path,
            args.begin,
            args.save_local_key,
            args.select_model,
            args.select_layout,
            args.generate,
            args.continue_retry,
        ]
        if sum(operations) != 1:
            raise SkillError("choose exactly one operation")
        if args.list_models:
            print(json.dumps({"default_model": DEFAULT_MODEL, "models": model_catalog_for_output()}, ensure_ascii=False, indent=2))
            return 0
        config_path = local_config_path()
        if args.show_config_path:
            print(config_path)
            return 0
        if args.configure:
            if not args.confirm_local_storage:
                raise SkillError("--configure requires --confirm-local-storage")
            if args.prompt or args.state:
                raise SkillError("--configure cannot be combined with --prompt or --state")
            configure_api_key(config_path)
            print(json.dumps({"configured": str(config_path), "security_reminder": security_reminder()}, ensure_ascii=False))
            return 0
        if args.remove_key:
            if args.prompt or args.state:
                raise SkillError("--remove-key cannot be combined with --prompt or --state")
            remove_local_api_key(config_path)
            return 0
        if args.begin:
            state_path = create_workflow_state(args.prompt or "")
            print(json.dumps(workflow_result(state_path, read_workflow_state(state_path)), ensure_ascii=False))
            return 0
        if args.save_local_key:
            print(json.dumps(save_local_key_for_workflow(args), ensure_ascii=False))
            return 0
        if args.select_model:
            print(json.dumps(select_workflow_model(args), ensure_ascii=False))
            return 0
        if args.select_layout:
            print(json.dumps(select_workflow_layout(args), ensure_ascii=False))
            return 0
        if args.continue_retry:
            print(json.dumps(continue_workflow_retry(args), ensure_ascii=False))
            return 0

        state_path = require_state_path(args)
        state = read_workflow_state(state_path)
        if state["status"] != "ready":
            raise SkillError(f"workflow is not ready to generate; next step is {state['status']}")
        layout = state.get("layout")
        if not isinstance(layout, dict):
            raise SkillError("workflow state has an invalid layout")
        generation_args = argparse.Namespace(
            prompt=state["prompt"],
            model=state.get("model"),
            size=layout.get("size"),
            aspect_ratio=layout.get("aspect_ratio"),
            timeout=args.timeout,
        )
        payload = build_payload(generation_args)
        base_url = api_base_url()
        try:
            output_path, mime_type, revised_prompt = generate_with_retries(
                payload,
                read_api_key(),
                base_url,
                args.timeout,
                Path(args.output_dir),
                args.output,
            )
        except RetryExhausted as error:
            state["status"] = "retry_exhausted"
            state["last_failure"] = {"attempts": error.attempts, "last_error": error.last_error}
            write_private_json(state_path, state)
            print(json.dumps(workflow_result(state_path, state), ensure_ascii=False))
            return 0
    except SkillError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    result = {"file": str(output_path), "model": payload["model"], "mime_type": mime_type}
    if revised_prompt:
        result["revised_prompt"] = revised_prompt
    if state.get("local_key_saved"):
        result["security_reminder"] = security_reminder()
    remove_workflow_state(state_path)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
