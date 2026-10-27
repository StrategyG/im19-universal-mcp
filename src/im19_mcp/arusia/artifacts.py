"""Immutable local artifact registry for ARUSIA trusted media workers.

Artifacts are copied from a configured private incoming directory to a
content-addressed blob directory; the host verifies the bytes, not a digest
claimed by an AI model. Media decoding/semantic QC is a separate acceptance gate.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
from pathlib import Path

from sqlalchemy import BigInteger, Float, ForeignKey, String, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column

from im19_mcp.storage import Base, Vault
from im19_mcp.arusia.workflow import ASSET, DIGEST, WorkflowError

MAX_FILE_BYTES = 64 * 1024 * 1024


class ArusiaArtifact(Base):
    __tablename__ = "arusia_artifacts"
    record_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    subject: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    asset_id: Mapped[str] = mapped_column(String(64), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_length: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    __table_args__ = (UniqueConstraint("subject", "asset_id", "sha256",
                                        name="uq_arusia_artifact_version"),)


def media_kind(prefix: bytes) -> str:
    if prefix.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if prefix.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP":
        return "image/webp"
    if prefix.startswith(b"RIFF") and prefix[8:12] == b"WAVE":
        return "audio/wav"
    if len(prefix) >= 12 and prefix[4:8] == b"ftyp":
        return "video/mp4"
    return "application/octet-stream"


class ArtifactRegistry:
    def __init__(self, vault: Vault, root: str | Path):
        self.vault = vault
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir() or self.root.is_symlink():
            raise WorkflowError("artifact_root_invalid")
        self.incoming = self.root / "incoming"
        self.blobs = self.root / "blobs"
        for directory in (self.incoming, self.blobs):
            if directory.is_symlink():
                raise WorkflowError("artifact_directory_symlink_forbidden")
            directory.mkdir(mode=0o700, exist_ok=True)
            if not directory.is_dir():
                raise WorkflowError("artifact_directory_invalid")

    def _blob_path(self, sha256: str) -> Path:
        if not isinstance(sha256, str) or DIGEST.fullmatch(sha256) is None:
            raise WorkflowError("artifact_digest_invalid")
        return self.blobs / sha256

    @staticmethod
    def _digest_fd(fd: int) -> tuple[str, int, str]:
        import stat
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise WorkflowError("artifact_not_regular_file")
        size = os.fstat(fd).st_size
        if not 1 <= size <= MAX_FILE_BYTES:
            raise WorkflowError("artifact_size_invalid")
        sha = hashlib.sha256()
        head = b""
        count = 0
        with os.fdopen(os.dup(fd), "rb") as fh:
            fh.seek(0)
            while chunk := fh.read(1024 * 1024):
                count += len(chunk)
                if count > MAX_FILE_BYTES:
                    raise WorkflowError("artifact_size_invalid")
                if len(head) < 16:
                    head += chunk[:16 - len(head)]
                sha.update(chunk)
        if count != size:
            raise WorkflowError("artifact_changed_during_read")
        return sha.hexdigest(), count, media_kind(head)

    def verify(self, sha256: str) -> int:
        """Fail closed if content-addressed file was replaced or modified."""
        path = self._blob_path(sha256)
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except OSError:
            raise WorkflowError("artifact_missing") from None
        try:
            actual, size, _ = self._digest_fd(fd)
        finally:
            os.close(fd)
        if actual != sha256:
            raise WorkflowError("artifact_checksum_mismatch")
        return size

    def ingest(self, subject: str, asset_id: str, filename: str) -> dict:
        """Trusted operator only. File must be one filename inside private incoming/."""
        import time
        if not subject or len(subject) > 255:
            raise WorkflowError("artifact_subject_invalid")
        if not isinstance(asset_id, str) or ASSET.fullmatch(asset_id) is None:
            raise WorkflowError("artifact_id_invalid")
        if (not isinstance(filename, str) or filename in {"", ".", ".."} or
                len(filename) > 120 or "/" in filename or "\\" in filename or
                any(ord(c) < 32 for c in filename)):
            raise WorkflowError("artifact_filename_invalid")
        source = self.incoming / filename
        if source.is_symlink():
            raise WorkflowError("artifact_symlink_forbidden")
        try:
            fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except OSError:
            raise WorkflowError("artifact_source_unavailable") from None
        try:
            digest, size, kind = self._digest_fd(fd)
            if kind == "application/octet-stream":
                try:
                    with os.fdopen(os.dup(fd), "rb") as handle:
                        handle.seek(0)
                        payload = handle.read(MAX_FILE_BYTES + 1)
                    json.loads(payload)
                except (ValueError, UnicodeError, TypeError):
                    raise WorkflowError("artifact_format_unsupported") from None
                kind = "application/json"
            target = self._blob_path(digest)
            if not target.exists():
                import tempfile
                tmpfd, tmpname = tempfile.mkstemp(prefix=".import-", dir=self.blobs)
                try:
                    with os.fdopen(tmpfd, "wb") as dst, os.fdopen(os.dup(fd), "rb") as src:
                        src.seek(0)
                        while chunk := src.read(1024 * 1024):
                            dst.write(chunk)
                        dst.flush()
                        os.fsync(dst.fileno())
                    # Atomic no-clobber publication: no replacement of existing digest.
                    try:
                        os.link(tmpname, target, follow_symlinks=False)
                    except FileExistsError:
                        pass
                finally:
                    os.unlink(tmpname)
            if self.verify(digest) != size:
                raise WorkflowError("artifact_checksum_mismatch")
        finally:
            os.close(fd)
        with self.vault.sessions.begin() as session:
            existing = session.execute(select(ArusiaArtifact).where(
                ArusiaArtifact.subject == subject, ArusiaArtifact.asset_id == asset_id,
                ArusiaArtifact.sha256 == digest,
            )).scalar_one_or_none()
            if existing is None:
                session.add(ArusiaArtifact(
                    record_id=secrets.token_hex(16), subject=subject, asset_id=asset_id,
                    sha256=digest, byte_length=size, kind=kind, state="STAGED",
                    created_at=time.time(),
                ))
        return {"status": "STAGED", "asset_id": asset_id, "sha256": digest,
                "bytes": size, "kind": kind, "content_verified": True,
                "creative_approval_verified": False}

    def approve(self, subject: str, asset_id: str, sha256: str,
                evidence_sha256: str) -> dict:
        """Local operator sign-off; evidence is an inspected registered blob."""
        if not isinstance(evidence_sha256, str) or DIGEST.fullmatch(evidence_sha256) is None:
            raise WorkflowError("asset_evidence_invalid")
        self.check(subject, "EVIDENCE_" + asset_id, evidence_sha256, approved=False)
        with self.vault.sessions.begin() as session:
            row = session.execute(select(ArusiaArtifact).where(
                ArusiaArtifact.subject == subject, ArusiaArtifact.asset_id == asset_id,
                ArusiaArtifact.sha256 == sha256,
            ).with_for_update()).scalar_one_or_none()
            if row is None or row.state not in {"STAGED", "APPROVED"}:
                raise WorkflowError("asset_not_staged")
            if row.state == "APPROVED" and row.evidence_sha256 != evidence_sha256:
                raise WorkflowError("asset_approval_conflict")
            self.verify(row.sha256)
            row.state = "APPROVED"
            row.evidence_sha256 = evidence_sha256
        return {"status": "APPROVED", "asset_id": asset_id,
                "sha256": sha256, "creative_approval_by": "trusted_local_operator"}

    def check(self, subject: str, asset_id: str, digest: str | None = None,
              *, approved: bool = False) -> str:
        """Resolve latest approved asset or exact version and rehash file bytes."""
        if not isinstance(asset_id, str) or ASSET.fullmatch(asset_id) is None:
            raise WorkflowError("artifact_id_invalid")
        with self.vault.sessions() as session:
            query = select(ArusiaArtifact).where(
                ArusiaArtifact.subject == subject, ArusiaArtifact.asset_id == asset_id,
            )
            if digest is not None:
                query = query.where(ArusiaArtifact.sha256 == digest)
            if approved:
                query = query.where(ArusiaArtifact.state == "APPROVED")
            row = session.execute(query.order_by(
                ArusiaArtifact.created_at.desc(), ArusiaArtifact.record_id.desc()
            )).scalars().first()
            if row is None:
                raise WorkflowError("required_artifact_not_registered")
            self.verify(row.sha256)
            return row.sha256
