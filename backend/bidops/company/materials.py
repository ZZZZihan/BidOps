import hashlib
import os
import re
import uuid
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath

from sqlalchemy import text

from bidops.company.authorization import ACTIONS, CompanyError, rows, transaction
from bidops.enterprise.auth import audit
from bidops.parser import canonical_text


def sha(data):
    return hashlib.sha256(data).hexdigest()


class HTMLBounds(HTMLParser):
    def __init__(self):
        super().__init__()
        self.nodes = self.depth = 0
        self.stack = []

    def handle_starttag(self, tag, attrs):
        self.nodes += 1
        if tag not in {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }:
            self.stack.append(tag)
            self.depth = len(self.stack)
        if self.nodes > 10_000 or self.depth > 64 or len(attrs) > 100:
            raise CompanyError("html_resource_limit", 413)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            raise CompanyError("html_structure_invalid", 415)
        self.stack.pop()
        self.depth = len(self.stack)

    def handle_startendtag(self, tag, attrs):
        self.nodes += 1
        if self.nodes > 10_000 or len(attrs) > 100:
            raise CompanyError("html_resource_limit", 413)


class MaterialStore:
    """Only bounded UTF-8 text/HTML. Archives, PDF, office files and OCR are rejected."""

    def __init__(self, db, data_dir, auth, *, max_bytes=2_000_000, max_text_chars=200_000):
        self.db, self.auth = db, auth
        self.data_dir = Path(data_dir).resolve()
        self.root = self.data_dir / "private" / "company"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self.root.resolve().is_relative_to(self.data_dir) or self.root.is_symlink():
            raise ValueError("unsafe_private_storage")
        self.max_bytes, self.max_text_chars = max_bytes, max_text_chars
        for folder in ("blob", "text"):
            directory = self.root / folder
            directory.mkdir(exist_ok=True, mode=0o700)
            if directory.is_symlink():
                raise ValueError("unsafe_private_storage")

    def path(self, relative):
        if self.root.is_symlink() or not self.root.resolve().is_relative_to(self.data_dir):
            raise CompanyError("unsafe_material_path", 409)
        name = PurePosixPath(relative)
        if (
            len(name.parts) != 2
            or name.parts[0] not in {"blob", "text"}
            or not re.fullmatch(r"[a-f0-9]{32}", name.parts[1])
        ):
            raise CompanyError("unsafe_material_path", 409)
        target = self.root / relative
        if target.is_symlink() or not target.resolve().is_relative_to(self.root.resolve()):
            raise CompanyError("unsafe_material_path", 409)
        return target

    def normalize(self, filename, content_type, data):
        if (
            not filename
            or len(filename) > 180
            or filename != filename.strip()
            or filename in {".", ".."}
            or any(c in filename for c in "/\\\x00\r\n")
        ):
            raise CompanyError("unsafe_filename", 422)
        if not 0 < len(data) <= self.max_bytes:
            raise CompanyError("material_size_limit", 413)
        media = content_type.split(";", 1)[0].strip().lower()
        extensions = {"text/plain": {".txt"}, "text/html": {".html", ".htm"}}
        if media not in extensions or Path(filename).suffix.lower() not in extensions[media]:
            raise CompanyError("unsupported_material_format", 415)
        if data.startswith((b"PK", b"%PDF", b"\x1f\x8b", b"\xd0\xcf\x11\xe0")):
            raise CompanyError("material_format_mismatch", 415)
        try:
            decoded = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise CompanyError("material_requires_utf8", 415) from None
        if any(ord(c) < 32 and c not in "\t\r\n" for c in decoded):
            raise CompanyError("binary_material_rejected", 415)
        if media == "text/html":
            bounds = HTMLBounds()
            bounds.feed(decoded)
            if not bounds.nodes:
                raise CompanyError("material_format_mismatch", 415)
        try:
            normalized = canonical_text(data, media, "utf-8-sig")
        except ValueError:
            raise CompanyError("invalid_material_text", 415) from None
        if not normalized or len(normalized) > self.max_text_chars:
            raise CompanyError("material_text_limit", 413)
        return media, normalized

    def put(
        self,
        context,
        *,
        filename,
        content_type,
        data,
        object_id=None,
        expected_version=None,
        retention_until=None,
        provenance_kind="engineering_fixture",
        source_attestation=None,
    ):
        existing = None
        if object_id is None:
            self.auth.require_creator(context)
            if expected_version is not None:
                raise CompanyError("new_object_cannot_have_expected_version", 422)
        else:
            existing = self.auth.require(context, object_id, "write")
        media, normalized = self.normalize(filename, content_type, data)
        if provenance_kind not in {"engineering_fixture", "authorized_real"}:
            raise CompanyError("invalid_material_provenance", 422)
        if provenance_kind == "authorized_real" and not source_attestation:
            raise CompanyError("real_material_requires_attestation", 422)
        if source_attestation is not None and not 0 < len(source_attestation) <= 200:
            raise CompanyError("invalid_material_attestation", 422)
        now = self.auth.clock()
        until = (
            retention_until.timestamp()
            if retention_until is not None
            else existing["retention_until"]
            if existing is not None
            else now + 30 * 86400
        )
        if until <= now:
            raise CompanyError("retention_must_be_future", 422)
        version = uuid.uuid4().hex
        created = object_id is None
        oid = object_id or uuid.uuid4().hex
        payload = {
            "id": oid,
            "version": version,
            "sha": sha(data),
            "text_sha": sha(normalized.encode()),
            "filename": filename,
            "media": media,
            "size": len(data),
            "blob": f"blob/{version}",
            "text": f"text/{version}",
            "provenance": provenance_kind,
            "attestation": source_attestation,
            "now": now,
            "owner": context.owner_id,
            "until": until,
        }
        written = []
        try:
            for relative, content in (
                (payload["blob"], data),
                (payload["text"], normalized.encode()),
            ):
                target = self.path(relative)
                descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                written.append(target)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
            with transaction(self.db) as conn:
                if created:
                    self.auth.require_creator(context, conn)
                    conn.execute(
                        text(
                            "INSERT INTO company_objects(object_id,kind,owner_id,"
                            "head_version,retention_until,created_at) "
                            "VALUES (:id,'material',:owner,:version,:until,:now)"
                        ),
                        payload,
                    )
                    for action in ACTIONS:
                        conn.execute(
                            text("INSERT INTO company_grants VALUES (:id,:owner,:action)"),
                            {**payload, "action": action},
                        )
                else:
                    obj = self.auth.require(context, oid, "write", conn)
                    if obj["kind"] != "material":
                        raise CompanyError("material_not_found")
                    if expected_version != obj["head_version"]:
                        raise CompanyError("version_conflict", 409)
                    conn.execute(
                        text(
                            "UPDATE company_objects SET head_version=:version,"
                            "retention_until=:until WHERE object_id=:id"
                        ),
                        payload,
                    )
                conn.execute(
                    text(
                        "INSERT INTO company_material_revisions VALUES "
                        "(:version,:id,:sha,:text_sha,:filename,:media,:size,:blob,:text,"
                        ":provenance,:attestation,:now,:owner)"
                    ),
                    payload,
                )
        except BaseException:
            for target in written:
                target.unlink(missing_ok=True)
            raise
        audit(
            self.db, "company_material_written", context.owner_id, object_id=oid, version_id=version
        )
        return {
            "object_id": oid,
            "version_id": version,
            "sha256": payload["sha"],
            "text_sha256": payload["text_sha"],
        }

    def revision(self, context, object_id, version_id=None):
        obj = self.auth.require(context, object_id, "read")
        if obj["kind"] != "material":
            raise CompanyError("material_not_found")
        found = rows(
            self.db,
            "SELECT * FROM company_material_revisions WHERE object_id=:id AND version_id=:version",
            {"id": object_id, "version": version_id or obj["head_version"]},
        )
        if not found:
            raise CompanyError("material_revision_not_found")
        return found[0]

    def metadata(self, context, object_id, version_id=None):
        row = self.revision(context, object_id, version_id)
        obj = self.auth.require(context, object_id, "read")
        return {
            **{key: value for key, value in row.items() if key not in {"blob_path", "text_path"}},
            "retention_until": obj["retention_until"],
        }

    def read(self, context, object_id, version_id=None):
        row = self.revision(context, object_id, version_id)
        raw, normalized = self.verify_revision(row)
        self.auth.require(context, object_id, "read")
        return row, raw, normalized

    def verify_revision(self, row):
        try:
            raw_path, text_path = self.path(row["blob_path"]), self.path(row["text_path"])
            if (
                raw_path.stat().st_size != row["byte_size"]
                or text_path.stat().st_size > 4 * self.max_text_chars
            ):
                raise CompanyError("material_integrity_failed", 409)
            raw = raw_path.read_bytes()
            content = text_path.read_bytes()
            if sha(raw) != row["sha256"] or sha(content) != row["text_sha256"]:
                raise CompanyError("material_integrity_failed", 409)
            normalized = content.decode("utf-8")
        except (OSError, UnicodeError):
            raise CompanyError("material_source_unavailable", 409) from None
        return raw, normalized

    def citation(self, context, object_id, version_id, start, end):
        row, _, normalized = self.read(context, object_id, version_id)
        if not 0 <= start < end <= len(normalized):
            raise CompanyError("invalid_citation_span", 422)
        return {
            "evidence_id": f"{version_id}:{start}:{end}",
            "side": "company",
            "object_kind": "company_material",
            "version": {"object_id": object_id, "version_id": version_id, "sha256": row["sha256"]},
            "text_sha256": row["text_sha256"],
            "quote": normalized[start:end],
            "start": start,
            "end": end,
            "locator": "normalized_text_v2",
        }

    def search(self, context, query, *, limit=50, offset=0):
        if not query.strip() or len(query) > 200 or not 1 <= limit <= 100 or offset < 0:
            raise CompanyError("invalid_search", 422)
        matches = []
        position = 0
        while True:
            page = self.auth.visible(context, "material", limit=100, offset=position)
            for obj in page["items"]:
                row, _, normalized = self.read(context, obj["object_id"])
                index = normalized.casefold().find(query.casefold())
                if index >= 0:
                    matches.append(
                        {
                            "object_id": obj["object_id"],
                            "version_id": row["version_id"],
                            "filename": row["filename"],
                            "snippet": normalized[max(0, index - 40) : index + len(query) + 80],
                        }
                    )
            if not page["has_more"]:
                break
            position += 100
        self.auth.require_many(context, [item["object_id"] for item in matches])
        return {
            "items": matches[offset : offset + limit],
            "total": len(matches),
            "limit": limit,
            "offset": offset,
            "has_more": offset + limit < len(matches),
        }

    def backup_files(self):
        """Privileged offline backup enumeration, not an HTTP content endpoint."""
        result = []
        for row in self.db.rows("SELECT * FROM company_material_revisions ORDER BY version_id"):
            self.verify_revision(row)
            for key, hash_key in (("blob_path", "sha256"), ("text_path", "text_sha256")):
                result.append(
                    {
                        "path": self.path(row[key]).relative_to(self.data_dir).as_posix(),
                        "sha256": row[hash_key],
                    }
                )
        return result
