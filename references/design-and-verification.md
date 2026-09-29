# Coder API image and video skill: design and verification

## Project overall analysis

This is a local Codex skill, not a New API plugin. The existing image entry point already owns the private key, explicit image-model catalog, generation/edit workflows, bounded retries, and output handling. The New API `xai-grok-video` plugin separately owns video parameter validation, upstream transformation, task billing, and the public `/v1/videos` protocol. The skill is a client of that public protocol; it must not duplicate the plugin's upstream xAI calls or replace the image workflow.

## Refactor plan

- Keep the skill directory and `coder-api-image` identifier so existing callers continue to work.
- Preserve `scripts/generate_image.py` behavior and its image regression tests.
- Add a video-only CLI and private, versioned video workflow state; reuse the existing key store and security reminder.
- Require explicit video model, duration, and resolution; implement text-to-video and optional one-image-to-video using the inspected Grok plugin's accepted fields.
- Treat POST as potentially billable: write `submitting` before POST, persist the public task ID when received, never automatically retry an uncertain submission. Allow manual task ID recovery, bounded GET polling, same-task download retries, and authenticated MP4 streaming with a byte cap.

## File structure design

| File | Role and change |
| --- | --- |
| `SKILL.md` | Existing skill trigger and user-facing image/video workflows; extended without replacing image instructions. |
| `agents/openai.yaml` | Existing skill presentation; broadened to image and video, retaining the stable identifier. |
| `scripts/generate_image.py` | Existing image generation/edit, key store, and shared helpers; left unchanged. |
| `scripts/generate_video.py` | New video model/parameter validation, video state machine, public API client, safe task polling and MP4 output. |
| `tests/test_generate_image.py` | Existing image compatibility tests; left unchanged. |
| `tests/test_generate_video.py` | New isolated mock-service tests for video and adverse network/file conditions; no chargeable calls. |
| `references/api.md` | Existing image API reference extended with the public video contract and failure policy. |
| `references/design-and-verification.md` | This architecture, file responsibilities, checklist, and phase review. |

## Development checklist

| Stage | Task | Status | Result |
| --- | --- | --- | --- |
| 1 | Inspect image skill, video plugin, public protocol, and existing image tests | complete | Source-level contract verified; no live key or paid generation used. |
| 2 | Preserve image CLI and add independent video client with shared credential config | complete | Original image source remains unchanged; explicit video model catalog and resumable task state added. |
| 3 | Add documentation, model restrictions, and separate video mock-service tests | complete | Video workflow and API reference documented; test cases avoid real media charges. |
| 4 | Run full image/video tests, skill validator, and final risk review | complete | All 36 unittest cases pass; skill validator passes; CLI video catalog lists the inspected model constraints. |

## Phase self-check record

- Phase 1: image status version and image model catalog remained untouched. The Grok plugin requires `seconds` 1-15, accepts model-specific `resolution`, and provides `/v1/videos` create/retrieve/content operations. The classic model is limited by the currently inspected plugin to 480p/720p; version 1.5 also accepts 1080p.
- Phase 2: video POST has no automatic retry; the state is written before the POST so a crash cannot silently trigger a new paid request on restart. The small window between a successful upstream POST and persisting the response ID still requires manual reconciliation from gateway task history; no client-only implementation can eliminate this without an upstream idempotency contract.
- Phase 3: reference images have a 20 MiB cap; output downloads have a 512 MiB cap, reject redirects to avoid bearer-token forwarding, require an MP4 signature, and use exclusive create so an existing output is never overwritten. Failed status queries and downloads retain the public task ID.
- Phase 4: corrected the exclusive-create collision cleanup so it cannot remove another process's output, and added a regression test. `python3 -m unittest discover -s tests -p 'test_*.py' -v` passes 36/36; `python3 /root/.codex/skills/.system/skill-creator/scripts/quick_validate.py /root/.codex/skills/coder-api-image` returns `Skill is valid!`; `python3 scripts/generate_video.py --list-models` lists both Grok models and their distinct resolutions.

## Final review conclusion

The original image implementation is unchanged and covered by its existing regression suite; the new video client is covered by mock-based success and failure scenarios. No structural or test failures were found in the final review. Live video generation, model availability on the configured key, and deployed plugin behavior are not verified by these mock-based tests; those require an explicitly authorized paid request to the target New API instance.
