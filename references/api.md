# Coder API Image and Video Contract

The script posts text-to-image JSON requests to `POST /v1/images/generations` and GPT Image 2 edit multipart requests to `POST /v1/images/edits`, using an API key from `CODER_API_KEY` or the local private config created by `--configure`.

## Local Credential Storage

`python3 scripts/generate_image.py --configure --confirm-local-storage --api-key "<key>"` writes `~/.config/coder-api-image/credentials.json` with mode `0600`. It performs no network validation and never stores a key in the Skill directory or repository. A key supplied in the conversation is saved automatically by the skill, with no second interactive paste.

Before configuring a key, tell the user to enable model limits and whitelist only the needed models. An IP allowlist is optional and appropriate only for a stable Codex egress IP. The standard image API cannot safely report whether those account-level restrictions are enabled, so the Skill only reminds the user.

## Built-In Model Parameters

| Model | Request fields |
| --- | --- |
| `gpt-image-2` | `size`, `quality=auto`, `output_format=png`; no separate `resolution` field |
| `gpt-image-2.5` | `size`, `quality=auto`, `output_format=png`; no separate `resolution` field |
| `gpt-image-2.5-flare` | `size`, `quality=auto`, `output_format=png`; no separate `resolution` field |
| `gpt-image-2.5-sunburst` | `size`, `quality=auto`, `output_format=png`; no separate `resolution` field |
| `gemini-3-pro-image-preview` | `aspect_ratio`, `resolution` (`1K`, `2K`, or `4K`) |
| `gemini-3.1-flash-image-preview` | `aspect_ratio`, `resolution` (`1K`, `2K`, or `4K`) |
| `grok-imagine-image-lite` | `aspect_ratio`, `resolution` (`1K` or `2K`) |
| `grok-imagine-image-2.0` | `aspect_ratio`, `resolution` (`1K` or `2K`) |

All requests use `n=1` and `response_format=b64_json`. The response may still contain either `data[0].b64_json` or `data[0].url`; the script handles both and honors `data[0].mime_type` when present.

## Image Editing

Image editing currently supports `gpt-image-2` only. The script sends `model`, `prompt`, `n`, `response_format`, `size`, `quality`, and `output_format` as multipart fields, plus one `image` file part. Input files must be a local PNG, JPEG, or WebP no larger than 50 MiB. The attachment is sent directly in the API request and is not copied to project storage.

Do not change a selected model name. The Skill submits the exact catalog name; it never normalizes or appends a suffix.

GPT Image 2 accepts these capability-listed sizes: `auto`, `1024x1024`, `1024x1536`, `1536x1024`, `1024x1792`, `1792x1024`, `2048x2048`, `2560x1440`, `1440x2560`, `3840x2160`, and `2160x3840`. The skill adds display-only K annotations for users; it submits the raw size value.

Both hardcoded Gemini models support these aspect ratios: `1:1`, `1:4`, `1:8`, `2:3`, `3:2`, `3:4`, `4:1`, `4:3`, `4:5`, `5:4`, `8:1`, `9:16`, `16:9`, and `21:9`. Their `resolution` is separately required and restricted to `1K`, `2K`, or `4K`.

Both hardcoded Grok models support these aspect ratios: `auto`, `1:1`, `16:9`, `9:16`, `4:3`, `3:4`, `3:2`, `2:3`, `2:1`, `1:2`, `19.5:9`, `9:19.5`, `20:9`, and `9:20`. Their `resolution` is separately required and restricted to `1K` or `2K`; the Coder API xAI adapter normalizes it to the lowercase upstream value.

## Error Handling

- `401` or `403`: the key is invalid, disabled, or lacks access.
- `404`: the selected model is not available to the key's group. Select another built-in model.
- Each request attempt waits no longer than 1000 seconds when the upstream has not returned a response.
- `408`, `409`, `429`, `5xx`, network failures, malformed JSON, timeouts, and `524` are retried at most three times in one user-approved round.
- `400`, `401`, `403`, and `404` are deterministic configuration failures and stop the round immediately.
- A failed image download or local output-save step never submits a duplicate generation request. The state is retained and the user is asked whether to continue after an exhausted round.

## Grok Video Contract

The separate `scripts/generate_video.py` command shares the private credential config and API base URL with the image workflow, but uses its own private workflow state. It submits once to `POST /v1/videos` (JSON for text-to-video, multipart with one `image` for image-to-video), checks `GET /v1/videos/{id}`, then downloads authorized `GET /v1/videos/{id}/content` as a local MP4. It does **not** call xAI's upstream `/v1/videos/generations` directly. Both `CODER_API_BASE_URL` and the API token must address the New API instance where the Grok video plugin is installed and the selected models are enabled.

Required submission fields are exact `model`, `seconds` (integer 1–15), and `resolution` (`480p` or `720p` for `grok-imagine-video`; additionally `1080p` for `grok-imagine-video-1.5`). An optional `prompt`, `aspect_ratio` (one of `1:1`, `16:9`, `9:16`, `4:3`, `3:4`, `3:2`, `2:3`), and explicit `generate_audio` boolean are supported. `prompt` is required for text-to-video, optional with a local image. Image-to-video sends at most one PNG/JPEG/WebP reference of 20 MiB and omits `aspect_ratio` when the source ratio should be retained. Other video plugins may use different parameters; the catalog here is specific to the locally inspected `xai-grok-video` plugin.

Creation must return a public task `id`. The script persists a `submitting` state before sending the request and the public `id` immediately after receiving a valid response. A network timeout, `429`, `5xx`, redirect, invalid response, or interruption can mean the upstream accepted and billed the request; the state must **not** be resubmitted. `--attach-task-id` can bind a public ID recovered from gateway task history to an uncertain state. `GET` and content downloads can be resumed on the same task; no response URL is followed, and the Bearer token is sent only to the configured API host. Downloads are capped at 512 MiB, checked for an MP4 header, and never overwrite an existing file. Local download failures leave the task state intact for a later `--poll`.
