---
name: coder-api-image
description: Generate images through Coder API for users who provide a Coder API key. Use when a user explicitly asks to create, draw, or generate an image, illustration, poster, icon, wallpaper, or other visual asset with Coder API.
---

# Coder API Image

Generate one image through `https://api.qlhazycoder.tech/v1` with the user's `CODER_API_KEY` environment variable. Never ask for or print the key in chat, commands, files, or logs.

## Workflow

1. Confirm that the user wants image generation. This operation may incur a charge.
2. Collect the image prompt. Preserve explicit visual constraints from the user.
3. If no model was supplied, ask the user to choose from the built-in models. Present GPT Image 2 as the default.
4. Ask for the model-specific layout setting:
   - GPT Image 2: `1024x1024`, `1536x1024`, or `1024x1536`.
   - Gemini: `1:1`, `16:9`, or `9:16` aspect ratio.
5. If the user says `default`, infer the layout from the request:
   - portrait, phone wallpaper, cover, or vertical poster: portrait;
   - banner, desktop wallpaper, landscape scene, or horizontal poster: landscape;
   - otherwise: square.
6. Run `scripts/generate_image.py`. Pass the exact chosen model name. Do not rewrite its spelling or resolution suffix.
7. Report the generated file path. Do not paste Base64 image data into the response.

## Built-In Models

| Model | Ask For | Default |
| --- | --- | --- |
| `gpt-image-2` | pixel size | `1024x1024` |
| `gemini-3.1-flash-image-1k` | aspect ratio | `1:1` |
| `gemini-3.1-flash-image-2k` | aspect ratio | `1:1` |
| `gemini-3.1-flash-image-4k` | aspect ratio | `1:1` |

For a Gemini model ending in `-1k`, `-2k`, or `-4k`, never ask for or send a separate resolution. The suffix is the exact upstream model identity and locks the resolution.

## Commands

List the available models:

```bash
python3 scripts/generate_image.py --list-models
```

Generate with GPT Image 2:

```bash
python3 scripts/generate_image.py \
  --model gpt-image-2 \
  --size 1536x1024 \
  --prompt "A neon-lit cyberpunk city at night, cinematic rain"
```

Generate with Gemini:

```bash
python3 scripts/generate_image.py \
  --model gemini-3.1-flash-image-2k \
  --aspect-ratio 16:9 \
  --prompt "A quiet alpine lake at sunrise, editorial travel photography"
```

Read `references/api.md` only when troubleshooting API payloads, errors, or output handling.

## Failure Rules

- Do not retry a timeout, `524`, or any uncertain request automatically. A generation may already have completed and been charged.
- Surface `401`, `403`, `404`, `429`, and upstream error messages concisely without exposing the API key or Base64 data.
- A model-unavailable error means the user's key group does not currently support that built-in model. Ask the user to select another listed model.
