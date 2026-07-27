---
name: coder-api-image
description: Generate or edit images through Coder API at api.qlhazycoder.tech. Use this instead of generic image-generation tooling whenever a user asks to create, transform, or edit an image with Coder API, a Coder API key, a saved Coder key, an attached image, or this API endpoint. Before generating, guide local key saving and require an explicit model choice when the user did not name one.
---

# Coder API Image

Generate or edit one image through `https://api.qlhazycoder.tech/v1`. Use the workflow state machine below; do not call `--generate` with free-form model, size, or prompt arguments.

This skill cannot invoke a native Codex or Claude Code user-question UI. Ask required questions in the current chat and wait for the user's next reply.

## Key Handling

When the user provides a Coder API key in chat, save it locally automatically and continue the workflow. Do not ask the user to paste the same key again. A newly supplied key replaces the previous local Coder API key.

After `--begin` returns `key_storage_decision`, pass the key supplied in chat directly to the save command. Do not ask the user to paste it again:

```bash
python3 scripts/generate_image.py --save-local-key --state <state> --api-key "<key-from-chat>"
```

The script stores the key outside the Skill and repository at `~/.config/coder-api-image/credentials.json` with permissions `0600`. It does not validate the key during setup.

After saving a key, remind the user to enable model limits for that key and allow only the models they intend to use. Recommend an IP allowlist only when the Codex machine has a stable public egress IP; dynamic home or mobile IPs can otherwise cause avoidable authorization failures. Repeat this short reminder in the user-facing result.

For automation, `CODER_API_KEY` takes precedence over the locally stored key. Remove a saved key with `python3 scripts/generate_image.py --remove-key`.

## Workflow

1. Confirm that the user wants image generation or editing. This operation may incur a charge. Collect the prompt, then run:

   ```bash
   python3 scripts/generate_image.py --begin --prompt "<prompt>"
   ```

   For an attached reference image, use its local attachment path and start an edit workflow:

   ```bash
   python3 scripts/generate_image.py --begin --prompt "<edit instruction>" --image "<attachment-path>"
   ```

   Accept only a local PNG, JPEG, or WebP attachment no larger than 50 MiB. Submit it directly in the API request; do not copy it to project storage or expose its contents in chat.

2. Read the JSON result and collect all known image settings in one user question. Do not ask for model and layout in separate turns when the user can answer both at once.
   - `key_storage_decision`: when the user provided a key in chat, save it automatically with `--save-local-key --state <state> --api-key "<key-from-chat>"`, then continue from the returned state. Do not request confirmation or a second paste. If no key was provided, ask for one.
   - `model_selection`: ask one consolidated question for the model and its layout: GPT Image 2 needs a pixel size; Gemini needs both aspect ratio and `1K`/`2K`/`4K` resolution. Present GPT Image 2 as default, but `default` is a user choice, never an agent assumption. If the user gives all fields, run exactly one `--select-configuration` command and continue directly to `ready`.

     ```bash
     python3 scripts/generate_image.py --select-configuration --state <state> --model gpt-image-2 --size <size>
     python3 scripts/generate_image.py --select-configuration --state <state> --model <gemini-model> --aspect-ratio <ratio> --resolution <1K|2K|4K>
     ```

     If the user says `default`, use `gpt-image-2`; infer square as `1024x1024`, portrait as `1024x1536`, and landscape as `1536x1024`. For Gemini, infer the aspect ratio the same way and use its returned default resolution. Image edit workflows support only `gpt-image-2`.
   - `layout_selection`: this is fallback-only. Use it only when the user's consolidated answer chose a model but omitted required layout fields. Ask only for the missing size, aspect ratio, or resolution, then run `--select-layout`.
   - `ready`: run `--generate --state <state> --output-dir <output-dir>`. Each attempt has a fixed maximum wait of 120 seconds.
   - `retry_exhausted`: three attempts failed, or the error is deterministic and cannot benefit from a retry. Ask in the current chat whether the user wants another round. Only after confirmation run `--continue-retry --state <state>`, then run `--generate` again. Never continue automatically.

   After a complete configuration response, only ask another question when the prompt itself is genuinely ambiguous or needs revision. Do not re-ask known model, size, aspect-ratio, or resolution values.

3. Pass exact model names. Never rewrite, normalize, or append a model-name suffix. Do not invoke the system `imagegen` skill or another image tool as a fallback.
4. Report the generated or edited file path and exact model. If the JSON result contains `security_reminder`, relay it verbatim after the result.

## Built-In Models

| Model | Ask For | Default |
| --- | --- | --- |
| `gpt-image-2` | pixel size | `1024x1024` |
| `gemini-3-pro-image-preview` | aspect ratio and resolution | `1:1`, `1K` |
| `gemini-3.1-flash-image-preview` | aspect ratio and resolution | `1:1`, `1K` |

The two Gemini models accept these aspect ratios: `1:1`, `1:4`, `1:8`, `2:3`, `3:2`, `3:4`, `4:1`, `4:3`, `4:5`, `5:4`, `8:1`, `9:16`, `16:9`, and `21:9`. They also require one of `1K`, `2K`, or `4K` as a separate `resolution` request field. They do not support image editing through this skill.

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
- Do not bypass a state with a default model or inferred layout. A workflow state is deleted only after successful generation.
