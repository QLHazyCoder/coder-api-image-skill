# Coder API Image Contract

The script posts one JSON request to `POST /v1/images/generations` using an API key from `CODER_API_KEY` or the local private config created by `--configure`.

## Local Credential Storage

`python3 scripts/generate_image.py --configure` reads the key with hidden terminal input and writes `~/.config/coder-api-image/credentials.json` with mode `0600`. It performs no network validation and never stores a key in the Skill directory or repository.

Before configuring a key, tell the user to enable model limits and whitelist only the needed models. An IP allowlist is optional and appropriate only for a stable Codex egress IP. The standard image API cannot safely report whether those account-level restrictions are enabled, so the Skill only reminds the user.

## Built-In Model Parameters

| Model | Request fields |
| --- | --- |
| `gpt-image-2` | `size`, `quality=auto`, `output_format=png`; no separate `resolution` field |
| `gemini-3.1-flash-image-1k` | `aspect_ratio`; no `resolution` |
| `gemini-3.1-flash-image-2k` | `aspect_ratio`; no `resolution` |
| `gemini-3.1-flash-image-4k` | `aspect_ratio`; no `resolution` |

All requests use `n=1` and `response_format=b64_json`. The response may still contain either `data[0].b64_json` or `data[0].url`; the script handles both and honors `data[0].mime_type` when present.

Do not change a selected model name. In particular, Gemini resolution suffixes are part of the upstream model identity.

GPT Image 2 accepts these capability-listed sizes: `auto`, `1024x1024`, `1024x1536`, `1536x1024`, `1024x1792`, `1792x1024`, `2048x2048`, `2560x1440`, `1440x2560`, `3840x2160`, and `2160x3840`. The skill adds display-only K annotations for users; it submits the raw size value.

## Error Handling

- `401` or `403`: the key is invalid, disabled, or lacks access.
- `404`: the selected model is not available to the key's group. Select another built-in model.
- Each request attempt waits no longer than 120 seconds.
- `408`, `409`, `429`, `5xx`, network failures, malformed JSON, timeouts, and `524` are retried at most three times in one user-approved round.
- `400`, `401`, `403`, and `404` are deterministic configuration failures and stop the round immediately.
- A failed image download or local output-save step never submits a duplicate generation request. The state is retained and the user is asked whether to continue after an exhausted round.
