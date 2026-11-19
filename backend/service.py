"""Durable, bounded, resumable uploads using SQLite + atomic POSIX file operations.

Per-session POSIX flock serializes PUT/finalize even across server processes.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Iterator

from .video import VideoProcessingError, VideoValidationError, analyse_video

CHUNK_SIZE = 256 * 1024
MAX_VIDEO_SIZE = 100 * 1024 * 1024
MAX_ACTIVE_SESSIONS = 12
MAX_PROCESSING_ATTEMPTS = 3
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
STATES = {"CREATED", "UPLOADING", "PAUSED", "VERIFYING", "COMPLETED", "FAILED", "REJECTED"}


class UploadError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class Service:
    def __init__(self, data_dir: Path):
        self.root = Path(data_dir).resolve()
        for sub in ("parts", "final", "locks"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        self.dbpath = self.root / "uploads.sqlite3"
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("""CREATE TABLE IF NOT EXISTS uploads (
                id TEXT PRIMARY KEY, filename TEXT NOT NULL, size INTEGER NOT NULL,
                sha256 TEXT NOT NULL, chunk_size INTEGER NOT NULL,
                chunk_count INTEGER NOT NULL, status TEXT NOT NULL,
                created_at REAL NOT NULL, updated_at REAL NOT NULL,
                transfer_verified INTEGER NOT NULL DEFAULT 0,
                video_validated INTEGER NOT NULL DEFAULT 0,
                report_json TEXT, error TEXT)""")
            db.execute("""CREATE TABLE IF NOT EXISTS chunks (
                upload_id TEXT NOT NULL, idx INTEGER NOT NULL, size INTEGER NOT NULL,
                sha256 TEXT NOT NULL, PRIMARY KEY(upload_id, idx),
                FOREIGN KEY(upload_id) REFERENCES uploads(id))""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_upload_status ON uploads(status)")
            # Preserve existing SQLite sessions when upgrading the reviewed release.
            columns = {row['name'] for row in db.execute('PRAGMA table_info(uploads)')}
            if 'processing_attempts' not in columns:
                db.execute('ALTER TABLE uploads ADD COLUMN processing_attempts INTEGER NOT NULL DEFAULT 0')

    @contextlib.contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.dbpath, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=20000")
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @contextlib.contextmanager
    def lock(self, upload_id: str) -> Iterator[None]:
        self._validate_id(upload_id)
        lock_path = self.root / "locks" / (upload_id + ".lock")
        with open(lock_path, "a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _validate_id(upload_id: str) -> None:
        try:
            if str(uuid.UUID(upload_id)) != upload_id:
                raise ValueError()
        except ValueError:
            raise UploadError("Unknown upload session", 404)

    def _get(self, db: sqlite3.Connection, upload_id: str) -> sqlite3.Row:
        self._validate_id(upload_id)
        item = db.execute("SELECT * FROM uploads WHERE id=?", (upload_id,)).fetchone()
        if item is None:
            raise UploadError("Unknown upload session", 404)
        return item

    def start(self, filename: str, size: int, sha256: str) -> dict[str, Any]:
        if not isinstance(filename, str) or not (1 <= len(filename) <= 160):
            raise UploadError("File name must be 1–160 characters")
        if not isinstance(size, int) or isinstance(size, bool) or not (1 <= size <= MAX_VIDEO_SIZE):
            raise UploadError(f"Size must be within 1..{MAX_VIDEO_SIZE} bytes")
        if not isinstance(sha256, str) or not SHA256_RE.fullmatch(sha256):
            raise UploadError("Expected 64-char lowercase SHA-256 hex digest")
        now = time.time()
        id_ = str(uuid.uuid4())
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            active = db.execute("SELECT COUNT(*) FROM uploads WHERE status NOT IN ('COMPLETED','FAILED','REJECTED')").fetchone()[0]
            if active >= MAX_ACTIVE_SESSIONS:
                raise UploadError("Too many active sessions; retry or clean up stale sessions", 429)
            db.execute("""INSERT INTO uploads
                (id,filename,size,sha256,chunk_size,chunk_count,status,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (id_, Path(filename).name, size, sha256, CHUNK_SIZE, math.ceil(size / CHUNK_SIZE), "CREATED", now, now))
        (self.root / "parts" / id_).mkdir(exist_ok=True)
        return self.status(id_)

    def _part_path(self, id_: str, idx: int) -> Path:
        return self.root / "parts" / id_ / f"{idx:06d}.chunk"

    def status(self, id_: str) -> dict[str, Any]:
        with self.db() as db:
            item = self._get(db, id_)
            parts = db.execute("SELECT idx,size,sha256 FROM chunks WHERE upload_id=? ORDER BY idx", (id_,)).fetchall()
            return {
                "id": id_, "filename": item["filename"], "file_size": item["size"],
                "file_sha256": item["sha256"], "chunk_size": item["chunk_size"],
                "chunk_count": item["chunk_count"], "status": item["status"],
                "received": [{"index": p["idx"], "size": p["size"], "sha256": p["sha256"]} for p in parts],
                "received_bytes": sum(p["size"] for p in parts),
                "transfer_verified": bool(item["transfer_verified"]),
                "video_validated": bool(item["video_validated"]),
                "processing_attempts": item["processing_attempts"],
                "max_processing_attempts": MAX_PROCESSING_ATTEMPTS,
                "server_sha256": item["sha256"] if item["transfer_verified"] else None,
                "error": item["error"],
                "created_at": item["created_at"], "updated_at": item["updated_at"],
            }

    def put(self, id_: str, idx: int, data: bytes, expected_hash: str) -> dict[str, Any]:
        if not isinstance(expected_hash, str) or not SHA256_RE.fullmatch(expected_hash):
            raise UploadError("Missing/invalid X-Chunk-SHA256")
        if not isinstance(idx, int) or idx < 0:
            raise UploadError("Invalid chunk index")
        if not isinstance(data, bytes):
            raise UploadError("Expected raw bytes")
        actual_hash = hashlib.sha256(data).hexdigest()
        if actual_hash != expected_hash:
            raise UploadError("Chunk SHA-256 mismatch; chunk not accepted", 422)
        with self.lock(id_):
            with self.db() as db:
                item = self._get(db, id_)
                if item["status"] in ("COMPLETED", "REJECTED", "FAILED", "VERIFYING"):
                    raise UploadError("Upload session is no longer writable", 409)
                if idx >= item["chunk_count"]:
                    raise UploadError("Chunk index is out of range")
                desired = min(item["chunk_size"], item["size"] - idx * item["chunk_size"])
                if len(data) != desired:
                    raise UploadError(f"Chunk length mismatch: expected {desired} bytes, got {len(data)}", 422)
                present = db.execute("SELECT sha256,size FROM chunks WHERE upload_id=? AND idx=?", (id_, idx)).fetchone()
                part = self._part_path(id_, idx)
                if present is not None:
                    if present["sha256"] != actual_hash or present["size"] != len(data):
                        raise UploadError("Conflicting chunk already committed at this index", 409)
                    if part.exists() and part.stat().st_size == len(data) and self._hash_file(part) == actual_hash:
                        return {"index": idx, "sha256": actual_hash, "size": len(data), "duplicate": True,
                                "durably_stored": True}
                part.parent.mkdir(parents=True, exist_ok=True)
                self._atomic_file(part, data)
                db.execute("INSERT OR REPLACE INTO chunks (upload_id,idx,size,sha256) VALUES (?,?,?,?)",
                           (id_, idx, len(data), actual_hash))
                db.execute("UPDATE uploads SET status='UPLOADING',updated_at=? WHERE id=?", (time.time(), id_))
        return {"index": idx, "sha256": actual_hash, "size": len(data), "duplicate": False,
                "durably_stored": True}

    @staticmethod
    def _sync_directory(path: Path) -> None:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _atomic_file(self, dest: Path, data: bytes) -> None:
        fd, tmpname = tempfile.mkstemp(prefix=".incoming-", dir=dest.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmpname, dest)
            self._sync_directory(dest.parent)
        finally:
            if os.path.exists(tmpname):
                os.unlink(tmpname)

    @staticmethod
    def _hash_file(path: Path) -> str:
        dig = hashlib.sha256()
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                dig.update(block)
        return dig.hexdigest()

    def pause(self, id_: str) -> dict[str, Any]:
        with self.lock(id_):
            with self.db() as db:
                item = self._get(db, id_)
                if item["status"] not in ("CREATED", "UPLOADING", "PAUSED"):
                    raise UploadError("Only active uploads may be paused", 409)
                db.execute("UPDATE uploads SET status='PAUSED',updated_at=? WHERE id=?", (time.time(), id_))
        return self.status(id_)

    def finalize(self, id_: str) -> dict[str, Any]:
        with self.lock(id_):
            with self.db() as db:
                item = self._get(db, id_)
                if item["status"] in ("COMPLETED", "FAILED"):
                    return self.status(id_)
                if item["status"] == "REJECTED":
                    raise UploadError("Session rejected; create a fresh upload session", 409)
                parts = db.execute("SELECT idx,size,sha256 FROM chunks WHERE upload_id=? ORDER BY idx", (id_,)).fetchall()
                if len(parts) != item["chunk_count"] or any(p["idx"] != i for i, p in enumerate(parts)):
                    raise UploadError("Cannot finalize: missing chunks", 409)
                if item['processing_attempts'] >= MAX_PROCESSING_ATTEMPTS:
                    # A crash can consume the final attempt before its error is saved.
                    exhausted = True
                else:
                    exhausted = False
                    db.execute("UPDATE uploads SET status='VERIFYING',processing_attempts=processing_attempts+1,updated_at=? WHERE id=?", (time.time(), id_))
            if exhausted:
                return self._processing_failure(id_, 'Processing attempt limit reached after an interrupted attempt')
            final_path = self.root / "final" / (id_ + ".video")
            pending = self.root / "final" / (id_ + ".pending")
            # Rebuild even after a crash during VERIFYING. The committed parts
            # are source of truth and the prior .pending file can be replaced.
            verified = False
            try:
                digest = hashlib.sha256()
                count = 0
                with open(pending, "wb") as combined:
                    for p in parts:
                        source_path = self._part_path(id_, p["idx"])
                        if not source_path.exists():
                            raise UploadError(f"Chunk {p['idx']} missing on disk", 422)
                        h = hashlib.sha256()
                        with open(source_path, "rb") as stream:
                            for block in iter(lambda: stream.read(1024 * 1024), b""):
                                count += len(block)
                                h.update(block)
                                digest.update(block)
                                combined.write(block)
                        if h.hexdigest() != p["sha256"] or source_path.stat().st_size != p["size"]:
                            raise UploadError(f"Chunk {p['idx']} became corrupted on disk", 422)
                    combined.flush()
                    os.fsync(combined.fileno())
                if count != item["size"] or digest.hexdigest() != item["sha256"]:
                    raise UploadError("Whole-file size/SHA-256 verification failed", 422)
                os.replace(pending, final_path)
                self._sync_directory(final_path.parent)
                verified = True
                with self.db() as db:
                    db.execute("UPDATE uploads SET transfer_verified=1,updated_at=? WHERE id=?", (time.time(), id_))
                # Hard gate: video tools never touch unverified content.
                video = analyse_video(final_path)
                report = {
                    "schema_version": "1.0", "upload_id": id_, "filename": item["filename"],
                    "transfer": {"complete": True, "client_sha256": item["sha256"],
                                 "server_sha256": digest.hexdigest(), "sha256_match": True,
                                 "bytes": count, "chunks": len(parts)},
                    "processing": video,
                    "outcome": "COMPLETED", "generated_at_unix": time.time(),
                }
                with self.db() as db:
                    db.execute("UPDATE uploads SET status='COMPLETED', video_validated=1,report_json=?,error=NULL,updated_at=? WHERE id=?",
                               (json.dumps(report), time.time(), id_))
                return self.status(id_)
            except (UploadError, VideoValidationError) as exc:
                error = str(exc)
                outcome = "REJECTED"
                report = {"schema_version": "1.0", "upload_id": id_, "outcome": outcome,
                          "transfer": {"complete": verified, "client_sha256": item["sha256"],
                                       "server_sha256": item["sha256"] if verified else None,
                                       "sha256_match": verified},
                          "processing": {"video_valid": False, "error": error} if verified else None,
                          "error": error, "generated_at_unix": time.time()}
                with self.db() as db:
                    db.execute("UPDATE uploads SET status=?,report_json=?,error=?,transfer_verified=?,video_validated=0,updated_at=? WHERE id=?",
                               (outcome, json.dumps(report), error, int(verified), time.time(), id_))
                return self.status(id_)
            except Exception as exc:
                # Known operational failures do not establish invalid source video.
                message = str(exc) if isinstance(exc, VideoProcessingError) else f'Internal processing failure: {type(exc).__name__}'
                result = self._processing_failure(id_, message)
                if result['status'] == 'FAILED':
                    return result
                raise VideoProcessingError(message) from exc
            finally:
                pending.unlink(missing_ok=True)

    def _processing_failure(self, id_: str, message: str) -> dict[str, Any]:
        """Called under the session lock; retain all chunks and the verified file."""
        with self.db() as db:
            item = self._get(db, id_)
            terminal = item['processing_attempts'] >= MAX_PROCESSING_ATTEMPTS
            status = 'FAILED' if terminal else 'VERIFYING'
            error = (f'Processing failed after {MAX_PROCESSING_ATTEMPTS} attempts; video validity is unknown. '
                     'Uploaded data retained; restore processing tools/resources and create a new session. '
                     if terminal else 'Processing unavailable; retry processing using the same session. ') + message
            report = None
            if terminal:
                report = json.dumps({'schema_version':'1.1', 'upload_id':id_, 'outcome':'FAILED',
                    'transfer':{'complete':bool(item['transfer_verified']),
                                'client_sha256':item['sha256'],
                                'server_sha256':item['sha256'] if item['transfer_verified'] else None,
                                'sha256_match':bool(item['transfer_verified'])},
                    'processing':{'video_valid':None,'status':'FAILED','attempts':item['processing_attempts'],
                                  'error_class':'operational_failure','error':message},
                    'error':error,'generated_at_unix':time.time()})
            db.execute('UPDATE uploads SET status=?,error=?,video_validated=0,report_json=?,updated_at=? WHERE id=?',
                       (status,error,report,time.time(),id_))
        return self.status(id_)

    def report(self, id_: str) -> dict[str, Any]:
        with self.db() as db:
            item = self._get(db, id_)
            if not item["report_json"]:
                raise UploadError("Report not available; upload not finalized", 409)
            return json.loads(item["report_json"])

    def cleanup(self, age_hours: float = 24.0) -> int:
        """Delete terminal or stale sessions older than the threshold; caller schedules it."""
        deleted = 0
        with self.db() as db:
            ids = [row[0] for row in db.execute("SELECT id FROM uploads WHERE updated_at<?",
                                                (time.time() - 3600 * age_hours,))]
        for id_ in ids:
            with self.lock(id_):
                with self.db() as db:
                    current = self._get(db, id_)
                    if current["updated_at"] >= time.time() - 3600 * age_hours:
                        continue
                    db.execute("DELETE FROM chunks WHERE upload_id=?", (id_,))
                    db.execute("DELETE FROM uploads WHERE id=?", (id_,))
                shutil.rmtree(self.root / "parts" / id_, ignore_errors=True)
                (self.root / "final" / (id_ + ".video")).unlink(missing_ok=True)
                (self.root / "final" / (id_ + ".pending")).unlink(missing_ok=True)
                deleted += 1
            # Keep the lock inode: existing waiters must share the same lock
            # with future callers. Reclaim these only during offline maintenance.
        return deleted
