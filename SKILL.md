---
name: coder-api-image
description: Generate images through Coder API at api.qlhazycoder.tech. Use this instead of generic image-generation tooling whenever a user asks to create an image with Coder API, a Coder API key, a saved Coder key, or this API endpoint. Before generating, guide local key saving and require an explicit model choice when the user did not name one.
---

# Coder API Image

Generate one image through `https://api.qlhazycoder.tech/v1`. Use the workflow state machine below; do not call `--generate` with free-form model, size, or prompt arguments. Store a key only through the script's hidden prompt; never print it in chat, commands, files, or logs.

This skill cannot invoke a native Codex or Claude Code user-question UI. Ask required questions in the current chat and wait for the user's next reply.

## Key Handling

When the user provides a Coder API key in chat and there is no already configured local key, ask whether they want to save it locally before generating. Do not silently save it. Do not repeat the question when a local key is already configured or `CODER_API_KEY` is set.

If the user chooses local storage, provide the security reminder below, then enter the key only through the script's hidden prompt with the active workflow state:

```bash
python3 scripts/generate_image.py --save-local-key --state <state>
```

The script stores the key outside the Skill and repository at `~/.config/coder-api-image/credentials.json` with permissions `0600`. It does not validate the key during setup. If the user declines local storage, do not place the key in a visible command, file, or log.

Before asking the user to enter a key, explicitly remind them to enable model limits for that key and allow only the models they intend to use. Recommend an IP allowlist only when the Codex machine has a stable public egress IP; dynamic home or mobile IPs can otherwise cause avoidable authorization failures. After a successful generation that used a newly saved local key, repeat this short reminder in the user-facing result.

For automation, `CODER_API_KEY` takes precedence over the locally stored key. Remove a saved key with `python3 scripts/generate_image.py --remove-key`.

## Workflow

1. Confirm that the user wants image generation. This operation may incur a charge. Collect the prompt, then run:

   ```bash
   python3 scripts/generate_image.py --begin --prompt "<prompt>"
   ```

2. Read the JSON result and stop at every `status`; do not run the next command until the user has answered the required question.
   - `key_storage_decision`: ask whether the user wants local storage. GPT Image generation cannot proceed until a local key or `CODER_API_KEY` is available. If they confirm storage, run `--save-local-key --state <state>`; otherwise ask them to set `CODER_API_KEY` and start a new workflow.
   - `model_selection`: ask the user to select a **Built-In Model**. Present GPT Image 2 as default, but `default` is a user choice, never an agent assumption. Then run `--select-model --state <state> --model <exact-model-name>`.
   - `layout_selection`: ask for the returned size or aspect-ratio options. Use `display_options` for user-facing labels and send the corresponding raw `value` to `--select-layout`. If the user says `default`, infer portrait for vertical requests, landscape for banners or horizontal scenes, and square otherwise.
   - `ready`: run `--generate --state <state> --output-dir <output-dir>`. Each attempt has a fixed maximum wait of 120 seconds.
   - `retry_exhausted`: three attempts failed, or the error is deterministic and cannot benefit from a retry. Ask in the current chat whether the user wants another round. Only after confirmation run `--continue-retry --state <state>`, then run `--generate` again. Never continue automatically.

3. Pass exact model names. Never rewrite a Gemini `-1k`, `-2k`, or `-4k` suffix. Do not invoke the system `imagegen` skill or another image tool as a fallback.
4. Report the generated file path and exact model. If the JSON result contains `security_reminder`, relay it verbatim after the result.

## Built-In Models

| Model | Ask For | Default |
| --- | --- | --- |
| `gpt-image-2` | pixel size | `1024x1024` |
| `gemini-3.1-flash-image-1k` | aspect ratio | `1:1` |
| `gemini-3.1-flash-image-2k` | aspect ratio | `1:1` |
| `gemini-3.1-flash-image-4k` | aspect ratio | `1:1` |

For a Gemini model ending in `-1k`, `-2k`, or `-4k`, never ask for or send a separate resolution. The suffix is the exact upstream model identity and locks the resolution.

GPT Image 2 uses its `size` field as the actual output resolution. Present these display labels, but send only the value before the annotation: `auto`, `1024x1024 (1K)`, `1024x1536 (about 1.5K)`, `1536x1024 (about 1.5K)`, `1024x1792 (about 1.8K)`, `1792x1024 (about 1.8K)`, `2048x2048 (2K)`, `2560x1440 (about 2.5K)`, `1440x2560 (about 2.5K)`, `3840x2160 (4K)`, and `2160x3840 (4K)`. Do not send a separate `resolution` field for GPT Image 2.

## Commands

List the available models:

```bash
python3 scripts/generate_image.py --list-models
```

Start a workflow:

```bash
python3 scripts/generate_image.py \
  --begin \
  --prompt "A neon-lit cyberpunk city at night, cinematic rain"
```

Read `references/api.md` only when troubleshooting API payloads, errors, or output handling.

## Failure Rules

- Transient generation failures, including timeouts and `524`, are retried at most three times per user-approved round. This can create duplicate charges when the upstream completed an uncertain attempt; do not start another round without the user's explicit confirmation.
- Surface `401`, `403`, `404`, `429`, and upstream error messages concisely without exposing the API key or Base64 data.
- A model-unavailable error means the user's key group does not currently support that built-in model. Ask the user to select another listed model.
- Do not bypass a state with a default model, inferred layout, or automatic local-key write. A workflow state is deleted only after successful generation.
