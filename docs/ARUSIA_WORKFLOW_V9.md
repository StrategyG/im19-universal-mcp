# ARUSIA v9 — IM19 workflow architecture (incremental implementation)

**Scope of this PR: durable planning + shot-stage orchestration contract.**
The eight logical subsystems in the ARUSIA v9 design are preserved, but the
code does not yet render episodes or generate individual frames.

## Workflow DAG

For every shot, ten sequential tasks are persisted. Three additional
tasks are created once per episode.

| Scope | Stage | Executor |
|---|---|---|
| Shot | SCRIPT_BREAKDOWN | Worker |
| Shot | ASSET_LOCK | Human; validates approved master assets |
| Shot | STORYBOARD | Worker |
| Shot | CREATIVE_APPROVAL | Human |
| Shot | POSE_CAMERA | Worker |
| Shot | IMAGE_GENERATION | Worker |
| Shot | FRAME_QC | Human |
| Shot | ANIMATION | Worker |
| Shot | AUDIO | Worker |
| Shot | SHOT_QC | Human |
| Episode | EPISODE_COMPOSITE | Worker; only after ALL shot QC gates pass |
| Episode | EPISODE_FINAL_QC | Human |
| Episode | DELIVERY_PACKAGE | Worker |

The episode composite has an explicit fan-in barrier and remains BLOCKED until
every shot has passed SHOT_QC. Completion must reference a real hash-verified
local artifact. That verifies bytes, not the creative correctness of the media.

Current limitations: no scheduler, media decoder, automatic image QC, Blender
worker, FFmpeg renderer, continuity model, or provider publishing.

## Input manifest

The strict manifest schema is `arusia.v9.0` and has:

- `episode`: S01E01-style code, unique **per authenticated user**.
- `fps`: exactly 24.
- `shots`: 1 to 100.
- Per shot: SH001-style ID, bounded action, `duration_frames` (1..3600),
  canonical character names (KIO/LUMI/ARKA/MAYOR/RUX), an uppercase location
  asset ID, and explicit asset IDs.

See [sample](ARUSIA_SAMPLE_SHOT.json). The Kio sample comprises one
five-second shot at 24fps and **120 output frames**; this does not require
generating 120 AI images. Only a small, approved keyframe set needs to be
generated before rig/tween composition.

The asset identifiers are **logical references, not proof that files exist**.
An operator must actually verify approved character masters, backgrounds and
props before passing `ASSET_LOCK`. No reference images, checkpoints, LoRAs,
ControlNet bundles or MP4 media are included in this PR.

## MCP tools

- `arusia_plan_episode(manifest_json)`: strict validation, duration and stage
  estimate; **does not write**.
- `arusia_create_episode(manifest_json)`: internal PostgreSQL episode and
  shot-task metadata creation; requires verified MCP subject and operator
  `IM19_ENABLE_ARUSIA_WORKFLOWS=true`. Idempotent for identical code/content;
  rejects incompatible repeated manifests.
- `arusia_get_episode(episode_id, offset=0, limit=50)`: owner-bound
  paginated stage/task status, no secret output.
- `im19://arusia/workflow-v9`: static capability contract exposing unconnected
  image/render engines as unconnected.

None of these tools can approve creative gates, claim worker leases, provide
provider tokens, execute binaries or publish media. Persistent state lives in
the same **PostgreSQL** as IM19; development may use isolated SQLite.

## Trusted worker/operator boundary

The host-side CLI requires IM19_ENABLE_ARUSIA_WORKFLOWS=true,
IM19_ARUSIA_OPERATOR_ACK=YES, IM19_ARUSIA_SCOPED_SUBJECT=<OWNER> and
IM19_ARUSIA_ARTIFACT_ROOT=<PRIVATE_DIR> in a restricted local environment.
These settings are **not IAM** and do not prove operator identity. Production
requires externally authenticated operator accounts and workers.

Before accepting outputs, copy real input files into
<PRIVATE_DIR>/incoming/ and register them:

```bash
im19-arusia-operator ingest --subject '<OWNER>' \
  --asset-id KIO_MASTER --source-name kio-master.png
im19-arusia-operator ingest --subject '<OWNER>' \
  --asset-id EVIDENCE_KIO_MASTER --source-name kio-review.json
im19-arusia-operator approve-asset --subject '<OWNER>' \
  --asset-id KIO_MASTER --artifact-sha256 '<MASTER_SHA256>' \
  --evidence-sha256 '<REVIEW_SHA256>'

# Claim secret goes to a private mode-0600 file; never to AI output.
im19-arusia-operator claim --subject '<OWNER>' \
  --task-id '<TASK_ID>' --worker-id local-worker-01 \
  --lease-token-file '/secure/worker/lease-001'
im19-arusia-operator ingest --subject '<OWNER>' \
  --asset-id 'TASK_<TASK_ID_UPPERCASE>' --source-name task-output.json
im19-arusia-operator complete --subject '<OWNER>' \
  --task-id '<TASK_ID>' --lease-token-file '/secure/worker/lease-001' \
  --artifact-sha256 '<OUTPUT_SHA256>' \
  --provenance-json '{"worker_version":"local-staging"}'

# Human QC needs registered EVIDENCE_<TASK_ID_UPPERCASE> file.
im19-arusia-operator approve --subject '<OWNER>' \
  --task-id '<GATED_TASK_ID>' --evidence-sha256 '<EVIDENCE_SHA256>'
```

The CLI is only for privileged, audited local/staging operators. It cannot
authenticate reviewers on its own or establish independently valid QC.

For `IMAGE_GENERATION`, `provenance-json` must contain exactly the keys:
`model_sha256`, `lora_sha256`, `controlnet_sha256`,
`reference_sha256` (nonempty array), `seed` and `resolution`.
These values are *reported* by a worker, **not cryptographically verified
against media bytes by the MCP Gateway**.

Lease expiration allows at most three manual attempts per worker task. The
MCP Gateway itself has no scheduler/poller. A real worker dispatcher must be
implemented before claiming unattended batch generation or crash recovery.

## Migration and safe staging

This branch intentionally builds on PR #15. The Alembic chain is:

`0005_distributed_quotas` → `0006_write_approvals` →
`0007_arusia_workflow` → `0008_arusia_artifacts`.

The default `IM19_ENABLE_ARUSIA_WORKFLOWS=false` prevents new workflow
creation until enabled in staging. After backup and controlled migration,
run `alembic upgrade head`. Do not initialize production schemas with
`create_all` and do not reset persistent Codespaces volumes.

For a local development smoke test:

```bash
python -m pip install -e '.[dev]'
pytest -q tests/test_arusia_workflow.py
```

Run the full CI matrix, schema readiness check and Codespaces OAuth flow
before deploying any branch. Test real media workers and approval UX separately.

## Next integration milestones

1. Asset Registry with authenticated Google Drive ingestion, content-addressed
   media blobs, master revisions and human identity lock.
2. Worker transport and queue leases with heartbeats and read-only job offers,
   plus secured first-party worker registration and sandboxing.
3. ComfyUI adapter with batch workflow payloads, model inventory, and
   explicit per-keyframe ControlNet/LoRA references.
4. Blender/OpenToonz deterministic pose/depth/rig tween pipeline.
5. Quality governor: independent image identity/pose/continuity scoring,
   thresholds and localized inpainting/repair queues.
6. TTS/audio reference syncing and FFmpeg render worker, backed by actual
   checksum verification, size checks and playable MP4 inspection.
7. Episode-scale scheduling for the full ARUSIA S01E01 61-shot animatic only
   after the real manifest and approved assets have been imported.

The sample manifest and CI synthetic tests are **NOT** production renders,
validated character models, or evidence that ARUSIA media assets were read.

## Artifact integrity added in v9.1

- Each approved Character Master, prop and background is catalogued under a
  scoped asset ID, file checksum and independent human review-evidence record.
- Worker completion requires actual file bytes registered as TASK_<TASK_ID>.
- Human QC requires registered EVIDENCE_<TASK_ID> bytes. Content hashes
  are recomputed on every check to detect tampering.
- Files are stored by content hash in a private directory. Symlink traversal,
  missing files and unsupported file headers fail closed; each blob is
  limited to 64 MiB. These protections do NOT replace an OS sandbox.
- The schema now requires Alembic revision 0008_arusia_artifacts.
- Video/audio/image decode verification, media quality metrics, real reviewer
  identity, ComfyUI/Blender/FFmpeg adapters and persistent auto-worker scheduling
  remain **NOT_IMPLEMENTED**.
