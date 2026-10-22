"""Append-only local notice history. No acquisition or model calls occur here."""

import hashlib
import json
import math
import os
import re
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy import text

from bidops.models import Evidence, Notice, Snapshot
from bidops.parser import canonical_text
from bidops.screening.schemas import EvidenceRef, VersionRef

from .schemas import AttachmentInput, AttachmentRecord, NoticeVersion, PackageVersion, ProjectStatus

NORMALIZER = "normalized_text_v2"
SUPPORTED_ATTACHMENT_TYPES = ("text/plain", "text/html")


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value: bytes | str) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def _id(namespace: str, *parts) -> str:
    return _sha(_json([namespace, *parts]))


def _at(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("notice_timezone_required")
    return value.astimezone(UTC)


def _norm(value: str) -> str:
    return " ".join(value.split()).casefold()


def _package_label(value: str) -> str:
    match = re.fullmatch(r"(?:第|采购包|包)?\s*([0-9零〇一二三四五六七八九十]+)\s*包?", value)
    if not match:
        return _norm(value)
    number = match[1]
    if not number.isascii():
        digits = {c: n for n, c in enumerate("零一二三四五六七八九")}
        digits["〇"] = 0
        if "十" in number:
            left, right = number.split("十")
            if len(left) > 1 or len(right) > 1:
                return _norm(value)
            number = str((digits[left] if left else 1) * 10 + (digits[right] if right else 0))
        else:
            number = "".join(str(digits[c]) for c in number)
    return f"package:{int(number)}"


class NoticeRepository:
    def __init__(self, db, raw: Path, *, attachment_raw: Path | None = None, max_bytes=10_000_000):
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
            raise ValueError("notice_invalid_size_limit")
        self.db = db
        self.raw = Path(raw)
        self.attachment_raw = (
            Path(attachment_raw) if attachment_raw else self.raw.parent / "attachments"
        )
        self.max_bytes = max_bytes
        self.attachment_raw.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.raw.is_symlink() or self.attachment_raw.is_symlink():
            raise ValueError("notice_blob_directory_symlink")

    @contextmanager
    def _connection(self, conn=None):
        if conn is not None:
            yield conn
        else:
            with self.db.engine.begin() as own:
                yield own

    def _read_blob(self, folder: Path, sha256: str) -> bytes:
        if not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise ValueError("notice_invalid_blob_hash")
        path = folder / sha256
        try:
            if folder.is_symlink() or path.is_symlink():
                raise ValueError("notice_blob_symlink")
            if not 0 < path.stat().st_size <= self.max_bytes:
                raise ValueError("notice_blob_size_mismatch")
            data = path.read_bytes()
        except OSError:
            raise ValueError("notice_blob_unavailable") from None
        if _sha(data) != sha256:
            raise ValueError("notice_blob_hash_mismatch")
        return data

    def _write_blob(self, data: bytes) -> str:
        if not isinstance(data, bytes) or not 0 < len(data) <= self.max_bytes:
            raise ValueError("attachment_blob_size_mismatch")
        sha = _sha(data)
        path = self.attachment_raw / sha
        if self.attachment_raw.is_symlink():
            raise ValueError("notice_blob_directory_symlink")
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            self._read_blob(self.attachment_raw, sha)
        else:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        return sha

    @staticmethod
    def _validate_spans(notice: Notice, normalized: str):
        spans = [s for group in notice.field_evidence.values() for s in group]
        spans += notice.eligibility_conditions + [p.evidence for p in notice.packages]
        for original in spans:
            span = Evidence.model_validate(original.model_dump())
            if span.locator != NORMALIZER:
                raise ValueError("notice_unsupported_legacy_locator")
            if span.source_snapshot_id != notice.notice_id:
                raise ValueError("notice_evidence_snapshot_mismatch")
            if not 0 <= span.start < span.end <= len(normalized):
                raise ValueError("notice_evidence_out_of_bounds")
            if normalized[span.start : span.end] != span.text:
                raise ValueError("notice_evidence_quote_mismatch")

    @staticmethod
    def _version_identity(snapshot, notice, normalized):
        payload_json = _json(notice.model_dump(mode="json"))
        payload_sha = _sha(payload_json)
        text_sha = _sha(normalized)
        version_id = _id(
            "notice_version_v1",
            snapshot.snapshot_id,
            snapshot.sha256,
            text_sha,
            NORMALIZER,
            notice.parser_version,
            payload_sha,
        )
        return version_id, text_sha, payload_sha, payload_json

    def capture(
        self,
        snapshot: Snapshot,
        notice: Notice,
        normalized: str,
        *,
        observed_at=None,
        legacy_payload_json=None,
        source_complete=False,
        interpreted_at=None,
        record_observation=True,
        conn=None,
    ) -> NoticeVersion:
        """Call inside Store's transaction, after snapshot and legacy notice inserts.

        source_complete is an explicit caller attestation, never inferred from content.
        The import layer currently defaults it to False. Captures do not update history.
        """
        try:
            snapshot = Snapshot.model_validate(snapshot.model_dump())
            notice = Notice.model_validate(notice.model_dump())
        except (AttributeError, ValidationError):
            raise ValueError("notice_invalid_capture") from None
        if type(source_complete) is not bool or type(record_observation) is not bool:
            raise ValueError("notice_invalid_completeness")
        observed_at = _at(observed_at if observed_at is not None else snapshot.fetched_at)
        if (
            notice.notice_id != snapshot.snapshot_id
            or notice.source.snapshot_id != snapshot.snapshot_id
        ):
            raise ValueError("notice_snapshot_mismatch")
        if (
            notice.source.sha256 != snapshot.sha256
            or notice.source.source_url != snapshot.source_url
        ):
            raise ValueError("notice_snapshot_mismatch")
        data = self._read_blob(self.raw, snapshot.sha256)
        if snapshot.byte_size != len(data) or snapshot.snapshot_id != _sha(
            snapshot.source_url + "\n" + snapshot.sha256
        ):
            raise ValueError("notice_snapshot_identity_mismatch")
        try:
            recomputed = canonical_text(data, snapshot.content_type, snapshot.encoding)
        except (ValueError, UnicodeError):
            raise ValueError("notice_normalization_failed") from None
        if recomputed != normalized:
            raise ValueError("notice_normalized_text_mismatch")
        self._validate_spans(notice, normalized)
        with self._connection(conn) as connection:
            stored = connection.execute(
                text("SELECT metadata_json FROM snapshots WHERE snapshot_id=:id"),
                {"id": snapshot.snapshot_id},
            ).scalar_one_or_none()
            if stored is None:
                raise ValueError("notice_snapshot_not_persisted")
            canonical = Snapshot.model_validate_json(stored)
            if (
                canonical.source_url,
                canonical.sha256,
                canonical.byte_size,
                canonical.content_type,
                canonical.encoding,
            ) != (
                snapshot.source_url,
                snapshot.sha256,
                snapshot.byte_size,
                snapshot.content_type,
                snapshot.encoding,
            ):
                raise ValueError("notice_snapshot_mismatch")
            # Re-observation may update fetched_at in a caller's temporary Snapshot.
            # It must not manufacture a new interpretation of the same source bytes.
            notice.source = canonical
            notice.observed_at = canonical.fetched_at.isoformat()
            version_id, text_sha, payload_sha, payload_json = self._version_identity(
                canonical, notice, normalized
            )
            has_interpretation = connection.execute(
                text("SELECT 1 FROM nd_versions WHERE snapshot_id=:id LIMIT 1"),
                {"id": canonical.snapshot_id},
            ).first()
            available_at = (
                _at(interpreted_at)
                if interpreted_at is not None
                else (datetime.now(UTC) if has_interpretation else observed_at)
            )
            if available_at < _at(canonical.fetched_at):
                raise ValueError("notice_interpretation_before_capture")
            if observed_at < _at(canonical.fetched_at):
                raise ValueError("notice_observation_before_capture")
            if legacy_payload_json is None:
                legacy_payload_json = connection.execute(
                    text("SELECT payload_json FROM notices WHERE snapshot_id=:id"),
                    {"id": snapshot.snapshot_id},
                ).scalar_one_or_none()
            if not isinstance(legacy_payload_json, str):
                raise ValueError("notice_legacy_payload_missing")
            try:
                legacy = Notice.model_validate_json(legacy_payload_json)
            except ValidationError:
                raise ValueError("notice_legacy_payload_invalid") from None
            if legacy.notice_id != snapshot.snapshot_id or legacy.source.sha256 != snapshot.sha256:
                raise ValueError("notice_legacy_payload_mismatch")
            logical_id = _id("logical_notice_v1", canonical.source_url)
            connection.execute(
                text("INSERT OR IGNORE INTO nd_logical_notices VALUES (:id,:url)"),
                {"id": logical_id, "url": canonical.source_url},
            )
            project_id = None
            # Neither title similarity nor a project code without its buyer is enough.
            if (
                notice.project_code
                and notice.buyer_name
                and all(
                    any(
                        _norm(getattr(notice, key)) in _norm(span.text)
                        for span in notice.field_evidence.get(key, [])
                    )
                    for key in ("project_code", "buyer_name")
                )
            ):
                authority = (urlsplit(canonical.source_url).hostname or "").lower()
                code, buyer = _norm(notice.project_code), _norm(notice.buyer_name)
                project_id = _id("project_v1", authority, code, buyer)
                connection.execute(
                    text("INSERT OR IGNORE INTO nd_projects VALUES (:id,:authority,:code,:buyer)"),
                    {"id": project_id, "authority": authority, "code": code, "buyer": buyer},
                )
            params = {
                "version_id": version_id,
                "snapshot_id": canonical.snapshot_id,
                "logical_notice_id": logical_id,
                "project_id": project_id,
                "association_state": "matched" if project_id else "pending_review",
                "parser_version": notice.parser_version,
                "normalizer_version": NORMALIZER,
                "available_at": available_at.timestamp(),
                "raw_sha256": canonical.sha256,
                "text_sha256": text_sha,
                "normalized_text": normalized,
                "payload_sha256": payload_sha,
                "payload_json": payload_json,
                "legacy_payload_json": legacy_payload_json,
            }
            connection.execute(
                text(
                    "INSERT OR IGNORE INTO nd_versions ("
                    + ",".join(params)
                    + ") VALUES ("
                    + ",".join(":" + name for name in params)
                    + ")"
                ),
                params,
            )
            labels = set()
            for package in notice.packages:
                label = _package_label(package.label)
                if label in labels:
                    raise ValueError("notice_duplicate_package_label")
                labels.add(label)
                connection.execute(
                    text(
                        "INSERT OR IGNORE INTO nd_package_versions "
                        "VALUES (:id,:version,:label,:norm,:s,:e)"
                    ),
                    {
                        "id": _id("package_v1", project_id or logical_id, label),
                        "version": version_id,
                        "label": package.label,
                        "norm": label,
                        "s": package.evidence.start,
                        "e": package.evidence.end,
                    },
                )
            obs = {
                "id": canonical.snapshot_id,
                "at": observed_at.timestamp(),
                "complete": int(source_complete),
            }
            previous = connection.execute(
                text(
                    "SELECT source_complete FROM nd_observations "
                    "WHERE snapshot_id=:id AND observed_at=:at"
                ),
                obs,
            ).scalar_one_or_none()
            if record_observation:
                if previous is not None and previous != int(source_complete):
                    raise ValueError("notice_observation_conflict")
                connection.execute(
                    text("INSERT OR IGNORE INTO nd_observations VALUES (:id,:at,:complete)"), obs
                )
            elif not connection.execute(
                text("SELECT 1 FROM nd_observations WHERE snapshot_id=:id LIMIT 1"),
                {"id": canonical.snapshot_id},
            ).first():
                raise ValueError("notice_observation_missing")
            return self._get_version(connection, version_id)

    def backfill_legacy(self) -> dict[str, int]:
        """Verify all missing rows before committing an idempotent legacy backfill."""
        with self.db.engine.begin() as conn:
            existing = conn.execute(
                text("SELECT COUNT(DISTINCT snapshot_id) FROM nd_versions")
            ).scalar_one()
            rows = (
                conn.execute(
                    text(
                        "SELECT s.metadata_json,n.payload_json FROM snapshots s JOIN notices n "
                        "ON n.snapshot_id=s.snapshot_id WHERE NOT EXISTS "
                        "(SELECT 1 FROM nd_versions v WHERE v.snapshot_id=s.snapshot_id) "
                        "ORDER BY s.snapshot_id"
                    )
                )
                .mappings()
                .all()
            )
            for row in rows:
                try:
                    snapshot = Snapshot.model_validate_json(row["metadata_json"])
                    notice = Notice.model_validate_json(row["payload_json"])
                    data = self._read_blob(self.raw, snapshot.sha256)
                    normalized = canonical_text(data, snapshot.content_type, snapshot.encoding)
                except (ValidationError, UnicodeError):
                    raise ValueError("notice_legacy_backfill_invalid") from None
                self.capture(
                    snapshot, notice, normalized, legacy_payload_json=row["payload_json"], conn=conn
                )
            return {"created": len(rows), "existing": existing}

    def _get_version(self, conn, version_id: str, *, as_of=None) -> NoticeVersion:
        row = (
            conn.execute(text("SELECT * FROM nd_versions WHERE version_id=:id"), {"id": version_id})
            .mappings()
            .first()
        )
        if row is None:
            raise ValueError("notice_version_not_found")
        params = {"id": row["snapshot_id"]}
        clause = ""
        if as_of is not None:
            params["at"] = _at(as_of).timestamp()
            clause = " AND observed_at<=:at"
        observations = (
            conn.execute(
                text(
                    "SELECT MIN(observed_at) AS first,MAX(observed_at) AS last "
                    "FROM nd_observations "
                    "WHERE snapshot_id=:id" + clause
                ),
                params,
            )
            .mappings()
            .one()
        )
        if observations["first"] is None:
            raise ValueError("notice_version_not_observed")
        packages = (
            conn.execute(
                text(
                    "SELECT package_id,label,normalized_label,evidence_start,evidence_end "
                    "FROM nd_package_versions WHERE version_id=:id ORDER BY package_id"
                ),
                {"id": version_id},
            )
            .mappings()
            .all()
        )
        return NoticeVersion(
            **{
                name: row[name]
                for name in (
                    "version_id",
                    "snapshot_id",
                    "logical_notice_id",
                    "project_id",
                    "association_state",
                    "parser_version",
                    "normalizer_version",
                    "raw_sha256",
                    "text_sha256",
                    "payload_sha256",
                )
            },
            notice=Notice.model_validate_json(row["payload_json"]),
            available_at=datetime.fromtimestamp(row["available_at"], UTC),
            packages=[PackageVersion(**p) for p in packages],
            first_observed_at=datetime.fromtimestamp(observations["first"], UTC),
            last_observed_at=datetime.fromtimestamp(observations["last"], UTC),
        )

    def get_version(self, version_id: str) -> NoticeVersion:
        with self.db.engine.connect() as conn:
            return self._get_version(conn, version_id)

    def versions_for_notice(self, snapshot_id: str) -> list[NoticeVersion]:
        """Accept a legacy snapshot/notice ID, returning its whole logical URL history."""
        with self.db.engine.connect() as conn:
            ids = (
                conn.execute(
                    text(
                        "SELECT version_id FROM nd_versions WHERE logical_notice_id IN "
                        "(SELECT logical_notice_id FROM nd_versions WHERE snapshot_id=:id) "
                        "ORDER BY version_id"
                    ),
                    {"id": snapshot_id},
                )
                .scalars()
                .all()
            )
            values = [self._get_version(conn, value) for value in ids]
        return sorted(values, key=lambda v: (v.first_observed_at, v.version_id))

    def project_versions(self, project_id: str, as_of: datetime) -> list[NoticeVersion]:
        at = _at(as_of)
        with self.db.engine.connect() as conn:
            ids = (
                conn.execute(
                    text(
                        "SELECT version_id FROM nd_versions v "
                        "WHERE project_id=:id AND available_at<=:at AND EXISTS "
                        "(SELECT 1 FROM nd_observations o WHERE o.snapshot_id=v.snapshot_id "
                        "AND observed_at<=:at)"
                    ),
                    {"id": project_id, "at": at.timestamp()},
                )
                .scalars()
                .all()
            )
            values = [self._get_version(conn, value, as_of=at) for value in ids]
        return sorted(values, key=lambda v: (v.available_at, v.version_id))

    def _verified_notice_text(self, version_id: str):
        rows = self.db.rows("SELECT * FROM nd_versions WHERE version_id=:id", {"id": version_id})
        if not rows:
            raise ValueError("notice_version_not_found")
        row = rows[0]
        try:
            notice = Notice.model_validate_json(row["payload_json"])
            data = self._read_blob(self.raw, row["raw_sha256"])
            normalized = canonical_text(data, notice.source.content_type, notice.source.encoding)
            identity, text_sha, payload_sha, payload = self._version_identity(
                notice.source, notice, normalized
            )
        except (ValidationError, UnicodeError):
            raise ValueError("notice_version_integrity_mismatch") from None
        if (
            row["normalizer_version"] != NORMALIZER
            or identity != version_id
            or row["normalized_text"] != normalized
            or row["text_sha256"] != text_sha
            or row["payload_sha256"] != payload_sha
            or row["payload_json"] != payload
            or row["snapshot_id"] != notice.source.snapshot_id
            or row["raw_sha256"] != notice.source.sha256
            or row["parser_version"] != notice.parser_version
            or row["logical_notice_id"] != _id("logical_notice_v1", notice.source.source_url)
            or notice.source.snapshot_id
            != _sha(notice.source.source_url + "\n" + notice.source.sha256)
            or len(data) != notice.source.byte_size
        ):
            raise ValueError("notice_version_integrity_mismatch")
        ref = VersionRef(
            object_id=row["logical_notice_id"], version_id=version_id, sha256=row["raw_sha256"]
        )
        return ref, normalized

    def _verify_attachment_content(self, record: AttachmentRecord):
        if record.raw_sha256 is None:
            raise ValueError("attachment_blob_not_acquired")
        data = self._read_blob(self.attachment_raw, record.raw_sha256)
        if len(data) != record.byte_size:
            raise ValueError("attachment_blob_size_mismatch")
        if record.parse_status != "parsed":
            return None
        try:
            normalized = canonical_text(data, record.content_type, record.encoding)
        except (ValueError, UnicodeError):
            raise ValueError("attachment_text_integrity_mismatch") from None
        rows = self.db.rows(
            "SELECT normalized_text FROM nd_attachments WHERE record_id=:id",
            {"id": record.record_id},
        )
        if (
            len(data) != record.byte_size
            or record.normalizer_version != NORMALIZER
            or _sha(normalized) != record.text_sha256
            or rows[0]["normalized_text"] != normalized
        ):
            raise ValueError("attachment_text_integrity_mismatch")
        return normalized

    def _verified_attachment_text(self, record_id: str):
        record = self.get_attachment(record_id)
        if record.parse_status != "parsed" or record.version_ref is None:
            raise ValueError("attachment_text_unavailable")
        return record.version_ref, self._verify_attachment_content(record)

    def make_evidence(
        self, version_id: str, start: int, end: int, *, object_kind="notice"
    ) -> EvidenceRef:
        if object_kind == "notice":
            version, normalized = self._verified_notice_text(version_id)
        elif object_kind == "attachment":
            version, normalized = self._verified_attachment_text(version_id)
        else:
            raise ValueError("notice_evidence_kind_unsupported")
        if (
            type(start) is not int
            or type(end) is not int
            or not 0 <= start < end <= len(normalized)
            or not normalized[start:end].strip()
        ):
            raise ValueError("notice_evidence_out_of_bounds")
        text_sha = _sha(normalized)
        return EvidenceRef(
            evidence_id=_id("notice_evidence_v1", version.key(), text_sha, start, end),
            side="notice",
            object_kind=object_kind,
            version=version,
            text_sha256=text_sha,
            quote=normalized[start:end],
            start=start,
            end=end,
        )

    def resolve_evidence(self, evidence: EvidenceRef | dict) -> EvidenceRef:
        try:
            reference = EvidenceRef.model_validate(evidence)
        except ValidationError:
            raise ValueError("notice_evidence_invalid") from None
        if reference.side != "notice" or reference.locator != NORMALIZER:
            raise ValueError("notice_evidence_kind_unsupported")
        expected = self.make_evidence(
            reference.version.version_id,
            reference.start,
            reference.end,
            object_kind=reference.object_kind,
        )
        if reference.model_dump() != expected.model_dump():
            raise ValueError("notice_evidence_integrity_mismatch")
        return expected

    def record_attachment(
        self, value: AttachmentInput, data: bytes | None = None, *, conn=None
    ) -> AttachmentRecord:
        try:
            value = AttachmentInput.model_validate(value)
        except ValidationError:
            raise ValueError("attachment_input_invalid") from None
        if (value.acquisition_status == "downloaded") != (data is not None):
            raise ValueError("attachment_acquisition_bytes_mismatch")
        sha = None
        normalized = None
        if data is not None:
            if not isinstance(data, bytes) or not 0 < len(data) <= self.max_bytes:
                raise ValueError("attachment_blob_size_mismatch")
            sha = _sha(data)
            if value.declared_sha256 is not None and value.declared_sha256 != sha:
                raise ValueError("attachment_declared_hash_mismatch")
            if value.parse_status == "parsed":
                try:
                    normalized = canonical_text(data, value.content_type, value.encoding)
                except (ValueError, UnicodeError):
                    raise ValueError("attachment_parse_failed") from None
                if not normalized.strip():
                    raise ValueError("attachment_text_empty")
        with self._connection(conn) as connection:
            parent = self._get_version(connection, value.parent_version_id)
            attachment_id = _id("attachment_v1", parent.logical_notice_id, value.attachment_key)
            metadata = value.model_dump(mode="json")
            metadata["observed_at"] = _at(value.observed_at).isoformat()
            text_sha = _sha(normalized) if normalized is not None else None
            record_id = _id("attachment_record_v1", attachment_id, metadata, sha, text_sha)
            record = AttachmentRecord(
                **metadata,
                record_id=record_id,
                attachment_id=attachment_id,
                raw_sha256=sha,
                text_sha256=text_sha,
                byte_size=len(data) if data else 0,
                normalizer_version=NORMALIZER if normalized is not None else None,
                integrity_status="verified" if data is not None else "not_checked",
            )
            conflicts = connection.execute(
                text(
                    "SELECT record_id FROM nd_attachments WHERE attachment_id=:id "
                    "AND parent_version_id=:parent AND observed_at=:at AND record_id<>:record"
                ),
                {
                    "id": attachment_id,
                    "parent": value.parent_version_id,
                    "at": _at(value.observed_at).timestamp(),
                    "record": record_id,
                },
            ).first()
            if conflicts:
                raise ValueError("attachment_observation_conflict")
            if data is not None:
                self._write_blob(data)
            connection.execute(
                text(
                    "INSERT OR IGNORE INTO nd_attachments VALUES "
                    "(:id,:attachment,:parent,:at,:acquisition,:parse,:raw,:text,:normalized,:metadata)"
                ),
                {
                    "id": record_id,
                    "attachment": attachment_id,
                    "parent": value.parent_version_id,
                    "at": _at(value.observed_at).timestamp(),
                    "acquisition": value.acquisition_status,
                    "parse": value.parse_status,
                    "raw": sha,
                    "text": text_sha,
                    "normalized": normalized,
                    "metadata": _json(
                        record.model_dump(
                            mode="json",
                            exclude={
                                "integrity_status",
                                "integrity_error",
                            },
                        )
                    ),
                },
            )
            return record

    def get_attachment(self, record_id: str) -> AttachmentRecord:
        rows = self.db.rows("SELECT * FROM nd_attachments WHERE record_id=:id", {"id": record_id})
        if not rows:
            raise ValueError("attachment_not_found")
        row = rows[0]
        try:
            record = AttachmentRecord.model_validate_json(row["metadata_json"])
        except ValidationError:
            raise ValueError("attachment_metadata_integrity_mismatch") from None
        metadata = {
            k: v
            for k, v in record.model_dump(mode="json").items()
            if k in AttachmentInput.model_fields
        }
        metadata["observed_at"] = _at(record.observed_at).isoformat()
        if (
            record.record_id != record_id
            or record_id
            != _id(
                "attachment_record_v1",
                record.attachment_id,
                metadata,
                record.raw_sha256,
                record.text_sha256,
            )
            or any(
                row[k] != getattr(record, k)
                for k in (
                    "attachment_id",
                    "parent_version_id",
                    "acquisition_status",
                    "parse_status",
                    "raw_sha256",
                    "text_sha256",
                )
            )
            or row["observed_at"] != _at(record.observed_at).timestamp()
        ):
            raise ValueError("attachment_metadata_integrity_mismatch")
        if record.raw_sha256 is not None:
            try:
                self._verify_attachment_content(record)
            except ValueError as error:
                record.integrity_status = (
                    "unavailable" if str(error) == "notice_blob_unavailable" else "corrupt"
                )
                record.integrity_error = str(error)
            else:
                record.integrity_status = "verified"
        return record

    def list_attachments(
        self, parent_version_id: str, as_of: datetime | None = None
    ) -> list[AttachmentRecord]:
        params = {"id": parent_version_id}
        clause = ""
        if as_of is not None:
            params["at"] = _at(as_of).timestamp()
            clause = " AND observed_at<=:at"
        rows = self.db.rows(
            "SELECT record_id FROM nd_attachments WHERE parent_version_id=:id"
            + clause
            + " ORDER BY observed_at,record_id",
            params,
        )
        return [self.get_attachment(row["record_id"]) for row in rows]

    def missing_attachments(
        self, parent_version_id: str, as_of: datetime | None = None
    ) -> list[AttachmentRecord]:
        latest = {}
        for record in self.list_attachments(parent_version_id, as_of):
            latest[record.attachment_id] = record
        return [
            latest[key]
            for key in sorted(latest)
            if latest[key].parse_status != "parsed" or latest[key].integrity_status != "verified"
        ]

    def blob_hashes(self) -> list[str]:
        return [
            row["raw_sha256"]
            for row in self.db.rows(
                "SELECT DISTINCT raw_sha256 FROM nd_attachments "
                "WHERE raw_sha256 IS NOT NULL ORDER BY raw_sha256"
            )
        ]

    def project_status(
        self, project_id: str, as_of: datetime, max_age_seconds=86400
    ) -> ProjectStatus:
        at = _at(as_of)
        if (
            isinstance(max_age_seconds, bool)
            or not isinstance(max_age_seconds, int | float)
            or not math.isfinite(max_age_seconds)
            or max_age_seconds <= 0
        ):
            raise ValueError("notice_invalid_freshness_limit")
        versions = self.project_versions(project_id, at)
        result = ProjectStatus(
            project_id=project_id, as_of=at, status="unknown", reason="not_observed"
        )
        if not versions:
            return result
        result.version_ids = [v.version_id for v in versions]
        current = {}
        for version in versions:
            prior = current.get(version.logical_notice_id)
            if prior is None or (
                version.first_observed_at,
                version.available_at,
                version.version_id,
            ) > (prior.first_observed_at, prior.available_at, prior.version_id):
                current[version.logical_notice_id] = version
        result.source_observations = {v.version_id: v.last_observed_at for v in current.values()}
        # Every current linked source is needed. Refreshing an old original page
        # cannot refresh the separate correction page used by this decision.
        result.observed_at = min(result.source_observations.values())
        result.age_seconds = (at - result.observed_at).total_seconds()
        result.is_fresh = result.age_seconds <= max_age_seconds
        result.missing_attachment_ids = sorted(
            {r.record_id for v in versions for r in self.missing_attachments(v.version_id, at)}
        )
        for version in versions:
            try:
                self._verified_notice_text(version.version_id)
            except ValueError as error:
                result.integrity_issues[version.version_id] = str(error)
        if result.integrity_issues:
            result.reason = "notice_evidence_unavailable"
            return result
        # Knowledge of termination is monotone, including after stale/incomplete refreshes.
        terminal = [v for v in versions if v.notice.notice_kind == "termination"]
        if terminal:
            basis = max(terminal, key=lambda v: (v.first_observed_at, v.version_id))
            result.status, result.reason, result.basis_version_id = (
                "terminated",
                "known_termination",
                basis.version_id,
            )
            return result
        awards = [v for v in versions if v.notice.notice_kind == "award"]
        if awards:
            result.status, result.reason, result.basis_version_id = (
                "closed",
                "known_award",
                awards[-1].version_id,
            )
            return result
        if not result.is_fresh:
            result.reason = "stale_observation"
            return result
        # Completeness of one fresh page cannot silently attest the other linked pages.
        completeness = [
            self.db.rows(
                "SELECT source_complete FROM nd_observations WHERE snapshot_id=:id "
                "AND observed_at<=:at ORDER BY observed_at DESC LIMIT 1",
                {"id": version.snapshot_id, "at": at.timestamp()},
            )[0]["source_complete"]
            for version in current.values()
        ]
        result.source_complete = all(completeness)
        if not result.source_complete:
            result.reason = "source_incomplete"
            return result
        projects = self.db.rows(
            "SELECT * FROM nd_projects WHERE project_id=:id", {"id": project_id}
        )
        project = projects[0]
        pending = self.db.rows(
            "SELECT v.payload_json,l.source_url FROM nd_versions v JOIN nd_logical_notices l "
            "ON l.logical_notice_id=v.logical_notice_id WHERE v.project_id IS NULL AND EXISTS "
            "(SELECT 1 FROM nd_observations o WHERE o.snapshot_id=v.snapshot_id "
            "AND o.observed_at<=:at) "
            "AND v.available_at<=:at",
            {"at": at.timestamp()},
        )
        if any(
            (urlsplit(row["source_url"]).hostname or "").lower() == project["authority"]
            and (n := Notice.model_validate_json(row["payload_json"])).project_code
            and _norm(n.project_code) == project["project_code"]
            for row in pending
        ):
            result.reason = "pending_association"
            return result
        originals = {
            v.logical_notice_id for v in versions if v.notice.notice_kind == "tender_announcement"
        }
        if len(originals) != 1:
            result.reason = "missing_or_ambiguous_original"
            return result
        # Different parser interpretations of the same bytes cannot silently pick a winner.
        interpretations = {}
        for v in versions:
            key = (v.notice.notice_kind, v.notice.bid_deadline_at, v.notice.document_deadline_at)
            interpretations.setdefault(v.snapshot_id, set()).add(key)
        if any(len(keys) > 1 for keys in interpretations.values()):
            result.reason = "interpretation_conflict"
            return result
        logicals = {v.logical_notice_id for v in versions}
        if len(logicals) > 1 and any(
            not (v.notice.published_at or v.notice.published_date) for v in versions
        ):
            result.reason = "revision_order_unknown"
            return result

        publication = {}
        try:
            for v in versions:
                if v.notice.published_at:
                    start = _at(datetime.fromisoformat(v.notice.published_at))
                    end = start
                elif v.notice.published_date:
                    start = (
                        datetime.fromisoformat(v.notice.published_date)
                        .replace(tzinfo=ZoneInfo("Asia/Shanghai"))
                        .astimezone(UTC)
                    )
                    # A date-only publication does not determine order within that day.
                    end = start + timedelta(days=1)
                else:
                    start = end = v.first_observed_at
                publication[v.version_id] = (start, end)
        except ValueError:
            result.reason = "publication_time_unknown"
            return result
        latest = max(
            versions,
            key=lambda v: (publication[v.version_id][0], v.first_observed_at, v.version_id),
        )
        if len(logicals) > 1 and any(
            v.logical_notice_id != latest.logical_notice_id
            and publication[v.version_id][1] >= publication[latest.version_id][0]
            for v in versions
        ):
            result.reason = "revision_order_unknown"
            return result
        result.basis_version_id = latest.version_id
        if result.missing_attachment_ids:
            result.reason = "attachments_incomplete"
            return result
        if publication[latest.version_id][0] > at:
            result.reason = "publication_not_effective"
            return result
        if latest.notice.notice_kind not in {"tender_announcement", "correction"}:
            result.reason = "notice_kind_unknown"
            return result
        try:
            deadline = _at(datetime.fromisoformat(latest.notice.bid_deadline_at or ""))
        except ValueError:
            result.reason = "deadline_unknown"
            return result
        # This result needs explicit complete-source evidence and fresh observations;
        # the normal notice-only importer cannot obtain it merely from a future date.
        result.status = "open" if deadline > at else "closed"
        result.reason = "complete_fresh_source" if deadline > at else "known_deadline_passed"
        return result
