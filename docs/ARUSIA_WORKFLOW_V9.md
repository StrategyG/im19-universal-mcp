# ARUSIA v9 — IM19 workflow architecture (incremental implementation)

**Scope of this PR: durable planning + shot-stage orchestration contract.**
The eight logical subsystems in the ARUSIA v9 design are preserved, but the
code does not yet render episodes or generate individual frames.

## Workflow DAG

For every shot, twelve ordered tasks are persisted:

| Stage | Executor | Subsystem | Gate |
|---|---|---|---|
| SCRIPT_BREAKDOWN | Worker | Story Engine | file/artifact reference report |
| ASSET_LOCK | Human | Asset Registry | human confirms source assets |
| STORYBOARD | Worker | Story Engine | storyboard reference report |
| CREATIVE_APPROVAL | Human | Creative Review | manual sign-off |
| POSE_CAMERA | Worker | Shot Controller | controlled rig/depth/pose reference |
| IMAGE_GENERATION | Worker | Precision Image Engine | model/LoRA/ControlNet/ref/seed metadata |
| FRAME_QC | Human | Quality Governor | identity/pose/continuity inspection |
| ANIMATION | Worker | Animation Engine | frame sequence/rig output reference |
| AUDIO | Worker | Audio Engine | voice and SFX artifact reference |
| RENDER | Worker | Render Engine | render output reference |
| FINAL_QC | Human | Quality Governor | final episode review |
| DELIVERY_PACKAGE | Worker | Render Engine | prepare bounded master/export bundle |

A successor becomes \`READY\` **only after** its worker predecessor reports
completion, or its human-gated predecessor is approved by a *trusted operator*.
Human approval cannot be issued via any MCP tool. AI cannot advance those
gates by crafting a prompt.

**Important limitation:** this initial DAG is deliberately sequential per
shot. Production should add cross-shot dependencies, parallel audio/render jobs,
adaptive retry queues, content-addressed storage, workers, cancellation,
notifications and richer evidence provenance in separately reviewed PRs.

## Input manifest

The strict manifest schema is \`arusia.v9.0\` and has:

- \`episode\`: S01E01-style code, unique **per authenticated user**.
- \`fps\`: exactly 24.
- \`shots\`: 1 to 100.
- Per shot: SH001-style ID, bounded action, \`duration_frames\` (1..3600),
  canonical character names (KIO/LUMI/ARKA/MAYOR/RUX), an uppercase location
  asset ID, and explicit asset IDs.

See [sample](ARUSIA_SAMPLE_SHOT.json). The Kio sample comprises one
five-second shot at 24fps and **120 output frames**; this does not require
generating 120 AI images. Only a small, approved keyframe set needs to be
generated before rig/tween composition.

The asset identifiers are **logical references, not proof that files exist**.
An operator must actually verify approved character masters, backgrounds and
props before passing \`ASSET_LOCK\`. No reference images, checkpoints, LoRAs,
ControlNet bundles or MP4 media are included in this PR.

## MCP tools

- \`arusia_plan_episode(manifest_json)\`: strict validation, duration and stage
  estimate; **does not write**.
- \`arusia_create_episode(manifest_json)\`: internal PostgreSQL episode and
  shot-task metadata creation; requires verified MCP subject and operator
  \`IM19_ENABLE_ARUSIA_WORKFLOWS=true\`. Idempotent for identical code/content;
  rejects incompatible repeated manifests.
- \`arusia_get_episode(episode_id, offset=0, limit=50)\`: owner-bound
  paginated stage/task status, no secret output.
- \`im19://arusia/workflow-v9\`: static capability contract exposing unconnected
  image/render engines as unconnected.

None of these tools can approve creative gates, claim worker leases, provide
provider tokens, execute binaries or publish media. Persistent state lives in
the same **PostgreSQL** as IM19; development may use isolated SQLite.

## Trusted worker/operator boundary

Local command \`im19-arusia-operator\` requires
\`IM19_ARUSIA_OPERATOR_ACK=YES\` **and**
\`IM19_ENABLE_ARUSIA_WORKFLOWS=true\` in a secured terminal with access to the
IM19 database. It is **not a remotely authenticated worker API** and must
not be offered as an MCP tool or published over a public port.

Examples after DB schema migration:

\`\`\`bash
export IM19_ENABLE_ARUSIA_WORKFLOWS=true
export IM19_ARUSIA_OPERATOR_ACK=YES

# Authenticate MCP client, then create the episode using the provided JSON.
# List its stages from MCP arusia_get_episode or:
im19-arusia-operator inspect --subject '<VERIFIED_SUBJECT>' \
  --episode-id '<EPISODE_ID>'

# Trusted local worker claims exactly one READY stage:
im19-arusia-operator claim --subject '<VERIFIED_SUBJECT>' \
  --task-id '<READY_TASK_ID>' --worker-id arusia-local-01

# The lease token is a secret. Keep it out of logs and prompts.
im19-arusia-operator complete --subject '<VERIFIED_SUBJECT>' \
  --task-id '<TASK_ID>' --lease-token '<SECRET_LEASE_TOKEN>' \
  --artifact-sha256 '<ACTUAL_ARTIFACT_SHA256>' \
  --provenance-json '{"tool":"trusted-local-worker"}'

# After independently reviewing the evidence (not a model self-approval):
im19-arusia-operator approve --subject '<VERIFIED_SUBJECT>' \
  --task-id '<WAITING_APPROVAL_TASK_ID>' \
  --evidence-sha256 '<ACTUAL_REVIEW_EVIDENCE_SHA256>'
\`\`\`

For \`IMAGE_GENERATION\`, \`provenance-json\` must contain exactly the keys:
\`model_sha256\`, \`lora_sha256\`, \`controlnet_sha256\`,
\`reference_sha256\` (nonempty array), \`seed\` and \`resolution\`.
These values are *reported* by a worker, **not cryptographically verified
against media bytes by the MCP Gateway**.

Lease expiration allows at most three manual attempts per worker task. The
MCP Gateway itself has no scheduler/poller. A real worker dispatcher must be
implemented before claiming unattended batch generation or crash recovery.

## Migration and safe staging

This branch intentionally builds on PR #15. The Alembic chain is:

\`0005_distributed_quotas\` → \`0006_write_approvals\` →
\`0007_arusia_workflow\`.

The default \`IM19_ENABLE_ARUSIA_WORKFLOWS=false\` prevents new workflow
creation until enabled in staging. After backup and controlled migration,
run \`alembic upgrade head\`. Do not initialize production schemas with
\`create_all\` and do not reset persistent Codespaces volumes.

For a local development smoke test:

\`\`\`bash
python -m pip install -e '.[dev]'
pytest -q tests/test_arusia_workflow.py
\`\`\`

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
