import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import Field
from sqlalchemy import text

from bidops.config import Settings
from bidops.db import Database
from bidops.models import Notice, Snapshot, StrictModel
from bidops.notice_data import NoticeRepository
from bidops.parser import canonical_text, parse_notice
from bidops.sources import canonical_url, load_sources


def stable_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def snapshot_id(url: str, digest: str) -> str:
    return hashlib.sha256((url + "\n" + digest).encode()).hexdigest()


class Manifest(StrictModel):
    format_version: int = 1
    parser_version: str = "p0.2"
    snapshots: list[Snapshot] = Field(max_length=500)


@dataclass(frozen=True)
class PreparedNotice:
    snapshot: Snapshot
    notice: Notice
    normalized: str


@dataclass(frozen=True)
class PersistedNotice:
    snapshot: Snapshot
    created: bool
    version_id: str


class Store:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.sources = load_sources(settings.source_registry)
        self.db = Database(settings.database_url)
        self.db.migrate()
        self.raw = settings.data_dir / "raw"
        self.raw.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.notice_data = NoticeRepository(self.db, self.raw, max_bytes=settings.max_bytes)
        self.notice_data.backfill_legacy()

    def register_url(self, url: str) -> str:
        url = canonical_url(url, self.sources)
        self.db.execute("INSERT OR IGNORE INTO urls(source_url) VALUES (:u)", {"u": url})
        return url

    def ingest(
        self,
        url: str,
        data: bytes,
        *,
        content_type="text/plain",
        encoding="utf-8",
        access_method="manual_import",
        manual_attestation=False,
        fetched_at=None,
        http_status=None,
        etag=None,
        last_modified=None,
    ) -> tuple[Snapshot, bool]:
        url = canonical_url(url, self.sources)
        if access_method == "manual_import" and not manual_attestation:
            raise ValueError("manual_rights_attestation_required")
        if not 0 < len(data) <= self.settings.max_bytes:
            raise ValueError("empty_or_oversized_input")
        digest = hashlib.sha256(data).hexdigest()
        snapshot = Snapshot(
            snapshot_id=snapshot_id(url, digest),
            source_url=url,
            sha256=digest,
            byte_size=len(data),
            fetched_at=fetched_at or datetime.now(UTC),
            http_status=http_status,
            content_type=content_type,
            encoding=encoding,
            access_method=access_method,
            manual_attestation=manual_attestation,
            etag=etag,
            last_modified=last_modified,
        )
        return self.save(snapshot, data)

    def prepare(self, snapshot: Snapshot, data: bytes) -> PreparedNotice:
        self.validate_snapshot(snapshot, data)
        normalized = canonical_text(data, snapshot.content_type, snapshot.encoding)
        if not normalized.strip():
            raise ValueError("empty_normalized_text")
        notice = parse_notice(normalized, snapshot)
        if snapshot.access_method == "http_fetch" and not (
            notice.project_code and notice.title and notice.notice_kind
        ):
            raise ValueError("unverified_notice_fields")
        target = self.raw / snapshot.sha256
        # Content addressed, O_EXCL: existing bytes are verified and never overwritten.
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if (
                target.is_symlink()
                or hashlib.sha256(target.read_bytes()).hexdigest() != snapshot.sha256
            ):
                raise ValueError("existing_blob_corrupt") from None
        else:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        return PreparedNotice(snapshot, notice, normalized)

    def persist(
        self, prepared: PreparedNotice, conn, *, source_complete=False, record_observation=True
    ) -> PersistedNotice:
        """Persist one prepared notice using the caller's transaction and receipt.

        Parsing and immutable blob creation happen in prepare, outside a batch
        write lock. The version layer revalidates bytes and source identity.
        A page/cursor caller can roll back every item and its audit together.
        """
        if type(prepared) is not PreparedNotice:
            raise ValueError("invalid_prepared_notice")
        snapshot = prepared.snapshot.model_copy(deep=True)
        notice = prepared.notice.model_copy(deep=True)
        normalized, observed_at = prepared.normalized, snapshot.fetched_at
        conn.execute(
            text("INSERT OR IGNORE INTO urls(source_url) VALUES (:u)"),
            {"u": snapshot.source_url},
        )
        result = conn.execute(
            text(
                "INSERT OR IGNORE INTO snapshots(snapshot_id,source_url,sha256,metadata_json) "
                "VALUES (:id,:url,:sha,:meta)"
            ),
            {
                "id": snapshot.snapshot_id,
                "url": snapshot.source_url,
                "sha": snapshot.sha256,
                "meta": snapshot.model_dump_json(),
            },
        )
        new = result.rowcount == 1
        if new:
            conn.execute(
                text(
                    "INSERT INTO notices("
                    "notice_id,snapshot_id,project_code,notice_kind,payload_json) "
                    "VALUES (:id,:id,:code,:kind,:payload)"
                ),
                {
                    "id": notice.notice_id,
                    "code": notice.project_code,
                    "kind": notice.notice_kind,
                    "payload": notice.model_dump_json(),
                },
            )
        else:
            saved = conn.execute(
                text("SELECT metadata_json FROM snapshots WHERE snapshot_id=:id"),
                {"id": snapshot.snapshot_id},
            ).scalar_one()
            snapshot = Snapshot.model_validate_json(saved)
            # A repeated observation cannot change the persisted source time or
            # manufacture a different parse revision. Reparse is explicit.
            payload = conn.execute(
                text("SELECT payload_json FROM notices WHERE notice_id=:id"),
                {"id": snapshot.snapshot_id},
            ).scalar_one()
            notice = Notice.model_validate_json(payload)
        version = self.notice_data.capture(
            snapshot,
            notice,
            normalized,
            observed_at=observed_at,
            source_complete=source_complete,
            record_observation=record_observation or new,
            conn=conn,
        )
        conn.execute(
            text("UPDATE urls SET state='snapshot_captured' WHERE source_url=:u"),
            {"u": snapshot.source_url},
        )
        conn.execute(
            text(
                "INSERT INTO audit_events(observed_at,event,code,source_url,metadata_json) "
                "VALUES (:at,'ingest',:code,:url,:meta)"
            ),
            {
                "at": datetime.now(UTC).isoformat(),
                "code": "created" if new else "duplicate",
                "url": snapshot.source_url,
                "meta": json.dumps(
                    {"snapshot_id": snapshot.snapshot_id, "sha256": snapshot.sha256}, sort_keys=True
                ),
            },
        )
        return PersistedNotice(snapshot, new, version.version_id)

    def save(self, snapshot: Snapshot, data: bytes) -> tuple[Snapshot, bool]:
        prepared = self.prepare(snapshot, data)
        with self.db.engine.begin() as conn:
            receipt = self.persist(prepared, conn)
        return receipt.snapshot, receipt.created

    def validate_snapshot(self, snapshot: Snapshot, data: bytes):
        url = canonical_url(snapshot.source_url, self.sources)
        if url != snapshot.source_url or snapshot.snapshot_id != snapshot_id(url, snapshot.sha256):
            raise ValueError("snapshot_identity_mismatch")
        if not 0 < len(data) <= self.settings.max_bytes or len(data) != snapshot.byte_size:
            raise ValueError("snapshot_size_mismatch")
        if hashlib.sha256(data).hexdigest() != snapshot.sha256:
            raise ValueError("sha256_mismatch")
        if snapshot.access_method == "manual_import" and not snapshot.manual_attestation:
            raise ValueError("manual_rights_attestation_required")
        if snapshot.access_method == "http_fetch" and snapshot.http_status != 200:
            raise ValueError("http_snapshot_requires_200")

    def get_notice(self, notice_id: str) -> Notice | None:
        rows = self.db.rows(
            "SELECT payload_json FROM notices WHERE notice_id=:id", {"id": notice_id}
        )
        return Notice.model_validate_json(rows[0]["payload_json"]) if rows else None

    def reparse(self) -> int:
        rows = self.db.rows("SELECT metadata_json FROM snapshots ORDER BY snapshot_id")
        interpreted_at = datetime.now(UTC)
        for row in rows:
            snapshot = Snapshot.model_validate_json(row["metadata_json"])
            data = (self.raw / snapshot.sha256).read_bytes()
            self.validate_snapshot(snapshot, data)
            normalized = canonical_text(data, snapshot.content_type, snapshot.encoding)
            notice = parse_notice(normalized, snapshot)
            with self.db.engine.begin() as conn:
                self.notice_data.capture(
                    snapshot,
                    notice,
                    normalized,
                    interpreted_at=interpreted_at,
                    record_observation=False,
                    conn=conn,
                )
                # Only the compatibility projection changes. Immutable revisions,
                # source bytes and historical session payloads remain intact.
                conn.execute(
                    text(
                        "UPDATE notices SET payload_json=:p,project_code=:c,notice_kind=:k "
                        "WHERE notice_id=:id"
                    ),
                    {
                        "p": notice.model_dump_json(),
                        "c": notice.project_code,
                        "k": notice.notice_kind,
                        "id": notice.notice_id,
                    },
                )
        self.db.audit("parse", "completed", count=len(rows))
        return len(rows)

    def export(self, destination: Path, include_raw: bool = False) -> dict:
        # Manifest contains provenance only. Raw bytes are included only in a local bundle.
        snapshots = [
            Snapshot.model_validate_json(r["metadata_json"])
            for r in self.db.rows("SELECT metadata_json FROM snapshots ORDER BY snapshot_id")
        ]
        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
        for snapshot in snapshots:
            blob = (self.raw / snapshot.sha256).read_bytes()
            self.validate_snapshot(snapshot, blob)
            if include_raw:
                folder = destination / "raw"
                folder.mkdir(exist_ok=True, mode=0o700)
                output = folder / snapshot.sha256
                if output.exists() and (output.is_symlink() or output.read_bytes() != blob):
                    raise ValueError("export_blob_conflict")
                output.write_bytes(blob)
                output.chmod(0o600)
        manifest = Manifest(snapshots=snapshots)
        content = stable_json(manifest.model_dump(mode="json")).encode()
        path = destination / "manifest.json"
        if path.is_symlink():
            raise ValueError("export_manifest_symlink")
        path.write_bytes(content)
        path.chmod(0o600)
        return {
            "snapshots": len(snapshots),
            "sha256": hashlib.sha256(content).hexdigest(),
            "includes_raw": include_raw,
        }

    def import_bundle(self, folder: Path) -> dict:
        manifest_path = folder / "manifest.json"
        if manifest_path.stat().st_size > 5_000_000:
            raise ValueError("manifest_too_large")
        manifest = Manifest.model_validate_json(manifest_path.read_bytes())
        if manifest.format_version != 1 or manifest.parser_version not in {"p0.1", "p0.2"}:
            raise ValueError("unsupported_manifest_version")
        # Fully preflight before changing the destination DB.
        prepared = []
        for snapshot in manifest.snapshots:
            path = folder / "raw" / snapshot.sha256
            if path.is_symlink() or path.stat().st_size > self.settings.max_bytes:
                raise ValueError("unsafe_bundle_blob")
            blob = path.read_bytes()
            self.validate_snapshot(snapshot, blob)
            if not canonical_text(blob, snapshot.content_type, snapshot.encoding).strip():
                raise ValueError("empty_normalized_text")
            prepared.append(self.prepare(snapshot, blob))
        # Replaying a historical bundle is not a new source verification. Initial
        # captures retain their recorded observation; existing ones get no new one.
        with self.db.engine.begin() as conn:
            created = sum(
                int(self.persist(item, conn, record_observation=False).created) for item in prepared
            )
        return {"created": created, "duplicates": len(prepared) - created}
