"""Synthetic bytes, real file hashing, tenant and tamper checks. No GPU media engine."""
import os

import pytest
from cryptography.fernet import Fernet

from im19_mcp.arusia.artifacts import ArtifactRegistry
from im19_mcp.arusia.workflow import ArusiaWorkflow, WorkflowError
from im19_mcp.storage import Vault


@pytest.fixture
def registry(tmp_path):
    root = tmp_path / "private"
    root.mkdir()
    v = Vault("sqlite:///" + str(tmp_path / "assets.sqlite"),
              encryption_key=Fernet.generate_key().decode(), audit_salt="artifact-integration")
    v.initialize_schema()
    catalog = ArtifactRegistry(v, root)
    yield catalog
    v.engine.dispose()


def ingest_json(catalog, asset, *, contents=b'{"ok": true}', owner="alice"):
    name = "upload-" + asset + ".json"
    (catalog.incoming / name).write_bytes(contents)
    return catalog.ingest(owner, asset, name)


def test_content_addressed_catalog_is_reproducible_and_owner_scoped(registry):
    a = ingest_json(registry, "KIO_MASTER")
    b = ingest_json(registry, "KIO_MASTER")
    assert a["status"] == "STAGED"
    assert a["sha256"] == b["sha256"]
    assert a["bytes"] == len(b'{"ok": true}')
    assert registry.check("alice", "KIO_MASTER", a["sha256"]) == a["sha256"]
    with pytest.raises(WorkflowError, match="required_artifact_not_registered"):
        registry.check("bob", "KIO_MASTER", a["sha256"])
    with pytest.raises(WorkflowError, match="required_artifact_not_registered"):
        registry.check("alice", "KIO_MASTER", a["sha256"], approved=True)

    proof = ingest_json(registry, "EVIDENCE_KIO_MASTER", contents=b'{"human_review": true}')
    approved = registry.approve("alice", "KIO_MASTER", a["sha256"], proof["sha256"])
    assert approved["status"] == "APPROVED"
    assert registry.check("alice", "KIO_MASTER", approved=True) == a["sha256"]
    assert registry.verify(a["sha256"]) == a["bytes"]


def test_real_bytes_rechecked_to_detect_tampering(registry):
    doc = ingest_json(registry, "KIO_MASTER")
    target = registry.blobs / doc["sha256"]
    target.write_bytes(b'{"corrupted": true}')
    with pytest.raises(WorkflowError, match="artifact_checksum_mismatch"):
        registry.check("alice", "KIO_MASTER", doc["sha256"])


def test_untrusted_symlink_or_non_regular_input_rejected(registry, tmp_path):
    filename = registry.incoming / "escape.json"
    outside = tmp_path / "secret.json"
    outside.write_text('{"secret": "outside"}')
    filename.symlink_to(outside)
    with pytest.raises(WorkflowError, match="artifact_symlink_forbidden"):
        registry.ingest("alice", "KIO_MASTER", filename.name)
    with pytest.raises(WorkflowError, match="artifact_filename_invalid"):
        registry.ingest("alice", "KIO_MASTER", "../secret.json")
    with pytest.raises(WorkflowError, match="artifact_format_unsupported"):
        _bad_file(registry)


def _bad_file(registry):
    (registry.incoming / "invalid.bin").write_bytes(b"not-json-or-image")
    return registry.ingest("alice", "KIO_MASTER", "invalid.bin")


def test_blob_symlink_always_denied(registry, tmp_path):
    result = ingest_json(registry, "KIO_MASTER")
    blob = registry.blobs / result["sha256"]
    blob.unlink()
    outside = tmp_path / "target.json"
    outside.write_text('{"ok": true}')
    blob.symlink_to(outside)
    with pytest.raises(WorkflowError, match="artifact_missing"):
        registry.verify(result["sha256"])


def test_workflow_cannot_complete_without_a_registered_real_artifact(registry):
    engine = ArusiaWorkflow(registry.vault, registry.root)
    manifest = ('{"schema_version":"arusia.v9.0","episode":"S01E01","fps":24,"shots":'
                '[{"shot_id":"SH001","action":"Kio berdiri.","duration_frames":24,'
                '"characters":["KIO"],"location_id":"HALAMAN_KIO",'
                '"asset_ids":["KIO_MASTER"]}]}')
    episode_id = engine.create("alice", manifest)["episode_id"]
    task = next(task for task in engine.inspect("alice", episode_id)["tasks"]
                if task["stage"] == "SCRIPT_BREAKDOWN")
    lease = engine.claim("alice", task["task_id"], worker_id="worker-123")
    with pytest.raises(WorkflowError, match="required_artifact_not_registered"):
        engine.complete("alice", task["task_id"], lease["lease_token"], "a" * 64,
                        provenance={"seed": 1})
    staged = ingest_json(registry, "TASK_" + task["task_id"].upper())
    result = engine.complete("alice", task["task_id"], lease["lease_token"],
                             staged["sha256"], provenance={"seed": 1})
    assert result["status"] == "WORKER_REPORTED_DONE"
    assert engine.inspect("alice", episode_id)["episode_rendered"] is False


def test_lease_is_not_leaked_through_cli_filename_failure(tmp_path):
    from im19_mcp.arusia_operator import _read_lease, _write_lease
    token = "q" * 43
    secretfile = tmp_path / "lease.txt"
    _write_lease(str(secretfile), token)
    assert _read_lease(str(secretfile)) == token
    assert os.stat(secretfile).st_mode & 0o077 == 0
    with pytest.raises(FileExistsError):
        _write_lease(str(secretfile), token)
    if hasattr(os, "chmod"):
        secretfile.chmod(0o644)
        with pytest.raises(WorkflowError, match="permissions_invalid"):
            _read_lease(str(secretfile))
