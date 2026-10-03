# Coder API image and video skill: design and verification

## Project overall analysis

This is a local Codex skill, not a New API plugin. The existing image entry point owns the private key, explicit image-model catalog, generation/edit workflows, bounded retries, and output handling. Video plugins separately own model validation, upstream transformation, task billing, and the public `/v1/videos` protocol. The skill is a client of that public protocol; it must not duplicate xAI or ZeeQi upstream calls or replace the image workflow.

## Refactor plan

- Keep the skill directory and `coder-api-image` identifier so existing callers continue to work.
- Preserve `scripts/generate_image.py` behavior and its image regression tests.
- Add a video-only CLI and private, versioned video workflow state; reuse the existing key store and security reminder.
- Require explicit video model, duration, and resolution; keep model-specific adapters for Grok and the four installed ZeeQi Seedance models. Use a local multipart image only for Grok and a public image URL for ZeeQi.
- Treat POST as potentially billable: write `submitting` before POST, persist the public task ID when received, never automatically retry an uncertain submission. Allow manual task ID recovery, bounded GET polling, same-task download retries, and authenticated MP4 streaming with a byte cap.

## File structure design

| File | Role and change |
| --- | --- |
| `SKILL.md` | Existing skill trigger and user-facing image/video workflows; extended without replacing image instructions. |
| `agents/openai.yaml` | Existing skill presentation; broadened to image and video, retaining the stable identifier. |
| `scripts/generate_image.py` | Existing image generation/edit, key store, and shared helpers; left unchanged. |
| `scripts/generate_video.py` | Video model/parameter validation, Grok/ZeeQi request adapters, video state machine, public API client, safe task polling and MP4 output. |
| `tests/test_generate_image.py` | Existing image compatibility tests; left unchanged. |
| `tests/test_generate_video.py` | New isolated mock-service tests for video and adverse network/file conditions; no chargeable calls. |
| `references/api.md` | Existing image API reference extended with the public video contract and failure policy. |
| `references/design-and-verification.md` | This architecture, file responsibilities, checklist, and phase review. |

## Development checklist

| Stage | Task | Status | Result |
| --- | --- | --- | --- |
| 1 | Inspect image skill, Grok/ZeeQi video plugins, public protocol, and existing image tests | complete | Source-level contracts verified; no live key or paid generation used. |
| 2 | Preserve image CLI and add independent video client with shared credential config | complete | Original image source remains unchanged; six-model catalog and resumable task state retained. |
| 3 | Add adapter-specific documentation, model restrictions, and mock-service tests | complete | ZeeQi JSON/duration/public-URL rules are documented and tested without real media charges. |
| 4 | Run full image/video tests, skill validator, and final risk review | complete | Full suite passes 39/39; skill validator passes; the catalog lists both Grok models and the four supported ZeeQi models with model-specific resolutions. |

## Phase self-check record

- Phase 1: image state/version and image model catalog remained untouched. Grok requires `seconds` 1-15; the classic model is limited to 480p/720p and version 1.5 also accepts 1080p. ZeeQi exposes four models, JSON-only submission, `duration` 1-3600, 480p/720p for the first three, and 480p/720p/1080p for `seedance-2.5`.
- Phase 2: video POST has no automatic retry; the state is written before the POST so a crash cannot silently trigger a new paid request on restart. The small window between a successful upstream POST and persisting the response ID still requires manual reconciliation from gateway task history; no client-only implementation can eliminate this without an upstream idempotency contract.
- Phase 3: local reference images have a 20 MiB cap; ZeeQi image-to-video requires a validated public HTTP(S) URL and a non-empty prompt; output downloads have a 512 MiB cap, reject redirects to avoid bearer-token forwarding, require an MP4 signature, and use exclusive create so an existing output is never overwritten. Failed status queries and downloads retain the public task ID.
- Phase 4: added four ZeeQi model profiles, model-specific duration/resolution validation, JSON `duration`/`input_reference` request construction, public URL validation, and regression coverage. The complete image/video suite passes 39/39 and `quick_validate.py` returns `Skill is valid!`.

## Final review conclusion

The original image implementation is unchanged and remains covered by its existing regression suite. The video client now has separate Grok and ZeeQi request behavior, including the public-image constraint for ZeeQi, and is covered by mock-based success and failure scenarios. Live video generation, model availability on the configured key, provider-side duration limits beyond the installed plugin contract, and deployed plugin behavior are not verified by these tests; those require an explicitly authorized request to the target New API instance.
