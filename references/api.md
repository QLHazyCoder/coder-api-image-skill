# Coder API Image Contract

The script posts one JSON request to `POST /v1/images/generations` using `Authorization: Bearer $CODER_API_KEY`.

## Built-In Model Parameters

| Model | Request fields |
| --- | --- |
| `gpt-image-2` | `size`, `quality=auto`, `output_format=png` |
| `gemini-3.1-flash-image-1k` | `aspect_ratio`; no `resolution` |
| `gemini-3.1-flash-image-2k` | `aspect_ratio`; no `resolution` |
| `gemini-3.1-flash-image-4k` | `aspect_ratio`; no `resolution` |

All requests use `n=1` and `response_format=b64_json`. The response may still contain either `data[0].b64_json` or `data[0].url`; the script handles both and honors `data[0].mime_type` when present.

Do not change a selected model name. In particular, Gemini resolution suffixes are part of the upstream model identity.

## Error Handling

- `401` or `403`: the key is invalid, disabled, or lacks access.
- `404`: the selected model is not available to the key's group. Select another built-in model.
- `429` or `503`: report the upstream error; do not retry automatically.
- timeout or `524`: do not retry automatically because the provider may already have generated and charged for the image.
