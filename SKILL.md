---
name: coder-api-image
description: Generate or edit images and generate text-to-video or image-to-video through Coder API at api.qlhazycoder.tech. Use when a user requests image or video generation with Coder API, a Coder API key, a saved Coder key, or this API endpoint, including Grok, ZeeQi Seedance, and MiniMax H3 video models. Save supplied keys privately, require explicit model selection, and require public image URLs for ZeeQi image-to-video.
---

# Coder API Image and Video

Generate or edit an image, or create a video, through `https://api.qlhazycoder.tech/v1`. Use `scripts/generate_image.py` for images and `scripts/generate_video.py` for videos. Both use the same private API key, but their workflow states and model catalogs are separate. Never pass free-form model, size, or prompt arguments to a submit command.

This skill cannot invoke a native Codex or Claude Code user-question UI. Ask required questions in the current chat and wait for the user's next reply.

## Key Handling

When the user provides a Coder API key in chat, save it locally automatically and continue the workflow. Do not ask the user to paste the same key again. A newly supplied key replaces the previous local Coder API key.

After `--begin` returns `key_storage_decision`, pass the key supplied in chat directly to the save command. Do not ask the user to paste it again:

```bash
python3 scripts/generate_image.py --save-local-key --state <state> --api-key "<key-from-chat>"
```

For video states, use the corresponding video command:

```bash
python3 scripts/generate_video.py --save-local-key --state <video-state> --api-key "<key-from-chat>"
```

The script stores the Coder API key outside the Skill and repository at `~/.config/coder-api-image/credentials.json` with permissions `0600`. For local ZeeQi image uploads, `scripts/generate_video.py --save-image-upload-key` securely prompts for the user's own `sk-` New API key and stores it in the separate `new_api_image_upload_key` field in that same private file. The two keys are never interchangeable.

After saving a key, remind the user to enable model limits for that key and allow only the models they intend to use. Recommend an IP allowlist only when the Codex machine has a stable public egress IP; dynamic home or mobile IPs can otherwise cause avoidable authorization failures. Repeat this short reminder in the user-facing result.

For automation, `CODER_API_KEY` takes precedence over the locally stored key. Remove a saved key with `python3 scripts/generate_image.py --remove-key`.

## Image Workflow

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
   - `model_selection`: ask one consolidated question for the model and its layout: GPT Image 2 needs a pixel size; Gemini and Grok need both aspect ratio and a supported resolution returned by the catalog. Present GPT Image 2 as default, but `default` is a user choice, never an agent assumption. If the user gives all fields, run exactly one `--select-configuration` command and continue directly to `ready`.

     ```bash
     python3 scripts/generate_image.py --select-configuration --state <state> --model gpt-image-2 --size <size>
     python3 scripts/generate_image.py --select-configuration --state <state> --model <aspect-ratio-model> --aspect-ratio <ratio> --resolution <resolution>
     ```

     If the user says `default`, use `gpt-image-2`; infer square as `1024x1024`, portrait as `1024x1536`, and landscape as `1536x1024`. For Gemini or Grok, infer the aspect ratio the same way and use the selected model's returned default resolution. Image edit workflows support only `gpt-image-2`.
   - `layout_selection`: this is fallback-only. Use it only when the user's consolidated answer chose a model but omitted required layout fields. Ask only for the missing size, aspect ratio, or resolution, then run `--select-layout`.
   - `ready`: run `--generate --state <state> --output-dir <output-dir>`. Each attempt waits up to 1000 seconds without an upstream response.
   - `retry_exhausted`: three attempts failed, or the error is deterministic and cannot benefit from a retry. Ask in the current chat whether the user wants another round. Only after confirmation run `--continue-retry --state <state>`, then run `--generate` again. Never continue automatically.

   After a complete configuration response, only ask another question when the prompt itself is genuinely ambiguous or needs revision. Do not re-ask known model, size, aspect-ratio, or resolution values.

3. Pass exact model names. Never rewrite, normalize, or append a model-name suffix. Do not invoke the system `imagegen` skill or another image tool as a fallback.
4. Report the generated or edited file path and exact model. If the JSON result contains `security_reminder`, relay it verbatim after the result.

## Built-In Models

| Model | Ask For | Default |
| --- | --- | --- |
| `gpt-image-2` | pixel size | `1024x1024` |
| `gpt-image-2.5` | pixel size | `1024x1024` |
| `gpt-image-2.5-flare` | pixel size | `1024x1024` |
| `gpt-image-2.5-sunburst` | pixel size | `1024x1024` |
| `gemini-3-pro-image-preview` | aspect ratio and resolution | `1:1`, `1K` |
| `gemini-3.1-flash-image-preview` | aspect ratio and resolution | `1:1`, `1K` |
| `grok-imagine-image-lite` | aspect ratio and resolution | `auto`, `1K` |
| `grok-imagine-image-2.0` | aspect ratio and resolution | `auto`, `1K` |

The two Gemini models accept these aspect ratios: `1:1`, `1:4`, `1:8`, `2:3`, `3:2`, `3:4`, `4:1`, `4:3`, `4:5`, `5:4`, `8:1`, `9:16`, `16:9`, and `21:9`. They also require one of `1K`, `2K`, or `4K` as a separate `resolution` request field. They do not support image editing through this skill.

The two Grok models accept `auto`, `1:1`, `16:9`, `9:16`, `4:3`, `3:4`, `3:2`, `2:3`, `2:1`, `1:2`, `19.5:9`, `9:19.5`, `20:9`, or `9:20` as `aspect_ratio`, plus `1K` or `2K` as `resolution`. They do not support image editing through this skill.

GPT Image 2 and GPT Image 2.5 variants use their `size` field as the actual output resolution. Present these display labels, but send only the value before the annotation: `auto`, `1024x1024 (1K)`, `1024x1536 (about 1.5K)`, `1536x1024 (about 1.5K)`, `1024x1792 (about 1.8K)`, `1792x1024 (about 1.8K)`, `2048x2048 (2K)`, `2560x1440 (about 2.5K)`, `1440x2560 (about 2.5K)`, `3840x2160 (4K)`, and `2160x3840 (4K)`. Do not send a separate `resolution` field for these models.

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

## Video Workflow

The video client uses the public `POST /v1/videos`, `GET /v1/videos/{id}`, and `GET /v1/videos/{id}/content` endpoints. It does not call an upstream vendor endpoint directly. Video generation can incur charges. Do not submit until the user has requested generation and chosen an exact model, duration, and resolution. A previous request to generate an image does not authorize creating a video.

For ZeeQi local image-to-video, configure the New API image-upload key before submitting:

```bash
python3 scripts/generate_video.py --save-image-upload-key
```

The key is read only from the private local config and sent as `Authorization: Bearer` to the fixed `https://coderapi.vip/image-upload/upload` endpoint. The request always uses one multipart `file`; there is no configurable image host, upload token, or upload URL. The response must contain a valid `https://coderapi.vip/image-upload/m_<ULID>` URL. Redirects and URLs from other hosts are rejected. Upload or validation failure stops before the billable video request. `--remove-image-upload-key` removes only this credential and preserves the Coder API key.

The current explicit video catalog is:

| Model | Resolution | Duration accepted by the installed plugin | Image-to-video input |
| --- | --- | --- | --- |
| `grok-imagine-video` | `480p`, `720p` | 1–15 seconds | local PNG/JPEG/WebP or public URL |
| `grok-imagine-video-1.5` | `480p`, `720p`, `1080p` | 1–15 seconds | local PNG/JPEG/WebP or public URL |
| `seedance-2.0` | `480p`, `720p` | 1–3600 seconds | public URL or local image uploaded to the Coder API image library |
| `seedance-2.5` | `480p`, `720p`, `1080p` | 1–3600 seconds | public URL or local image uploaded to the Coder API image library |
| `seedance-2.0-fast-offical` | `480p`, `720p` | 1–3600 seconds | public URL or local image uploaded to the Coder API image library |
| `seedance-2.0-mini-offical` | `480p`, `720p` | 1–3600 seconds | public URL or local image uploaded to the Coder API image library |
| `seedance-2.0-offical` | `480p`, `720p`, `1080p` | 1–3600 seconds | public URL or local image uploaded to the Coder API image library |
| `H3` | `720p` | 4–15 seconds | up to 9 public image URLs and 3 public audio URLs; local images are uploaded first |

The five `seedance` names above and `H3` are the only ZeeQi models implemented in this skill. Preserve the provider's exact `offical` spelling; do not rewrite it to `official`, invent aliases, or add removed models such as `wan3.0-video` or `seedance-2.5-once`.

1. Start a video state with a prompt, a local reference image, or a public reference image URL:

   ```bash
   python3 scripts/generate_video.py --begin --prompt "A short shot of a paper boat on a rainy street"
   python3 scripts/generate_video.py --begin --prompt "Animate the street scene" --image "/absolute/path/reference.png"
   python3 scripts/generate_video.py --begin --prompt "Animate the product photo" --image-url "https://images.example/product.png"
   ```

`--image` is sent directly as multipart for Grok. For ZeeQi, local `--image` values are uploaded to the fixed image host first; the returned public URLs are then sent as JSON. `--image-url` skips that upload and may be repeated for H3 (up to 9 images). H3 also accepts up to 3 repeated public `--audio-url` values and sends them as `reference_audio_urls`. Do not copy a local reference image to project storage or pretend a local filesystem path is a public URL. The installed ZeeQi protocol requires a non-empty prompt even when reference URLs are supplied. If the New API image-upload key is not configured, submission stops before the billable video request.

2. If the JSON status is `key_storage_decision`, save the key supplied in chat with the video `--save-local-key` command above. If no key was supplied, ask for one. For `model_selection`, ask a single question for all missing settings: exact model, seconds, and resolution. Use the selected model's duration range and resolution list from the catalog; never assume a model or silently downgrade a resolution. For the two Grok models, the request field is `seconds`; for the six ZeeQi models, the client converts the same CLI value to the upstream-compatible `duration` field. H3 accepts 4–15 seconds at 720p and aspect ratios `9:16`, `16:9`, `4:3`, `3:4`, and `1:1`. Offer optional aspect ratio and audio generation only when relevant and only preserve an explicitly chosen audio preference.

   ```bash
   python3 scripts/generate_video.py --list-models
   python3 scripts/generate_video.py --select-configuration --state <video-state> --model grok-imagine-video-1.5 --seconds 5 --resolution 480p --aspect-ratio 16:9
   python3 scripts/generate_video.py --select-configuration --state <video-state> --model seedance-2.5 --seconds 8 --resolution 1080p --aspect-ratio 16:9
   ```

   Grok validates its known aspect-ratio list. ZeeQi passes a non-empty aspect-ratio string through its JSON contract; do not apply Grok-only resolution or image-upload assumptions to a ZeeQi model.

3. When `ready`, call `--submit --state <video-state>` once. Save the returned state path and public task ID; submission may incur a charge. Use `--poll` to query that ID and download its MP4 when completed. Each poll call has a bounded wait (120 seconds by default); when it returns `in_progress`, call `--poll` again with the same state. Use `--max-wait 0` to check once without waiting. Specify `--output-dir` on the poll step.

   ```bash
   python3 scripts/generate_video.py --submit --state <video-state>
   python3 scripts/generate_video.py --poll --state <video-state> --output-dir /absolute/output/directory
   ```

4. A failed status query or content download retains the task ID: retry `--poll`, never `--submit`. An uncertain or interrupted submission remains `submitting` or `submission_uncertain` and is **not** retried. Inspect the gateway task history first; if the public task ID can be recovered, run `--attach-task-id --state <video-state> --task-id <public-id>` and then `--poll`. If no ID can be recovered, tell the user the charge is uncertain and request fresh authorization before starting any new workflow. Never reuse `--submit` to recover an uncertain request.

5. Report the local MP4 path, exact model, and task ID. If the result contains `security_reminder`, relay it verbatim. Do not disclose the key or upstream download URLs. Other video adapters require their own validated model and parameter catalog before being added here.

## Failure Rules

- Transient generation failures, including timeouts and `524`, are retried at most three times per user-approved round. This can create duplicate charges when the upstream completed an uncertain attempt; do not start another round without the user's explicit confirmation.
- Surface `401`, `403`, `404`, `429`, and upstream error messages concisely without exposing the API key or Base64 data.
- A model-unavailable error means the user's key group does not currently support that built-in model. Ask the user to select another listed model.
- Do not bypass a state with a default model or inferred layout. A workflow state is deleted only after successful generation.
- Video creation is not automatically retried, including on a timeout, `429`, or `5xx`: the upstream may have accepted the paid job. Polling and same-task content retrieval are safe to retry. The video state is deleted only after a verified MP4 has been downloaded.
