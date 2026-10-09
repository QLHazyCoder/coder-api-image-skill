# Coder API image and video skill: design and verification

## Project overall analysis

This is a local Codex skill, not a New API plugin. The existing image entry point owns the private key, explicit image-model catalog, generation/edit workflows, bounded retries, and output handling. Video plugins separately own model validation, upstream transformation, task billing, and the public `/v1/videos` protocol. The skill is a client of that public protocol; it must not duplicate xAI or ZeeQi upstream calls or replace the image workflow.

## Refactor plan

- Keep the skill directory and `coder-api-image` identifier so existing callers continue to work.
- Preserve `scripts/generate_image.py` behavior and its image regression tests.
- Add a video-only CLI and private, versioned video workflow state; reuse the existing key store and security reminder.
- Require explicit video model, duration, and resolution; keep model-specific adapters for Grok, the five installed ZeeQi Seedance models, and MiniMax H3. Use a local multipart image directly for Grok; for ZeeQi/H3, pass public URLs directly or upload local images to the fixed Coder API image library using the user's New API key stored separately from the Coder API key.
- Treat POST as potentially billable: write `submitting` before POST, persist the public task ID when received, never automatically retry an uncertain submission. Allow manual task ID recovery, bounded GET polling, same-task download retries, and authenticated MP4 streaming with a byte cap.

## File structure design

| File | Role and change |
| --- | --- |
| `SKILL.md` | Existing skill trigger and user-facing image/video workflows; extended without replacing image instructions. |
| `agents/openai.yaml` | Existing skill presentation; broadened to image and video, retaining the stable identifier. |
| `scripts/generate_image.py` | Existing image generation/edit and shared key store; Coder API key updates now preserve the separate New API upload key. |
| `scripts/generate_video.py` | Video model/parameter validation, Grok/ZeeQi/H3 request adapters, multi-image upload, private New API key lifecycle, video state machine, public API client, safe task polling and MP4 output. |
| `tests/test_generate_image.py` | Image compatibility tests plus shared credential coexistence coverage. |
| `tests/test_generate_video.py` | New isolated mock-service tests for video and adverse network/file conditions; no chargeable calls. |
| `references/api.md` | Existing image API reference extended with the public video contract and failure policy. |
| `references/design-and-verification.md` | This architecture, file responsibilities, checklist, and phase review. |

## Development checklist

| Stage | Task | Status | Result |
| --- | --- | --- | --- |
| 1 | Inspect image skill, Grok/ZeeQi video plugins, public protocol, and existing image tests | complete | Source-level contracts verified; no live key or paid generation used. |
| 2 | Preserve image CLI and add independent video client with shared credential config | complete | Image-generation behavior and six-model catalog retained; only shared key-update/removal logic was extended. |
| 3 | Add adapter-specific documentation, model restrictions, fixed image-library upload, and mock-service tests | complete | ZeeQi/H3 JSON, duration, public-URL, multi-image and reference-audio rules are documented and tested without real media charges. |
| 4 | Run full image/video tests, syntax checks, and final risk review | complete | Image/video regression suite and syntax checks pass; the repository has no separate `quick_validate.py` validator. |
| 5 | Live image-host and lowest-cost Seedance smoke test (2026-10-06) | blocked upstream | Real upload and byte-for-byte public read succeeded; one `seedance-2.0-mini` 480p/1s video POST returned 503 with no routing candidates. No task ID was created; the client retained `submission_uncertain` and did not retry. |

## Phase self-check record

- Phase 1: image state/version and image model catalog remained untouched. Grok requires `seconds` 1-15; the classic model is limited to 480p/720p and version 1.5 also accepts 1080p. ZeeQi exposes five Seedance names plus H3; all use JSON submission, with Seedance `duration` 1-3600 and H3 `duration` 4-15/720p.
- Phase 2: video POST has no automatic retry; the state is written before the POST so a crash cannot silently trigger a new paid request on restart. The small window between a successful upstream POST and persisting the response ID still requires manual reconciliation from gateway task history; no client-only implementation can eliminate this without an upstream idempotency contract.
- Phase 3: local reference images have a 20 MiB cap; ZeeQi image-to-video requires a validated public HTTP(S) URL and a non-empty prompt, so local images are uploaded only to `https://coderapi.vip/image-upload/upload` with the user's New API key stored in the private credential file's separate `new_api_image_upload_key` field. Upload redirects and non-canonical response URLs are rejected. Output downloads have a 512 MiB cap, reject redirects to avoid bearer-token forwarding, require an MP4 signature, and use exclusive create so an existing output is never overwritten. Failed status queries and downloads retain the public task ID.
- Phase 4: added five Seedance profiles plus H3, model-specific duration/resolution/aspect/reference-count validation, JSON `duration`/`input_reference`/`reference_image_urls`/`reference_audio_urls` construction, strict hosted-image URL validation, private New API key setup/removal, and regression coverage. Verification uses the complete unittest suites, Python bytecode compilation, and `git diff --check`; this repository does not ship a separate validator script.
- Phase 5: Cloudflare blocked urllib's default User-Agent on the public image host (403 before authentication). An explicit `CoderAPIImageSkill/1.0` agent returned the expected New API response; the upload client and regression assertion now use it. The production Caddy bind mount held an older Caddyfile inode; a validated host config differing only by the image route was hot-reloaded, but the container must later be recreated to retain this route after a restart. The public image response matched the generated PNG byte for byte. One video POST reached New API, which refunded its local 0.015 precharge after six channel-117 503 responses; a read-only task-table check found no task for that user and time window. Upstream billing outside New API remains unverified, so the uncertain workflow must not be resubmitted without fresh authorization.

## Final review conclusion

The original image implementation remains covered by its existing regression suite. The video client has separate Grok and ZeeQi request behavior, and live public image upload was verified. Video completion remains unverified because the selected upstream currently has no available routing candidate. Provider-side billing and duration limits beyond the installed plugin contract are not established by this failed smoke test; resolve upstream routing and obtain fresh authorization before creating another paid workflow.
