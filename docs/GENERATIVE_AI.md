# IM19 Universal Generative AI — first integration phase

## Scope

This phase adds a provider-neutral, tenant-scoped generation control plane to IM19.
It is designed for the previously discussed pipeline:

1. Text prompt -> PNG/image
2. PNG asset -> 3D GLB asset
3. GLB asset -> motion/animation
4. 3D scene or generated motion -> MP4/video
5. Optional full-pipeline orchestration

IM19 is the authenticated MCP gateway and orchestrator. It is not itself a trained
foundation model, and this repository does not bundle model weights or GPU compute.
Inference runs in a separately isolated worker using operator-selected open-source
models and approved workflow profiles. The worker can be backed by ComfyUI, TRELLIS
or another compatible inference runtime, but each model adapter and workflow must
be installed, licensed and tested separately.

## Security boundary

- Only authenticated IM19 subjects can invoke generation tools.
- IM19 sends an HMAC-pseudonymized principal to the worker; raw MCP subject IDs are
  not forwarded.
- Worker URL and bearer token are operator configuration, never tool arguments.
- Only allowlisted profile IDs and the fixed stage enum are accepted.
- Input references are opaque asset IDs, not arbitrary URLs or filesystem paths.
- User-provided workflow JSON, shell commands, Python, model URLs and arbitrary
  callback URLs are never accepted.
- Generation submission and cancellation require explicit confirmation and pass
  through the existing per-subject quota and audit hooks.
- Worker capability, progress and asset responses are strictly validated; malformed
  worker output fails closed rather than being silently filtered.
- Worker errors are normalized; prompts, tokens and upstream exception bodies are
  not written to logs or returned to callers.

## Worker HTTP contract

The configured worker must implement:

- GET /v1/capabilities -> JSON with an array of supported stages
- POST /v1/jobs -> JSON containing an opaque job_id. Request body contains stage,
  allowlisted profile, prompt, input asset IDs, bounded parameters and an Idempotency-Key header.
- GET /v1/jobs/{job_id} -> status, optional progress, stage, output asset IDs and
  sanitized error code.
- POST /v1/jobs/{job_id}/cancel -> one of cancelled, cancel_requested, succeeded
  or failed. Cancellation is a request; a running model may not stop immediately.

Every request includes a bearer worker token, X-IM19-Principal (HMAC pseudonym)
and a random X-IM19-Request-ID. The worker must enforce tenant ownership on every
asset and job, idempotency-key deduplication, storage quotas, GPU admission, cancellation and retention.
The worker must never trust the principal header unless network ingress authenticates
IM19 and blocks direct public access.

## Configuration

Set these only in a secrets manager or protected deployment environment:

- IM19_GENERATIVE_ENABLED=true
- IM19_GENERATIVE_WORKER_URL=https://private-worker-origin
- IM19_GENERATIVE_WORKER_TOKEN=long-random-secret
- IM19_GENERATIVE_PROFILES=default,image-v1,3d-v1,motion-v1,video-v1

The worker should be private-network reachable, HTTPS protected, and independently
rate-limited. Use IM19_GENERATIVE_ENABLED=false until the worker is installed and
acceptance-tested. The generation tools return NOT_CONFIGURED when disabled.

## Acceptance gates before production

- Deploy an isolated worker and verify its ingress cannot be reached directly.
- Install exact model weights and pin immutable revisions and license metadata.
- Verify each stage separately with real sample assets (PNG, GLB, MP4).
- Confirm asset ownership, tenant isolation, job status access and signed downloads.
- Test GPU queue saturation, timeouts, retries, duplicate requests and cancellation.
- Add per-user GPU-minute / storage budgets and cost estimation before enabling paid GPU.
- Run malware/content checks on uploaded assets and validate output MIME, size and codecs.
- Benchmark latency, VRAM, output quality and failure rates for each model profile.
- Complete security, privacy, license and disaster-recovery review.

## Current status

This is an integration/control-plane increment. It does not prove that a model is
installed, a GPU is allocated, or that PNG-to-GLB/video generation works end-to-end.
Those claims require the worker implementation and real inference acceptance tests.
