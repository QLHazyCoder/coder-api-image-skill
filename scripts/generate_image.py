#!/usr/bin/env python3
"""Generate one image through Coder API without exposing API credentials."""

from __future__ import annotations

import argparse
import base64
import binascii
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = "https://api.qlhazycoder.tech/v1"
DEFAULT_MODEL = "gpt-image-2"
MAX_IMAGE_BYTES = 50 * 1024 * 1024

MODEL_CATALOG: dict[str, dict[str, Any]] = {
    "gpt-image-2": {
        "label": "GPT Image 2",
        "parameter": "size",
        "options": ["1024x1024", "1536x1024", "1024x1536"],
        "default": "1024x1024",
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list-models", action="store_true", help="print the built-in model catalog")
    parser.add_argument("--prompt", help="image prompt")
    parser.add_argument("--model", choices=sorted(MODEL_CATALOG), default=DEFAULT_MODEL)
    parser.add_argument("--size", help="GPT Image 2 size")
    parser.add_argument("--aspect-ratio", help="Gemini aspect ratio")
    parser.add_argument("--output-dir", default=".", help="directory for the generated file")
    parser.add_argument("--output", help="optional output filename")
    parser.add_argument("--timeout", type=int, default=300, help="request timeout in seconds")
    return parser.parse_args()


def model_catalog_for_output() -> list[dict[str, Any]]:
    return [
        {
            "model": model,
            "label": config["label"],
            "parameter": config["parameter"],
            "options": config["options"],
            "default": config["default"],
            "resolution_locked": config.get("resolution_locked"),
        }
        for model, config in MODEL_CATALOG.items()
    ]


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    if not args.prompt or not args.prompt.strip():
        raise SkillError("--prompt is required")
    if args.timeout <= 0:
        raise SkillError("--timeout must be positive")

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
        size = args.size or config["default"]
        if size not in config["options"]:
            raise SkillError(f"unsupported size {size!r} for {model}")
        payload["size"] = size
        payload["quality"] = "auto"
        payload["output_format"] = "png"
        return payload

    if args.size:
        raise SkillError(f"{model} uses --aspect-ratio, not --size")
    aspect_ratio = args.aspect_ratio or config["default"]
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


def read_api_key() -> str:
    api_key = os.environ.get("CODER_API_KEY", "").strip()
    if not api_key:
        raise SkillError("CODER_API_KEY is not set")
    return api_key


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
        raise SkillError(f"generation failed with HTTP {error.code}: {api_error_message(error.read(8192))}") from error
    except urllib.error.URLError as error:
        raise SkillError(f"generation request failed: {error.reason}") from error
    except TimeoutError as error:
        raise SkillError("generation request timed out; do not retry automatically because it may have completed") from error

    if len(body) > MAX_IMAGE_BYTES:
        raise SkillError("generation response exceeds the 50 MiB safety limit")
    try:
        decoded = json.loads(body)
    except json.JSONDecodeError as error:
        raise SkillError("generation response was not valid JSON") from error
    if not isinstance(decoded, dict):
        raise SkillError("generation response was not an object")
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


def main() -> int:
    args = parse_args()
    if args.list_models:
        print(json.dumps({"default_model": DEFAULT_MODEL, "models": model_catalog_for_output()}, ensure_ascii=False, indent=2))
        return 0

    try:
        payload = build_payload(args)
        base_url = api_base_url()
        response = post_generation(payload, read_api_key(), base_url, args.timeout)
        output_path, mime_type, revised_prompt = save_first_image(
            response,
            Path(args.output_dir),
            args.output,
            args.timeout,
            base_url,
        )
    except SkillError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    result = {"file": str(output_path), "model": payload["model"], "mime_type": mime_type}
    if revised_prompt:
        result["revised_prompt"] = revised_prompt
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
